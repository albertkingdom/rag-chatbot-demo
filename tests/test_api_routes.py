"""Hermetic contract tests for the React frontend API."""

import json
import sys
import types
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.access_control import AuthConfig, SESSION_COOKIE
from src.api.routes import router
from src.chat_events import DeltaEvent, DoneEvent, MetadataEvent, StatusEvent


def _app(*, enabled: bool = False, redis=None) -> FastAPI:
    application = FastAPI()
    application.state.auth_config = AuthConfig(
        enabled=enabled,
        api_key="test-key" if enabled else None,
        rate_limit_rpm=60,
        session_ttl_seconds=86400,
        redis=redis or MagicMock(),
        spa_public=True,
    )
    application.include_router(router)
    return application


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
