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
async def test_chain_returns_five_keys(patched_chain):
    result = await patched_chain.ainvoke("test query")

    for key in ("context", "contexts", "docs", "rerank_scores", "fusion_metadata"):
        assert key in result, f"missing key: {key}"


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
