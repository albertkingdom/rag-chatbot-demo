"""Hermetic contract tests for the React frontend API."""

import asyncio
import json
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from src.access_control import AuthConfig, SESSION_COOKIE
from src.api.routes import router
from src.chat_events import DeltaEvent, DoneEvent, MetadataEvent, StatusEvent
from src.api import routes


def _app(*, enabled: bool = False, redis=None) -> FastAPI:
    application = FastAPI()
    from src.api_errors import install_api_error_handlers
    install_api_error_handlers(application)
    application.state.auth_config = AuthConfig(
        enabled=enabled,
        api_key="test-key" if enabled else None,
        rate_limit_rpm=60,
        session_ttl_seconds=86400,
        redis=redis or MagicMock(),
    )
    application.include_router(router)
    return application


def _request_counts():
    """Every chat_requests_total sample as {(source, outcome): value}."""
    return {
        (sample.labels["response_source"], sample.labels["outcome"]): sample.value
        for family in REGISTRY.collect()
        for sample in family.samples
        if sample.name == "chat_requests_total"
    }


def _count_changes(before, after):
    return {key: after[key] - before.get(key, 0.0) for key in after if after[key] != before.get(key, 0.0)}


@pytest.fixture
def fake_chat_service(monkeypatch):
    """Install a stand-in for src.chat_service whose event stream the test supplies."""

    def install(stream):
        module = types.ModuleType("src.chat_service")
        module.chat_event_stream = stream
        monkeypatch.setitem(sys.modules, "src.chat_service", module)

    return install


class TestLoginAndSession:
    def test_login_sets_http_only_session_cookie(self):
        redis = MagicMock()
        client = TestClient(_app(enabled=True, redis=redis), base_url="https://testserver")

        response = client.post("/api/v1/auth/login", json={"apiKey": "test-key"})

        assert response.status_code == 204
        assert SESSION_COOKIE in response.cookies
        assert "HttpOnly" in response.headers["set-cookie"]
        assert "Secure" in response.headers["set-cookie"]
        redis.setex.assert_called_once()

    def test_login_rejects_invalid_key(self):
        response = TestClient(_app(enabled=True)).post(
            "/api/v1/auth/login", json={"apiKey": "wrong"}
        )

        assert response.status_code == 401

    def test_logout_revokes_only_presented_session(self):
        store = {}
        redis = MagicMock()
        redis.setex.side_effect = lambda key, ttl, value: store.update({key: value})
        redis.get.side_effect = store.get
        redis.delete.side_effect = lambda key: store.pop(key, None)
        app = _app(enabled=True, redis=redis)
        from src.access_control import AuthRateLimitMiddleware
        redis.pipeline.return_value.execute.return_value = [1, True]
        app.add_middleware(AuthRateLimitMiddleware, config=app.state.auth_config)
        a, b = TestClient(app), TestClient(app)
        for client in (a, b):
            assert client.post("/api/v1/auth/login", json={"apiKey": "test-key"}).status_code == 204
        assert a.cookies.get(SESSION_COOKIE) != b.cookies.get(SESSION_COOKIE)
        assert a.post("/api/v1/auth/logout", headers={"Origin": "http://testserver"}).status_code == 204
        assert a.get("/api/v1/auth/session").status_code == 401
        assert b.get("/api/v1/auth/session").status_code == 200

    def test_auth_disabled_session_is_authenticated(self):
        response = TestClient(_app()).get("/api/v1/auth/session")

        assert response.json() == {"authenticated": True, "authEnabled": False}


class TestChatStream:
    def test_stream_is_ndjson_with_stable_event_aliases(self):
        async def fake_stream(*args, **kwargs):
            yield StatusEvent(stage="retrieving", message="搜尋中")
            yield DeltaEvent(text="回答")
            yield MetadataEvent(elapsed_ms=42, response_source="rag", cache_hit=False)
            yield DoneEvent()

        fake_module = types.ModuleType("src.chat_service")
        fake_module.chat_event_stream = fake_stream
        previous = sys.modules.get("src.chat_service")
        sys.modules["src.chat_service"] = fake_module
        try:
            response = TestClient(_app()).post(
                "/api/v1/chat/stream", json={"message": "測試問題"}
            )
        finally:
            if previous is None:
                sys.modules.pop("src.chat_service", None)
            else:
                sys.modules["src.chat_service"] = previous

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/x-ndjson")
        events = [json.loads(line) for line in response.text.splitlines()]
        assert [event["type"] for event in events] == [
            "status",
            "delta",
            "metadata",
            "done",
        ]
        assert events[2] == {
            "type": "metadata",
            "elapsedMs": 42,
            "responseSource": "rag",
            "cacheHit": False,
        }

    def test_stream_lines_are_unchanged_by_metrics_and_the_request_is_counted_once(self, fake_chat_service):
        events = [
            StatusEvent(stage="retrieving", message="搜尋中"),
            DeltaEvent(text="回答"),
            MetadataEvent(elapsed_ms=42, response_source="rag", cache_hit=False),
            DoneEvent(),
        ]

        async def fake_stream(*args, **kwargs):
            for event in events:
                yield event

        fake_chat_service(fake_stream)
        before = _request_counts()

        response = TestClient(_app()).post("/api/v1/chat/stream", json={"message": "測試問題"})

        assert response.status_code == 200
        assert response.text == "".join(event.model_dump_json(by_alias=True) + "\n" for event in events)
        assert _count_changes(before, _request_counts()) == {("rag", "success"): 1.0}

    def test_failing_chat_service_sends_error_event_and_is_counted_as_error(self, fake_chat_service):
        async def failing_stream(*args, **kwargs):
            yield StatusEvent(stage="retrieving", message="搜尋中")
            raise RuntimeError("provider down")

        fake_chat_service(failing_stream)
        before = _request_counts()

        response = TestClient(_app()).post("/api/v1/chat/stream", json={"message": "測試問題"})

        assert [json.loads(line)["type"] for line in response.text.splitlines()] == ["status", "error"]
        assert _count_changes(before, _request_counts()) == {("unknown", "error"): 1.0}

    def test_client_disconnect_is_counted_as_cancelled_and_closes_the_chat_stream(self, fake_chat_service):
        closed = []

        async def endless_stream(*args, **kwargs):
            try:
                yield StatusEvent(stage="retrieving", message="搜尋中")
                yield DeltaEvent(text="回答")
                yield DoneEvent()
            finally:
                closed.append(True)

        fake_chat_service(endless_stream)
        polls = iter([False, True])

        async def is_disconnected():
            return next(polls)

        request = SimpleNamespace(
            headers={},
            cookies={},
            app=SimpleNamespace(state=SimpleNamespace(auth_config=SimpleNamespace(enabled=False))),
            is_disconnected=is_disconnected,
        )
        before = _request_counts()

        async def consume():
            response = await routes.stream_chat(routes.ChatRequest(message="測試問題"), request)
            return [line async for line in response.body_iterator]

        lines = asyncio.run(consume())

        assert [json.loads(line)["type"] for line in lines] == ["status"]
        assert closed == [True]
        assert _count_changes(before, _request_counts()) == {("unknown", "cancelled"): 1.0}

    def test_request_rejected_before_the_stream_starts_is_not_counted(self, fake_chat_service):
        async def never_started(*args, **kwargs):
            raise AssertionError("the chat service must not run for a rejected request")
            yield  # pragma: no cover

        fake_chat_service(never_started)
        application = _app(enabled=True)
        application.state.active_chat_sessions = {"session-1"}
        client = TestClient(application)
        client.cookies.set(SESSION_COOKIE, "session-1")
        before = _request_counts()

        response = client.post(
            "/api/v1/chat/stream",
            json={"message": "測試問題"},
            headers={"Origin": "http://testserver"},
        )

        assert response.status_code == 409
        assert _request_counts() == before

    def test_message_is_trimmed_and_limited_server_side(self):
        client = TestClient(_app())

        assert client.post("/api/v1/chat/stream", json={"message": "   "}).status_code == 422
        assert client.post(
            "/api/v1/chat/stream", json={"message": "x" * 2001}
        ).status_code == 422


class TestConversationMutation:
    def test_cookie_mutation_requires_same_origin(self):
        client = TestClient(_app(enabled=True))
        client.cookies.set(SESSION_COOKIE, "session-1")

        response = client.delete("/api/v1/conversations/current/messages")

        assert response.status_code == 403

    def test_clear_failure_is_reported_and_not_faked_as_success(self):
        redis = MagicMock()
        redis.delete.side_effect = RuntimeError("redis unavailable")
        client = TestClient(_app(enabled=True, redis=redis))
        client.cookies.set(SESSION_COOKIE, "session-1")

        response = client.delete(
            "/api/v1/conversations/current/messages",
            headers={"Origin": "http://testserver"},
        )

        assert response.status_code == 503
        assert "無法清除" in response.json()["detail"]
