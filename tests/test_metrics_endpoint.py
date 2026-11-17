"""Spec: metrics-endpoint-exposure — GET /metrics is off unless METRICS_ENABLED.

create_app reads ``config.METRICS_ENABLED`` when it is called, so each test
flips the module attribute with monkeypatch and builds its own app.
"""

import pytest
from fastapi.testclient import TestClient
from prometheus_client import CONTENT_TYPE_LATEST

from src import config

SERVICE_METRICS = (
    "chat_requests_total",
    "chat_first_answer_text_seconds",
    "chat_response_duration_seconds",
    "rag_retrieval_total",
    "rag_retrieval_retries_total",
    "rag_rerank_duration_seconds",
)


@pytest.fixture
def make_client(monkeypatch, tmp_path):
    """Build an app with METRICS_ENABLED set as given and a one-page SPA behind it."""
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("FRONTEND_DIST_DIR", str(tmp_path))
    (tmp_path / "index.html").write_text("<html>spa shell</html>")

    def build(enabled):
        monkeypatch.setattr(config, "METRICS_ENABLED", enabled)
        from src.app import create_app

        return TestClient(create_app())

    return build


def test_enabled_serves_prometheus_text_with_every_service_metric(make_client):
    response = make_client(True).get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"] == CONTENT_TYPE_LATEST
    for name in SERVICE_METRICS:
        assert f"# HELP {name} " in response.text


def test_enabled_route_is_not_listed_in_openapi(make_client):
    schema = make_client(True).get("/openapi.json").json()

    assert "/metrics" not in schema["paths"]


def test_disabled_does_not_serve_metrics_and_falls_through_to_the_spa(make_client):
    response = make_client(False).get("/metrics")

    assert "# HELP" not in response.text
    assert "spa shell" in response.text


def test_disabled_without_a_frontend_build_returns_the_503_json_not_metrics(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("FRONTEND_DIST_DIR", str(tmp_path / "missing"))
    monkeypatch.setattr(config, "METRICS_ENABLED", False)
    from src.app import create_app

    response = TestClient(create_app()).get("/metrics")

    assert response.status_code == 503
    assert "# HELP" not in response.text


def test_the_route_is_registered_before_the_spa_catch_all(make_client):
    # The catch-all would otherwise answer /metrics with the SPA shell.
    response = make_client(True).get("/metrics")

    assert "spa shell" not in response.text


def test_flag_is_read_when_the_app_is_built_not_when_the_module_is_imported(make_client):
    assert "# HELP" in make_client(True).get("/metrics").text
    assert "# HELP" not in make_client(False).get("/metrics").text
