"""Run inside the built image: python /tmp/frontend_docker_smoke.py.

Uses fake chat/job providers; no LLM, GCP, or datastore requests.
"""
import json
import importlib.util
import os
import shutil
import sys
import tempfile
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, os.getcwd())
os.environ.update(AUTH_ENABLED="false", LANGCHAIN_TRACING_V2="false", LANGSMITH_TRACING="false")
from fastapi.testclient import TestClient
from src.chat_events import DeltaEvent, DoneEvent, MetadataEvent

fake = types.ModuleType("src.chat_service")
async def chat_stream(*args, **kwargs):
    yield DeltaEvent(text="fake provider answer")
    yield MetadataEvent(elapsed_ms=10, response_source="rag", cache_hit=False)
    yield DoneEvent()
fake.chat_event_stream = chat_stream
sys.modules["src.chat_service"] = fake

from src.app import app
from src.api import routes
from src import manual_service
routes.enqueue_sync = lambda: "fake-job"
routes.get_sync_status = lambda job: {"jobId":job,"status":"succeeded"}

assert importlib.util.find_spec("gradio") is None, "runtime unexpectedly includes Gradio"
assert shutil.which("node") is None, "runtime unexpectedly includes Node"
assert not Path("/app/frontend/node_modules").exists()
with tempfile.TemporaryDirectory() as data_dir:
    manual_service.DATA_SOURCE_DIR = data_dir
    with TestClient(app) as client:
        assert client.get("/health").json()=={"status":"ok"}
        for path in ("/", "/login", "/admin"):
            response=client.get(path)
            assert response.status_code==200 and '<div id="root">' in response.text
        assets=list(Path('/app/frontend/dist/assets').iterdir())
        assert any(path.suffix=='.js' for path in assets)
        for asset in assets: assert client.get('/assets/'+asset.name).status_code==200
        assert client.get('/api/unknown').status_code==404
        assert client.get('/api/unknown').json()=={'detail':'Not Found','code':'not_found'}
        def ask(message):
            response=client.post('/api/v1/chat/stream',json={'message':message})
            assert response.status_code==200
            assert response.headers['content-type'].startswith('application/x-ndjson')
            events=[json.loads(line) for line in response.text.splitlines()]
            assert events[-1]['type']=='done'
            assert len([e for e in events if e['type']=='done'])==1
        ask('x'*2000)
        assert client.post('/api/v1/chat/stream',json={'message':'x'*2001}).status_code==422
        with ThreadPoolExecutor(max_workers=2) as pool: list(pool.map(ask,['first','second']))
        response=client.post('/api/v1/admin/manuals',files={'file':('manual.csv',b'question,answer\nq,a','text/csv')})
        assert response.status_code==202 and response.json()['jobId']=='fake-job'
        assert client.get('/api/v1/admin/jobs/fake-job').json()['status']=='succeeded'
        assert client.post('/api/v1/admin/manuals',files={'file':('manual.exe',b'x','application/octet-stream')}).status_code==422
        assert client.post('/api/v1/admin/manuals',files={'file':('../../manual.csv',b'q,a','text/csv')}).status_code==422
        manual_service.MAX_UPLOAD_BYTES=3
        assert client.post('/api/v1/admin/manuals',files={'file':('large.csv',b'four','text/csv')}).status_code==413
        assert len(list(Path(data_dir).iterdir()))==1
print('PASS built-image smoke: SPA/deep links/assets, API 404, 2000/2001 boundaries, typed stream, two fake requests, upload/job/413/422, Python-only runtime')
