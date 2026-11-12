"""Pipeline test for the reassembled retrieval chain.

Verifies that ``get_retrieval_chain().ainvoke(query)`` returns the full
five-key dict and that ``context`` is assembled from ``metadata["answer"]``
(preserving the existing generation contract).
"""
import pytest
from unittest.mock import MagicMock, AsyncMock

from langchain_core.documents import Document

import src.services as services_module
import src.rag_pipeline as rag_module


def _make_doc(doc_id: str, answer: str) -> Document:
    return Document(
        page_content=f"Q {doc_id}",
        metadata={"doc_id": doc_id, "answer": answer},
    )


@pytest.fixture
def docs_map():
    return {
        "A": _make_doc("A", "ans A"),
        "B": _make_doc("B", "ans B"),
        "C": _make_doc("C", "ans C"),
        "D": _make_doc("D", "ans D"),
    }


@pytest.fixture
def patched_chain(monkeypatch, docs_map):
    bm25 = MagicMock()
    bm25.is_built = True
    bm25.corpus_size = 4
    bm25.search = MagicMock(return_value=[("B", 3.0), ("D", 2.0), ("A", 1.0)])
    bm25.get_doc = MagicMock(side_effect=lambda d: docs_map.get(d))

    vector_store = MagicMock()
    vector_store.asimilarity_search_with_score = AsyncMock(
        return_value=[
            (docs_map["A"], 0.9),
            (docs_map["B"], 0.8),
            (docs_map["C"], 0.7),
        ]
    )

    reranker = MagicMock()
    reranker.score = MagicMock(return_value=[0.60, 0.95, 0.70, 0.80])

    # Build a mock hybrid retriever that returns a fixed fused candidate set,
    # preserving the original fixture's intent (independent of RRF internals).
    hybrid_retriever = MagicMock()
    hybrid_retriever.retrieve = AsyncMock(
        return_value={
            "candidates": [
                docs_map["B"],
                docs_map["A"],
                docs_map["D"],
                docs_map["C"],
            ],
            "fusion_metadata": {
                "bm25_results": [
                    {"doc_id": "B", "score": 3.0},
                    {"doc_id": "D", "score": 2.0},
                    {"doc_id": "A", "score": 1.0},
                ],
                "vector_results": [
                    {"doc_id": "A", "score": 0.9},
                    {"doc_id": "B", "score": 0.8},
                    {"doc_id": "C", "score": 0.7},
                ],
                "fusion_results": [
                    {"doc_id": "B", "score": 0.05},
                    {"doc_id": "A", "score": 0.04},
                    {"doc_id": "D", "score": 0.03},
                    {"doc_id": "C", "score": 0.02},
                ],
            },
        }
    )

    # Patch the providers on src.services (where they now live) and reset the
    # chain singletons on src.rag_pipeline (where retrieval/generation chains
    # are cached). app.py re-exports the providers, so callers using
    # `from src.app import get_bm25_index` are unaffected.
    # Patch the providers where they are *looked up*: rag_pipeline imported
    # them by name at module load, so patching src.services alone is not
    # enough — we must patch the names as seen by rag_pipeline too.
    monkeypatch.setattr(rag_module, "get_hybrid_retriever", lambda: hybrid_retriever)
    monkeypatch.setattr(rag_module, "get_reranker_model", lambda: reranker)
    # Also reset services singletons in case other tests touched them.
    # These are LazySingleton instances (see src/services.py); reset their
    # cached value via monkeypatch so the change auto-reverts after the test
    # instead of permanently clobbering the module-level singleton object.
    monkeypatch.setattr(services_module._hybrid_retriever, "_value", None)
    monkeypatch.setattr(services_module._reranker, "_value", None)

    rag_module._retrieval_chain = None  # reset cached chain

    return rag_module.get_retrieval_chain()


@pytest.mark.asyncio
async def test_chain_returns_six_keys(patched_chain):
    result = await patched_chain.ainvoke("test query")

    for key in ("context", "contexts", "sources", "docs", "rerank_scores", "fusion_metadata"):
        assert key in result, f"missing key: {key}"


@pytest.mark.asyncio
async def test_sources_built_from_qa_content(patched_chain):
    result = await patched_chain.ainvoke("test query")

    # docs_map fixture docs have no "text" metadata, so the question falls
    # back to page_content; combined with "answer" this is short enough to
    # not need truncation.
    assert result["sources"] == [
        "Q: Q A A: ans A",
        "Q: Q C A: ans C",
        "Q: Q D A: ans D",
    ]


def test_format_sources_dedupes_by_qa_content_preserving_order():
    docs = [
        Document(page_content="ignored", metadata={
            "doc_id": "B", "text": "How to reset password?", "answer": "Click forgot password.",
        }),
        Document(page_content="ignored", metadata={
            "doc_id": "D", "text": "What is CarbonM?", "answer": "A carbon management platform.",
        }),
        Document(page_content="ignored", metadata={
            "doc_id": "C", "text": "How to reset password?", "answer": "Click forgot password.",
        }),
    ]

    assert rag_module._format_sources(docs) == [
        "Q: How to reset password? A: Click forgot password.",
        "Q: What is CarbonM? A: A carbon management platform.",
    ]


def test_format_sources_truncates_long_qa_content_to_100_chars():
    long_answer = "重設連結 30 分鐘內有效，過期後請在登入畫面點選「忘記密碼？」重新申請。" * 3
    docs = [Document(page_content="ignored", metadata={
        "doc_id": "faq-01", "text": "重設密碼的信件可以撐多久？", "answer": long_answer,
    })]

    [source] = rag_module._format_sources(docs)

    assert len(source) == 103  # 100 chars + "..."
    assert source.endswith("...")
    assert source.startswith("Q: 重設密碼的信件可以撐多久？ A: ")


def test_format_sources_falls_back_to_doc_id_when_question_or_answer_missing():
    docs = [Document(page_content="", metadata={"doc_id": "faq-042"})]

    assert rag_module._format_sources(docs) == ["faq-042"]


@pytest.mark.asyncio
async def test_context_built_from_answer_metadata(patched_chain):
    result = await patched_chain.ainvoke("test query")

    assert result["context"] == "ans A\n\nans C\n\nans D"
    assert result["contexts"] == ["ans A", "ans C", "ans D"]


@pytest.mark.asyncio
async def test_top3_docs_order(patched_chain):
    result = await patched_chain.ainvoke("test query")

    ids = [d.metadata["doc_id"] for d in result["docs"]]
    assert ids == ["A", "C", "D"]
    assert len(result["docs"]) == 3

    ranks = [entry["rank"] for entry in result["rerank_scores"]]
    assert ranks == [1, 2, 3]
    assert result["rerank_scores"][0]["score"] == "0.9500"


@pytest.mark.asyncio
async def test_fusion_metadata_present(patched_chain):
    result = await patched_chain.ainvoke("test query")

    meta = result["fusion_metadata"]
    for key in ("bm25_results", "vector_results", "fusion_results"):
        assert key in meta, f"missing fusion key: {key}"


# --- Rerank failure falls back to fused order (spec: rerank-stage) ---------------

import logging  # noqa: E402
import threading  # noqa: E402


@pytest.fixture
def fused(patched_chain):
    """The fused candidates the fixture's retriever returns, in fused order."""
    retriever = rag_module.get_hybrid_retriever()
    return retriever.retrieve.return_value["candidates"]


class BoomError(RuntimeError):
    pass


def _failing_reranker(exc):
    reranker = MagicMock()
    reranker.score = MagicMock(side_effect=exc)
    return reranker


@pytest.mark.asyncio
async def test_scorer_error_falls_back_to_fused_order(patched_chain, fused, monkeypatch):
    monkeypatch.setattr(rag_module, "get_reranker_model", lambda: _failing_reranker(BoomError("secret detail")))
    result = await patched_chain.ainvoke("test query")
    assert result["docs"] == fused[:3]
    assert result["rerank_scores"] == []
    assert result["rerank_error"] == "error"
    assert result["fusion_metadata"]["fusion_results"]
    assert result["contexts"] == [d.metadata["answer"] for d in fused[:3]]


@pytest.mark.asyncio
async def test_client_construction_failure_falls_back(patched_chain, fused, monkeypatch):
    def broken():
        raise ValueError("PINECONE_API_KEY not configured")

    monkeypatch.setattr(rag_module, "get_reranker_model", broken)
    result = await patched_chain.ainvoke("test query")
    assert (result["docs"], result["rerank_scores"], result["rerank_error"]) == (fused[:3], [], "error")


@pytest.mark.asyncio
async def test_scorer_timeout_falls_back(patched_chain, fused, monkeypatch):
    release = threading.Event()

    def slow(pairs):
        release.wait(5)
        return [0.9] * len(pairs)

    reranker = MagicMock()
    reranker.score = MagicMock(side_effect=slow)
    monkeypatch.setattr(rag_module, "get_reranker_model", lambda: reranker)
    monkeypatch.setattr(rag_module, "RERANK_TIMEOUT_SECONDS", 0.05)
    try:
        result = await patched_chain.ainvoke("test query")
    finally:
        release.set()
    assert (result["docs"], result["rerank_scores"], result["rerank_error"]) == (fused[:3], [], "timeout")


@pytest.mark.asyncio
async def test_requests_timeout_counts_as_timeout(patched_chain, monkeypatch):
    import requests

    monkeypatch.setattr(rag_module, "get_reranker_model", lambda: _failing_reranker(requests.Timeout("read timed out")))
    result = await patched_chain.ainvoke("test query")
    assert result["rerank_error"] == "timeout"


@pytest.mark.asyncio
async def test_no_candidates_skip_the_reranker(patched_chain, monkeypatch):
    retriever = rag_module.get_hybrid_retriever()
    retriever.retrieve.return_value = {"candidates": [], "fusion_metadata": {"fallback": "none"}}
    called = MagicMock()
    monkeypatch.setattr(rag_module, "get_reranker_model", called)
    result = await patched_chain.ainvoke("test query")
    called.assert_not_called()
    assert result["docs"] == [] and result["rerank_scores"] == [] and result["sources"] == []
    assert result["context"] == "" and result["contexts"] == []
    assert result["fusion_metadata"] == {"fallback": "none"}
    assert "rerank_error" not in result


@pytest.mark.asyncio
async def test_successful_rerank_has_no_error_marker(patched_chain):
    result = await patched_chain.ainvoke("test query")
    assert result["rerank_scores"]
    assert "rerank_error" not in result


@pytest.mark.asyncio
async def test_failure_log_carries_no_content(patched_chain, monkeypatch, caplog):
    monkeypatch.setattr(rag_module, "get_reranker_model", lambda: _failing_reranker(BoomError("secret detail")))
    with caplog.at_level(logging.WARNING):
        await patched_chain.ainvoke("needle query text")
    [record] = [r for r in caplog.records if "rerank" in r.getMessage().lower()]
    message = record.getMessage()
    assert record.levelno == logging.WARNING
    assert record.exc_info is None
    assert "error" in message and "BoomError" in message and "ms" in message
    for leaked in ("needle query text", "secret detail", "Q A", "ans A"):
        assert leaked not in message
