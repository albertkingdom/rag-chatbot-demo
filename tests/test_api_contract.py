"""Lock HTTP/event schema, conflicts, transport errors, and storage ownership."""
import asyncio
import json
import sys
import types
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError
from starlette.requests import Request
from src.api import routes
from src.api_errors import ApiErrorResponse
from src.chat_events import ChatStreamEvent, DeltaEvent, DoneEvent
from tests.test_api_routes import _app

@pytest.mark.parametrize("event", [
    {"type":"status","stage":"retrieving","message":"search"},
    {"type":"delta","text":"answer"},
    {"type":"sources","items":[{"label":"manual"}]},
    {"type":"metadata","elapsedMs":2,"responseSource":"rag","cacheHit":False},
    {"type":"done"}, {"type":"error","code":"internal_error","message":"safe error"},
])
def test_event_schema_roundtrip(event):
    parsed=TypeAdapter(ChatStreamEvent).validate_python(event)
    assert json.loads(parsed.model_dump_json(by_alias=True))==event


def test_invalid_event_and_job_status_are_rejected():
    with pytest.raises(ValidationError): TypeAdapter(ChatStreamEvent).validate_python({"type":"unknown"})
    with pytest.raises(ValidationError): routes.SyncJobResponse(jobId="job",status="unknown")


def test_openapi_errors_and_safe_validation_are_consistent():
    app=_app()
    schema=app.openapi()
    for code in ("401","409","413","422","429","503"):
        assert schema['paths']['/api/v1/chat/stream']['post']['responses'][code]['content']['application/json']['schema']['$ref'].endswith('/ApiErrorResponse')
    response=TestClient(app).post('/api/v1/auth/login',json={'apiKey':'secret-input-'*500})
    assert response.status_code==422
    error=ApiErrorResponse.model_validate(response.json())
    assert error.code=='invalid_request'
    assert 'secret-input' not in response.text


def test_client_cannot_supply_an_arbitrary_history_key():
    response=TestClient(_app()).post('/api/v1/chat/stream',json={'message':'q','historyKey':'other-user'})
    assert response.status_code==422


@pytest.mark.parametrize('endpoint,method', [('/api/v1/auth/login','post'),('/api/v1/auth/logout','post')])
def test_session_store_errors_are_503(endpoint,method):
    redis=MagicMock(); redis.setex.side_effect=RuntimeError('private infrastructure details');redis.delete.side_effect=RuntimeError('private infrastructure details')
    client=TestClient(_app(enabled=True,redis=redis));client.cookies.set('session_id','sid')
    response=getattr(client,method)(endpoint,headers={'Origin':'http://testserver'},json={'apiKey':'test-key'} if endpoint.endswith('login') else None)
    assert response.status_code==503
    assert ApiErrorResponse.model_validate(response.json()).code=='unavailable'
    assert 'private infrastructure' not in response.text


@pytest.mark.parametrize('path,method', [('/api/v1/chat/stream','post'),('/api/v1/conversations/current/messages','delete')])
def test_active_cookie_session_rejects_mutations_with_409(path,method):
    app=_app(enabled=True);app.state.active_chat_sessions={'sid'}
    client=TestClient(app);client.cookies.set('session_id','sid')
    kwargs={'json':{'message':'q'}} if method=='post' else {}
    response=getattr(client,method)(path,headers={'Origin':'http://testserver'},**kwargs)
    assert response.status_code==409
    assert ApiErrorResponse.model_validate(response.json()).code=='conflict'


def test_transport_exception_ends_with_safe_error_and_releases_session(monkeypatch):
    app=_app(enabled=True)
    fake=types.ModuleType('src.chat_service')
    async def broken(*args,**kwargs):
        yield DeltaEvent(text='partial')
        raise RuntimeError('private-provider-error')
    fake.chat_event_stream=broken
    monkeypatch.setitem(sys.modules,'src.chat_service',fake)
    client=TestClient(app);client.cookies.set('session_id','sid')
    response=client.post('/api/v1/chat/stream',json={'message':'q'},headers={'Origin':'http://testserver'})
    events=[json.loads(line) for line in response.text.splitlines()]
    assert [event['type'] for event in events]==['delta','error']
    assert 'private-provider-error' not in response.text
    assert not app.state.active_chat_sessions


def test_closing_transport_closes_service_and_releases_session(monkeypatch):
    app=_app(enabled=True)
    fake=types.ModuleType('src.chat_service');closed=[]
    async def stream(*args,**kwargs):
        try:
            yield DeltaEvent(text='partial')
            yield DoneEvent()
        finally: closed.append(True)
    fake.chat_event_stream=stream;monkeypatch.setitem(sys.modules,'src.chat_service',fake)
    request=Request({'type':'http','method':'POST','path':'/api/v1/chat/stream','scheme':'http','server':('testserver',80),'query_string':b'','headers':[(b'cookie',b'session_id=sid'),(b'origin',b'http://testserver'),(b'host',b'testserver')],'app':app})
    async def run():
        response=await routes.stream_chat(routes.ChatRequest(message='q'),request)
        await response.body_iterator.__anext__()
        await response.body_iterator.aclose()
    asyncio.run(run())
    assert closed==[True]
    assert not app.state.active_chat_sessions


def test_upload_collision_is_http_409(monkeypatch):
    async def collision(_file): raise routes.ManualConflictError('檔案名稱衝突')
    monkeypatch.setattr(routes,'save_manual',collision)
    response=TestClient(_app()).post('/api/v1/admin/manuals',files={'file':('manual.csv',b'q,a','text/csv')})
    assert response.status_code==409
    assert response.json()['code']=='conflict'

def test_api_key_header_cannot_select_history_with_an_unvalidated_cookie():
    redis = MagicMock()
    redis.get.side_effect = lambda key: None if key.startswith('session:') else json.dumps([{'role':'user','content':'private history'}])
    client = TestClient(_app(enabled=True, redis=redis))
    client.cookies.set('session_id', 'unvalidated-client-value')
    response = client.get('/api/v1/conversations/current/messages', headers={'X-API-Key':'test-key'})
    assert response.status_code == 200
    assert response.json() == {'items': []}
    assert not any(call.args[0].startswith('chat_history:') for call in redis.get.call_args_list)
