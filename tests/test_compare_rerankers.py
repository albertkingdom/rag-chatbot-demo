"""scripts/compare_rerankers.py — reranker comparison tooling.

No real reranker, index or network: scorers are plain functions, HTTP calls go
through a patched requests.post, and retrieval is not exercised.
"""

import json

import pytest

from scripts import compare_rerankers as cmp
from src import config


def by_keyword(keyword):
    """Scorer that ranks passages containing keyword first."""
    return lambda query, passages: [1.0 if keyword in p else 0.0 for p in passages]


def case(relevant_pos, n=5):
    passages = [f"p{i}" for i in range(n)]
    passages[relevant_pos] = "gold"
    return cmp.Case("q", passages, {relevant_pos})


# --- ranking metrics ---------------------------------------------------------


def test_perfect_scorer_hits_everything():
    result = cmp.evaluate("x", "m", by_keyword("gold"), [case(4), case(0)])
    s = result.summary()
    assert (s["scored_cases"], s["hit@1"], s["hit@3"], s["mrr"]) == (2, 2, 2, 1.0)


def test_relevant_ranked_fourth_misses_top3():
    scores = {"p0": 5, "p1": 4, "p2": 3, "gold": 2, "p4": 1}
    scorer = lambda q, ps: [scores[p] for p in ps]
    s = cmp.evaluate("x", "m", scorer, [case(3)]).summary()
    assert (s["hit@1"], s["hit@3"], s["mrr"]) == (0, 0, 0.25)


def test_relevant_ranked_second_is_hit3_not_hit1():
    scores = {"p0": 5, "gold": 4, "p2": 3, "p3": 2, "p4": 1}
    scorer = lambda q, ps: [scores[p] for p in ps]
    s = cmp.evaluate("x", "m", scorer, [case(1)]).summary()
    assert (s["hit@1"], s["hit@3"], s["mrr"]) == (0, 1, 0.5)


def test_case_missed_by_retrieval_is_counted_but_not_scored():
    missed = cmp.Case("q", ["a", "b"], set())
    s = cmp.evaluate("x", "m", by_keyword("a"), [missed, case(0)]).summary()
    assert (s["cases"], s["scored_cases"], s["hit@1"]) == (2, 1, 1)


def test_scorer_error_is_recorded_and_the_run_continues():
    calls = []

    def flaky(query, passages):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("HTTP 500")
        return by_keyword("gold")(query, passages)

    s = cmp.evaluate("x", "m", flaky, [case(0), case(2)]).summary()
    assert len(s["errors"]) == 1 and "HTTP 500" in s["errors"][0]
    assert (s["scored_cases"], s["hit@1"]) == (1, 1)


def test_latency_and_score_range_are_reported():
    ticks = iter([0.0, 0.2, 1.0, 1.05])
    s = cmp.evaluate("x", "m", by_keyword("gold"), [case(0), case(1)], clock=lambda: next(ticks)).summary()
    assert s["latency_ms_max"] == 200.0
    assert s["latency_ms_median"] == pytest.approx(125.0)
    assert (s["score_min"], s["score_max"]) == (0.0, 1.0)


def test_synthetic_cases_mark_relevant_passages():
    cases = cmp.synthetic_cases()
    assert cases
    for c in cases:
        assert c.relevant
        assert all(0 <= i < len(c.passages) for i in c.relevant)


# --- API response handling -----------------------------------------------------


def test_scores_are_put_back_in_passage_order():
    items = [{"index": 2, "s": 0.9}, {"index": 0, "s": 0.5}, {"index": 1, "s": 0.1}]
    assert cmp._scores_by_index(items, 3, "index", "s") == [0.5, 0.1, 0.9]


def test_partial_response_is_an_error():
    with pytest.raises(RuntimeError):
        cmp._scores_by_index([{"index": 0, "s": 1.0}], 2, "index", "s")


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

    def install(status, body):
        def fake_post(url, headers, json, timeout):
            calls.append({"url": url, "headers": headers, "json": json})
            return FakeResponse(status, body)

        monkeypatch.setattr(cmp.requests, "post", fake_post)
        return calls

    return install


def test_voyage_request_and_reordering(monkeypatch, post):
    monkeypatch.setenv("VOYAGE_API_KEY", "vk-test")
    calls = post(200, {"data": [{"index": 1, "relevance_score": 0.8}, {"index": 0, "relevance_score": 0.2}]})
    scores = cmp.voyage_scorer("rerank-3")("q", ["a", "b"])
    assert scores == [0.2, 0.8]
    assert calls[0]["json"] == {"query": "q", "documents": ["a", "b"], "model": "rerank-3"}
    assert calls[0]["headers"]["Authorization"] == "Bearer vk-test"


def test_cohere_request_and_reordering(monkeypatch, post):
    monkeypatch.setenv("COHERE_API_KEY", "ck-test")
    calls = post(200, {"results": [{"index": 1, "relevance_score": 0.7}, {"index": 0, "relevance_score": 0.3}]})
    assert cmp.cohere_scorer("rerank-v5.0-pro")("q", ["a", "b"]) == [0.3, 0.7]
    assert calls[0]["json"]["top_n"] == 2
    assert calls[0]["json"]["model"] == "rerank-v5.0-pro"


def test_http_error_message_does_not_contain_the_key(monkeypatch, post):
    monkeypatch.setenv("VOYAGE_API_KEY", "vk-secret")
    post(401, {"detail": "unauthorized"})
    with pytest.raises(RuntimeError) as err:
        cmp.voyage_scorer("rerank-3")("q", ["a"])
    assert "401" in str(err.value)
    assert "vk-secret" not in str(err.value)


@pytest.mark.parametrize("factory, env", [
    (cmp.voyage_scorer, "VOYAGE_API_KEY"),
    (cmp.cohere_scorer, "COHERE_API_KEY"),
    (cmp.pinecone_scorer, "PINECONE_API_KEY"),
])
def test_missing_key_makes_provider_unavailable(monkeypatch, factory, env):
    monkeypatch.delenv(env, raising=False)
    with pytest.raises(cmp.ProviderUnavailable):
        factory("m")


# --- main ------------------------------------------------------------------------


def test_refuses_production_index(monkeypatch, capsys):
    monkeypatch.setattr(config, "PINECONE_INDEX_NAME", cmp.PRODUCTION_INDEXES[0])
    assert cmp.main(["--dataset", "manual"]) == 1
    assert "production index" in capsys.readouterr().out


def test_manual_dataset_needs_retrieval_keys(monkeypatch, capsys):
    monkeypatch.setattr(config, "PINECONE_INDEX_NAME", "rag-demo-qa-index")
    monkeypatch.delenv("PINECONE_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    assert cmp.main(["--dataset", "manual"]) == 1
    assert "PINECONE_API_KEY" in capsys.readouterr().out


def test_unknown_provider_and_bad_model_override():
    assert cmp.main(["--dataset", "synthetic", "--providers", "nope"]) == 2
    assert cmp.main(["--dataset", "synthetic", "--model", "voyage"]) == 2


def test_no_runnable_provider_fails(monkeypatch):
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    assert cmp.main(["--dataset", "synthetic", "--providers", "voyage"]) == 1


def test_synthetic_run_writes_report(monkeypatch, tmp_path, capsys):
    seen = {}

    def factory(model):
        seen["model"] = model
        return lambda q, ps: [float(len(p)) for p in ps]

    monkeypatch.setitem(cmp.PROVIDER_FACTORIES, "voyage", factory)
    out = tmp_path / "report.json"
    code = cmp.main(["--dataset", "synthetic", "--providers", "voyage",
                     "--model", "voyage=rerank-3-lite", "--out", str(out)])
    assert code == 0
    assert seen["model"] == "rerank-3-lite"
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["dataset"] == "synthetic"
    assert report["results"][0]["provider"] == "voyage"
    assert report["results"][0]["scored_cases"] == report["cases"]
    assert "hit@3" in capsys.readouterr().out


# --- parity report ------------------------------------------------------------


def scored(name, per_case):
    r = cmp.Result(name, "m")
    r.case_scores = per_case
    return r


def test_parity_reports_max_and_mean_difference():
    base = scored("bge-local", [[0.90, 0.50, 0.10]])
    other = scored("pinecone-bge", [[0.91, 0.50, 0.10]])
    row = cmp.parity(base, other)
    assert row["max_abs_diff"] == pytest.approx(0.01)
    assert row["mean_abs_diff"] == pytest.approx(0.01 / 3, abs=1e-4)
    assert (row["compared_cases"], row["compared_passages"], row["top3_order_differs"]) == (1, 3, 0)


def test_parity_counts_order_changes_and_same_set():
    base = scored("a", [[0.9, 0.8, 0.7, 0.1], [0.9, 0.8, 0.7, 0.1]])
    # case 1: same top-3 members, swapped order; case 2: a different member enters the top 3
    other = scored("b", [[0.8, 0.9, 0.7, 0.1], [0.9, 0.8, 0.1, 0.7]])
    row = cmp.parity(base, other)
    assert row["top3_order_differs"] == 2
    assert row["top3_order_differs_same_set"] == 1


def test_parity_skips_cases_without_scores():
    row = cmp.parity(scored("a", [None, [0.5]]), scored("b", [[0.4], [0.5]]))
    assert (row["compared_cases"], row["compared_passages"]) == (1, 1)


def test_evaluate_keeps_scores_per_case():
    r = cmp.evaluate("x", "m", by_keyword("gold"), [case(0, n=2), cmp.Case("q", [], set())])
    assert r.case_scores == [[1.0, 0.0], None]


def test_bge_local_unavailable_without_sentence_transformers(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "sentence_transformers":
            raise ImportError("no module")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(cmp.ProviderUnavailable):
        cmp.bge_local_scorer("m")


def test_pinecone_provider_uses_the_service_client(monkeypatch):
    from src import hosted_reranker

    monkeypatch.setenv("PINECONE_API_KEY", "pk-test")
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen["url"], seen["json"] = url, json

        class R:
            status_code = 200

            def json(self):
                return {"data": [{"index": 1, "score": 0.7}, {"index": 0, "score": 0.2}]}

        return R()

    monkeypatch.setattr(hosted_reranker.requests, "post", fake_post)
    assert cmp.pinecone_scorer("ignored")("q", ["a", "b"]) == [0.2, 0.7]
    assert seen["url"] == hosted_reranker.RERANK_URL
    assert seen["json"]["parameters"] == {"truncate": "END"}


@pytest.mark.parametrize("cases", [
    [cmp.Case("q", [], set())],
    [cmp.Case("q", ["a"], {0}, fallback="vector_only")],
])
def test_manual_dataset_fails_without_real_candidates(monkeypatch, capsys, cases):
    monkeypatch.setattr(config, "PINECONE_INDEX_NAME", "rag-demo-qa-index")
    monkeypatch.setenv("PINECONE_API_KEY", "x")
    monkeypatch.setenv("OPENAI_API_KEY", "x")

    async def fake_manual_cases():
        return cases

    monkeypatch.setattr(cmp, "manual_cases", fake_manual_cases)
    monkeypatch.setitem(cmp.PROVIDER_FACTORIES, "voyage", lambda m: by_keyword("a"))
    assert cmp.main(["--dataset", "manual", "--providers", "voyage"]) == 1
    assert "vector-only" in capsys.readouterr().out


def test_report_includes_parity_rows(monkeypatch, tmp_path):
    monkeypatch.setitem(cmp.PROVIDER_FACTORIES, "voyage", lambda m: (lambda q, ps: [0.5] * len(ps)))
    monkeypatch.setitem(cmp.PROVIDER_FACTORIES, "cohere", lambda m: (lambda q, ps: [0.51] * len(ps)))
    out = tmp_path / "r.json"
    assert cmp.main(["--dataset", "synthetic", "--providers", "voyage,cohere", "--out", str(out)]) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["parity"][0]["baseline"] == "voyage"
    assert report["parity"][0]["max_abs_diff"] == pytest.approx(0.01)
    assert report["max_passage_chars"] > 0


def test_manual_cases_record_retrieval_fallback(monkeypatch, tmp_path):
    import asyncio

    from langchain_core.documents import Document

    from src import build_vector_store, services

    data = tmp_path / "eval.json"
    data.write_text(json.dumps([{"question": "q1", "ground_truth": "ans"}]), encoding="utf-8")
    monkeypatch.setattr(build_vector_store, "extract_from_xlsx", lambda path: [{"answer": "ans", "uuid": "u1"}])

    class FakeRetriever:
        async def retrieve(self, question):
            return {"candidates": [Document(page_content="x", metadata={"doc_id": "u1"})],
                    "fusion_metadata": {"fallback": "vector_only"}}

    monkeypatch.setattr(services, "get_hybrid_retriever", lambda: FakeRetriever())
    [c] = asyncio.run(cmp.manual_cases(str(data)))
    assert (c.fallback, c.relevant, c.passages) == ("vector_only", {0}, ["x"])
