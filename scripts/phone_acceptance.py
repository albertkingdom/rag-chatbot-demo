"""Isolated same-origin phone acceptance server with fake chat/jobs and volatile storage."""
import asyncio
import os
import sys
import tempfile
import time
import types
import uuid

sys.path.insert(0, '/app')
os.environ.update(AUTH_ENABLED='true', APP_API_KEY='mobile-test-2026', LANGCHAIN_TRACING_V2='false', LANGSMITH_TRACING='false')

class MemoryRedis:
    def __init__(self): self.values={}; self.deadlines={}
    def get(self,key):
        if self.deadlines.get(key,float('inf')) < time.monotonic(): self.delete(key)
        return self.values.get(key)
    def setex(self,key,ttl,value): self.values[key]=value; self.deadlines[key]=time.monotonic()+ttl; return True
    def delete(self,key): self.values.pop(key,None); self.deadlines.pop(key,None); return 1
    def pipeline(self):
        redis=self
        class Pipeline:
            def __init__(self): self.key=''
            def incr(self,key): self.key=key; return self
            def expire(self,key,ttl): return self
            def execute(self):
                count=int(redis.get(self.key) or 0)+1
                redis.setex(self.key,60,count)
                return [count, True]
        return Pipeline()

redis=MemoryRedis()
from src import services
services.get_redis_conn=lambda:redis
from src.chat_history_service import ChatHistoryService
from src.chat_events import DeltaEvent, DoneEvent, MetadataEvent, SourcesEvent, SourceItem, StatusEvent

fake=types.ModuleType('src.chat_service')
async def chat_event_stream(message, *, history_key, session_id):
    yield StatusEvent(stage='generating',message='正在產生驗收用模擬回答…')
    answer='這是實機驗收用的模擬回答，不會呼叫正式模型。\n\n'+('請確認鍵盤開啟時仍能送出、停止，並可閱讀與捲動訊息。\n\n'*16)
    for start in range(0,len(answer),18):
        yield DeltaEvent(text=answer[start:start+18])
        await asyncio.sleep(0.12)
    ChatHistoryService(redis).append_turn(history_key,message,answer)
    yield SourcesEvent(items=[SourceItem(label='手機驗收模擬手冊')])
    yield MetadataEvent(elapsed_ms=4000,response_source='rag',cache_hit=False)
    yield DoneEvent()
fake.chat_event_stream=chat_event_stream
sys.modules['src.chat_service']=fake

from src.app import app
from src.api import routes
from src import manual_service
uploads=tempfile.TemporaryDirectory(prefix='phone-acceptance-')
manual_service.DATA_SOURCE_DIR=uploads.name
jobs={}
def enqueue():
    job=uuid.uuid4().hex
    jobs[job]=time.monotonic()
    return job
def status(job):
    if job not in jobs: raise KeyError(job)
    return {'jobId':job,'status':'succeeded' if time.monotonic()-jobs[job]>3 else 'running','message':'驗收模擬同步；未更新正式知識庫。'}
routes.enqueue_sync=enqueue
routes.get_sync_status=status
if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='0.0.0.0',port=8000,access_log=False)
