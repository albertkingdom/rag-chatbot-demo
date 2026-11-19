"""src/vertex_reranker.py — Vertex AI Ranking client (spec: rerank-stage).

Credentials and the HTTP session are injected fakes; no network.
"""

import json
import threading

import pytest
import requests

from src.hosted_reranker import RerankError
from src import vertex_reranker as vr

URL = (
    "https://discoveryengine.googleapis.com/v1/projects/proj-1/locations/global/"
    "rankingConfigs/default_ranking_config:rank"
)


class FakeResponse:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


class FakeSession:
    instances = []

    def __init__(self, credentials, max_refresh_attempts=None):
        self.credentials = credentials
        self.max_refresh_attempts = max_refresh_attempts
        self.calls = []
        self.reply = (200, {"records": []})
        self.exc = None
        FakeSession.instances.append(self)

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers or {}, "timeout": timeout})
        if self.exc:
            raise self.exc
        return FakeResponse(*self.reply)


class UserCredentials:
    """Stands in for google.oauth2.credentials.Credentials (end-user ADC)."""

    def __init__(self, quota_project_id=None):
        self.quota_project_id = quota_project_id

    def with_quota_project(self, project):
        return UserCredentials(project)


class ServiceAccountCredentials:
    quota_project_id = None

    def with_quota_project(self, project):  # must not be called
        raise AssertionError("service account credentials must not get a quota project")


@pytest.fixture(autouse=True)
def reset_sessions():
    FakeSession.instances.clear()


def make(credentials=None, timeout=3.0, counter=None):
    creds = credentials if credentials is not None else ServiceAccountCredentials()

    def default(scopes=None):
        if counter is not None:
            counter.append(1)
        return creds, "ignored-project"

    return vr.VertexReranker(
        project_id="proj-1",
        timeout=timeout,
        credentials_factory=default,
        session_factory=FakeSession,
        user_credentials_type=UserCredentials,
    )


def pairs(*passages, query="q"):
    return [(query, p) for p in passages]


def session():
    assert len(FakeSession.instances) == 1
    return FakeSession.instances[0]


def test_building_the_client_does_not_fetch_credentials():
    counter = []
    make(counter=counter)
    assert counter == []
    assert FakeSession.instances == []


def test_request_contract():
    client = make(timeout=3.0)
    client._ensure_session()
    session().reply = (200, {"records": [{"id": "0", "score": 0.4}, {"id": "1", "score": 0.6}]})
    client.score(pairs("a", "b", query="question"))
    call = session().calls[0]
    assert call["url"] == URL
    assert call["json"] == {
        "model": "semantic-ranker-default-005",
        "query": "question",
        "records": [{"id": "0", "content": "a"}, {"id": "1", "content": "b"}],
    }
    assert call["timeout"] == 3.0 + vr.HTTP_TIMEOUT_MARGIN_SECONDS
    assert "X-Goog-User-Project" not in call["headers"]


def test_session_never_refreshes_and_resends_on_401():
    client = make()
    client._ensure_session()
    assert session().max_refresh_attempts == 0


def test_user_credentials_get_the_project_as_quota_project():
    client = make(credentials=UserCredentials())
    client._ensure_session()
    assert session().credentials.quota_project_id == "proj-1"


def test_user_credentials_keep_an_existing_quota_project():
    client = make(credentials=UserCredentials("other"))
    client._ensure_session()
    assert session().credentials.quota_project_id == "other"


def test_service_account_credentials_get_no_quota_project():
    client = make(credentials=ServiceAccountCredentials())
    client._ensure_session()
    assert isinstance(session().credentials, ServiceAccountCredentials)


def test_scores_come_back_in_input_order():
    client = make()
    client._ensure_session()
    session().reply = (200, {"records": [
        {"id": "2", "score": 0.9}, {"id": "0", "score": 0.5}, {"id": "1", "score": 0.1},
    ]})
    assert client.score(pairs("a", "b", "c")) == [0.5, 0.1, 0.9]


def test_record_without_a_score_counts_as_zero():
    client = make()
    client._ensure_session()
    session().reply = (200, {"records": [{"id": "0", "score": 0.7}, {"id": "1"}]})
    assert client.score(pairs("a", "b")) == [0.7, 0.0]


def test_missing_record_is_an_error():
    client = make()
    client._ensure_session()
    session().reply = (200, {"records": [{"id": "0", "score": 0.7}]})
    with pytest.raises(RerankError):
        client.score(pairs("a", "b"))


def test_pairs_with_different_queries_are_rejected():
    client = make()
    with pytest.raises(ValueError):
        client.score([("q1", "a"), ("q2", "b")])
    assert FakeSession.instances == []


def test_too_many_passages_for_one_request():
    client = make()
    with pytest.raises(ValueError):
        client.score(pairs(*[str(i) for i in range(vr.MAX_DOCUMENTS + 1)]))
    assert FakeSession.instances == []


def test_empty_input_sends_nothing():
    client = make()
    assert client.score([]) == []
    assert FakeSession.instances == []


def test_failed_request_is_sent_once_and_hides_the_content():
    client = make()
    client._ensure_session()
    session().reply = (403, {"error": {"message": "secret passage text"}})
    with pytest.raises(RerankError) as err:
        client.score(pairs("secret passage text", query="secret question"))
    assert len(session().calls) == 1
    assert "403" in str(err.value)
    assert "secret" not in str(err.value)


def test_http_timeout_propagates_as_requests_timeout():
    client = make()
    client._ensure_session()
    session().exc = requests.Timeout("read timed out")
    with pytest.raises(requests.Timeout):
        client.score(pairs("a"))


def test_one_session_is_reused_across_calls():
    client = make()
    for _ in range(3):
        client._ensure_session()
        FakeSession.instances[0].reply = (200, {"records": [{"id": "0", "score": 0.1}]})
        client.score(pairs("a"))
    assert len(FakeSession.instances) == 1
    assert len(FakeSession.instances[0].calls) == 3


def test_concurrent_first_calls_fetch_credentials_once():
    counter = []
    client = make(counter=counter)
    start = threading.Barrier(8)

    def worker():
        start.wait()
        client._ensure_session()

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(counter) == 1
    assert len(FakeSession.instances) == 1
