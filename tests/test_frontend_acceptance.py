"""API/security/static acceptance without infrastructure calls."""
import json
from unittest.mock import MagicMock
import pytest
from fastapi.testclient import TestClient
from src.access_control import AuthRateLimitMiddleware, SESSION_COOKIE
from tests.test_api_routes import _app
from src.api import routes


def protected_client(*, count=1):
    redis = MagicMock()
    redis.get.return_value = json.dumps({"key_hash":"test-hash"})
    redis.pipeline.return_value.execute.return_value = [count,True]
    app = _app(enabled=True,redis=redis)
    app.add_middleware(AuthRateLimitMiddleware,config=app.state.auth_config)
    return TestClient(app),redis


@pytest.mark.parametrize("headers,expected", [({},403),({"Origin":"https://evil.example"},403),({"Origin":"http://testserver"},204),({"Referer":"http://testserver/admin"},204),({"X-API-Key":"test-key"},204)])
def test_cookie_mutation_origin_boundary(headers,expected):
    client,_ = protected_client()
    client.cookies.set(SESSION_COOKIE,"test-session")
    assert client.delete('/api/v1/conversations/current/messages',headers=headers).status_code == expected


def test_session_expiration_returns_401_without_redirect_loop():
    client,redis=protected_client(); redis.get.return_value=None
    client.cookies.set(SESSION_COOKIE,"expired")
    response=client.get('/api/v1/auth/session')
    assert response.status_code == 401


def test_api_always_returns_json_even_for_html_accept():
    client,_=protected_client()
    response=client.get('/api/v1/auth/session',headers={"Accept":"text/html"},follow_redirects=False)
    assert response.status_code == 401
    assert response.headers['content-type'].startswith('application/json')


def test_rate_limit_has_retry_after():
    client,_=protected_client(count=61)
    response=client.get('/api/v1/auth/session',headers={"X-API-Key":"test-key"})
    assert response.status_code == 429
    assert int(response.headers['Retry-After'])>0


def test_upload_enqueue_failure_cleans_saved_file(tmp_path,monkeypatch):
    path=tmp_path/'manual.csv';path.write_text('q,a')
    async def save(_): return path
    def fail(): raise RuntimeError('unavailable')
    monkeypatch.setattr(routes,'save_manual',save);monkeypatch.setattr(routes,'enqueue_sync',fail)
    response=TestClient(_app()).post('/api/v1/admin/manuals',files={'file':('manual.csv',b'q,a','text/csv')})
    assert response.status_code == 503
    assert not path.exists()


@pytest.mark.parametrize('status,exception',[(404,KeyError('missing')),(503,RuntimeError('unavailable'))])
def test_job_errors_are_safe(status,exception,monkeypatch):
    def fail(_): raise exception
    monkeypatch.setattr(routes,'get_sync_status',fail)
    response=TestClient(_app()).get('/api/v1/admin/jobs/test-job')
    assert response.status_code==status


def test_spa_deep_link_and_api_404(tmp_path,monkeypatch):
    monkeypatch.setenv('FRONTEND_MODE','spa');monkeypatch.setenv('AUTH_ENABLED','false');monkeypatch.setenv('FRONTEND_DIST_DIR',str(tmp_path))
    (tmp_path/'index.html').write_text('<html>acceptance SPA</html>')
    from src.app import create_app
    client=TestClient(create_app())
    assert 'acceptance SPA' in client.get('/admin').text
    assert client.get('/api/unknown').status_code==404
    assert client.get('/api/unknown').json()=={'detail':'Not Found'}
    assert client.get('/health').json()=={'status':'ok'}
