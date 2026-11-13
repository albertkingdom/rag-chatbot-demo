"""tests/evaluate_rag.py — a rerank failure must fail the evaluation.

Collection and scoring are patched; no LLM, index or network.
"""

import pytest

from tests import evaluate_rag as ev

CASES = [{"question": "q1", "ground_truth": "a1"}, {"question": "q2", "ground_truth": "a2"}]


@pytest.fixture
def env(monkeypatch, tmp_path):
    for name in ev.REQUIRED_CREDENTIALS:
        monkeypatch.setenv(name, "fake")
    monkeypatch.setattr(ev, "load_dataset", lambda path: CASES)
    report = tmp_path / "report.json"
    monkeypatch.setattr(ev, "REPORT_PATH", str(report))
    scored = []

    def fake_score(samples):
        scored.append(samples)
        return {name: 1.0 for name in ev.METRIC_NAMES}

    monkeypatch.setattr(ev, "score_with_ragas", fake_score)
    return monkeypatch, report, scored


def samples(*errors):
    return [
        {"question": c["question"], "answer": "x", "contexts": ["c"], "ground_truth": c["ground_truth"],
         **({"rerank_error": e} if e else {})}
        for c, e in zip(CASES, errors)
    ]


def install(monkeypatch, result):
    async def fake_collect(cases):
        return result

    monkeypatch.setattr(ev, "collect_samples", fake_collect)


def test_rerank_failure_fails_before_scoring(env, capsys):
    monkeypatch, report, scored = env
    install(monkeypatch, samples(None, "timeout"))
    assert ev.main() == 1
    assert scored == []
    assert not report.exists()
    assert "1/2" in capsys.readouterr().out


def test_no_failure_scores_without_the_marker(env):
    monkeypatch, report, scored = env
    install(monkeypatch, samples(None, None))
    assert ev.main() == 0
    assert len(scored) == 1
    assert all("rerank_error" not in s for s in scored[0])
    assert report.exists()


def test_collect_samples_records_rerank_error(monkeypatch):
    import asyncio

    from src import rag_pipeline

    class Retrieval:
        async def ainvoke(self, question):
            return {"context": "", "contexts": [], "rerank_error": "error"}

    class Generation:
        async def ainvoke(self, payload):
            return "answer"

    monkeypatch.setattr(rag_pipeline, "get_retrieval_chain", lambda: Retrieval())
    monkeypatch.setattr(rag_pipeline, "get_generation_chain", lambda: Generation())
    [first, _] = asyncio.run(ev.collect_samples(CASES))
    assert first["rerank_error"] == "error"
