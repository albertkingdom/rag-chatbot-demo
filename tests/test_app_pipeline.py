"""Pipeline test for the reassembled retrieval chain.

Verifies that ``get_retrieval_chain().ainvoke(query)`` returns the full
five-key dict and that ``context`` is assembled from ``metadata["answer"]``
(preserving the existing generation contract).
"""
import pytest
from unittest.mock import MagicMock, AsyncMock

from langchain_core.documents import Document

import src.app as app_module


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

    monkeypatch.setattr(app_module, "get_bm25_index", lambda: bm25)
    monkeypatch.setattr(app_module, "get_vectorstore", lambda: vector_store)
    monkeypatch.setattr(app_module, "get_reranker_model", lambda: reranker)

    monkeypatch.setattr(app_module, "_hybrid_retriever", None)
    monkeypatch.setattr(app_module, "_retrieval_chain", None)

    return app_module.get_retrieval_chain()


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
