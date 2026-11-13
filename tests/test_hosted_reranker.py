"""src/hosted_reranker.py — Pinecone Inference rerank client (spec: rerank-stage).

requests.Session.post is patched; no network.
"""

import json

import pytest
import requests

from src import hosted_reranker as hr


class FakeResponse:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


@pytest.fixture
def post(monkeypatch):
    calls = []

    def install(status=200, body=None, exc=None):
        def fake_post(session, url, headers=None, json=None, timeout=None):
            calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout,
                          "session": session})
            if exc:
                raise exc
            return FakeResponse(status, body)

        monkeypatch.setattr(hr.requests.Session, "post", fake_post)
        return calls

    return install


def client(timeout=3.0):
    return hr.PineconeReranker(api_key="pk-secret", timeout=timeout)


def pairs(*passages, query="q"):
    return [(query, p) for p in passages]


def test_scores_come_back_in_input_order(post):
    calls = post(body={"data": [{"index": 2, "score": 0.9}, {"index": 0, "score": 0.5}, {"index": 1, "score": 0.1}]})
    assert client().score(pairs("a", "b", "c")) == [0.5, 0.1, 0.9]
    assert len(calls) == 1


def test_request_contract(post):
    calls = post(body={"data": [{"index": 0, "score": 0.4}]})
    client(timeout=3.0).score(pairs("a", query="how"))
    call = calls[0]
    assert call["url"] == hr.RERANK_URL
    assert call["headers"]["Api-Key"] == "pk-secret"
    assert call["headers"]["X-Pinecone-Api-Version"] == hr.API_VERSION
    assert call["json"] == {
        "model": "bge-reranker-v2-m3",
        "query": "how",
        "documents": [{"text": "a"}],
        "top_n": 1,
        "return_documents": False,
        "parameters": {"truncate": "END"},
    }
    assert call["timeout"] == 4.0  # client-side HTTP timeout = rerank timeout + 1s


def test_pairs_with_different_queries_are_rejected(post):
    calls = post(body={"data": []})
    with pytest.raises(ValueError):
        client().score([("q1", "a"), ("q2", "b")])
    assert calls == []


def test_too_many_passages_for_one_request(post):
    calls = post(body={"data": []})
    with pytest.raises(ValueError):
        client().score(pairs(*[f"p{i}" for i in range(hr.MAX_DOCUMENTS + 1)]))
    assert calls == []


def test_limit_itself_is_accepted(post):
    n = hr.MAX_DOCUMENTS
    post(body={"data": [{"index": i, "score": 0.1} for i in range(n)]})
    assert len(client().score(pairs(*[f"p{i}" for i in range(n)]))) == n


def test_empty_input_sends_nothing(post):
    calls = post(body={"data": []})
    assert client().score([]) == []
    assert calls == []


def test_failed_request_is_not_retried_and_hides_the_key(post):
    calls = post(status=401, body={"error": "unauthorized"})
    with pytest.raises(hr.RerankError) as err:
        client().score(pairs("a"))
    assert len(calls) == 1
    assert "401" in str(err.value)
    assert "pk-secret" not in str(err.value)


def test_partial_response_is_an_error(post):
    post(body={"data": [{"index": 0, "score": 0.5}]})
    with pytest.raises(hr.RerankError):
        client().score(pairs("a", "b"))


def test_http_timeout_propagates_as_requests_timeout(post):
    post(exc=requests.Timeout("read timed out"))
    with pytest.raises(requests.Timeout):
        client().score(pairs("a"))


def test_one_session_is_reused_across_calls(post):
    """Connections (and their TLS handshake) are reused between rerank calls."""
    calls = post(body={"data": [{"index": 0, "score": 0.4}]})
    c = client()
    c.score(pairs("a"))
    c.score(pairs("b"))
    assert len(calls) == 2
    assert isinstance(calls[0]["session"], requests.Session)
    assert calls[0]["session"] is calls[1]["session"]
