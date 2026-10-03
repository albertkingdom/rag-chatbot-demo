"""Same typed service output remains readable through the temporary legacy UI."""
import asyncio
import sys
import types
from types import SimpleNamespace
import pytest
from src.gradio_adapter import chat_stream
from src.chat_events import DeltaEvent, DoneEvent, MetadataEvent, SourcesEvent, SourceItem, ErrorEvent

@pytest.mark.parametrize('route',['rag','direct','cache','guardrail'])
def test_legacy_adapter_parity(monkeypatch,route):
    calls=[];fake=types.ModuleType('src.chat_service')
    async def events(message,**kwargs):
        calls.append((message,kwargs))
        yield DeltaEvent(text='完整');yield DeltaEvent(text='回答')
        if route in ('rag','cache'): yield SourcesEvent(items=[SourceItem(label='手冊')])
        yield MetadataEvent(elapsed_ms=1200,response_source=route,cache_hit=route=='cache')
        yield DoneEvent()
    fake.chat_event_stream=events;monkeypatch.setitem(sys.modules,'src.chat_service',fake)
    async def run(): return [text async for text in chat_stream('q',[],SimpleNamespace(cookies={'session_id':'sid'},session_hash='legacy'))]
    result=asyncio.run(run())
    assert result[:2]==['完整','完整回答']
    assert '回應時間：1.2 秒' in result[-1]
    assert ('參考資料：' in result[-1])==(route in ('rag','cache'))
    assert calls[0][1]['history_key']=='sid'


def test_legacy_adapter_exposes_safe_terminal_error(monkeypatch):
    fake=types.ModuleType('src.chat_service')
    async def events(*args,**kwargs): yield ErrorEvent()
    fake.chat_event_stream=events;monkeypatch.setitem(sys.modules,'src.chat_service',fake)
    async def run(): return [text async for text in chat_stream('q',[],None)]
    assert asyncio.run(run())==['暫時無法完成回答，請稍後再試。']
