"""Tests for the /health endpoint (exempt from auth + rate limiting, no heavy
singleton initialization).

Hermetic: Redis is mocked; no external services required.
"""
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.access_control import AuthConfig, AuthRateLimitMiddleware, _health


def _make_app(enabled=True):
    config = AuthConfig(
        enabled=enabled,
        api_key="test-key" if enabled else None,
        rate_limit_rpm=60,
        session_ttl_seconds=86400,
        redis=MagicMock(),
    )
    app = FastAPI()
    app.add_route("/health", _health, methods=["GET"])

    @app.get("/protected")
    def _protected():
        return {"ok": True}

    app.add_middleware(AuthRateLimitMiddleware, config=config)
    return app


class TestHealthEndpoint:
    def test_health_no_credential_returns_200(self):
        app = _make_app(enabled=True)
        c = TestClient(app)
        r = c.get("/health")  # no X-API-Key, no session cookie
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}

    def test_health_not_rate_limited(self):
        # Even with a tiny rate limit, /health must always return 200.
        app = _make_app(enabled=True)
        # Override rate limit to 2 so we can exceed it quickly.
        app.user_middleware[0].kwargs["config"].rate_limit_rpm = 2
        c = TestClient(app)
        for _ in range(10):
            r = c.get("/health")
            assert r.status_code == 200, "health must bypass rate limiting"

    def test_health_does_not_load_heavy_singletons(self):
        app = _make_app(enabled=True)
        c = TestClient(app)
        with patch("src.app.get_reranker_model") as spy_reranker, \
             patch("src.app.get_embeddings") as spy_embed, \
             patch("src.app.get_vectorstore") as spy_vs, \
             patch("src.app.get_bm25_index") as spy_bm25:
            r = c.get("/health")
        assert r.status_code == 200
        spy_reranker.assert_not_called()
        spy_embed.assert_not_called()
        spy_vs.assert_not_called()
        spy_bm25.assert_not_called()
