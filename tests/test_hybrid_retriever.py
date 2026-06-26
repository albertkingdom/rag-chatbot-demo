"""Unit tests for HybridRetriever. Covers tasks 3.1-3.4."""
import pytest
from unittest.mock import MagicMock, AsyncMock

from langchain_core.documents import Document

from src.hybrid_retriever import HybridRetriever


# --- Fixtures ---------------------------------------------------------


@pytest.fixture
def sample_docs_map():
    return {
        "A": Document(page_content="Doc A content", metadata={"doc_id": "A"}),
        "B": Document(page_content="Doc B content", metadata={"doc_id": "B"}),
        "C": Document(page_content="Doc C content", metadata={"doc_id": "C"}),
        "D": Document(page_content="Doc D content", metadata={"doc_id": "D"}),
    }


@pytest.fixture
def bm25_index(sample_docs_map):
    idx = MagicMock()
    idx.is_built = True
    idx.corpus_size = 3
    idx.search = MagicMock(return_value=[("B", 3.0), ("D", 2.0), ("A", 1.0)])
    idx.get_doc = MagicMock(side_effect=lambda doc_id: sample_docs_map.get(doc_id))
    return idx


@pytest.fixture
def vector_store():
    vs = MagicMock()
    vs.asimilarity_search_with_score = AsyncMock(
        return_value=[
            (Document(page_content="Doc A content", metadata={"doc_id": "A"}), 0.9),
            (Document(page_content="Doc B content", metadata={"doc_id": "B"}), 0.8),
            (Document(page_content="Doc C content", metadata={"doc_id": "C"}), 0.7),
        ]
    )
    return vs


@pytest.fixture
def reranker_scorer():
    return MagicMock(return_value=[0.95, 0.80, 0.70, 0.60])


@pytest.fixture
def retriever(bm25_index, vector_store, reranker_scorer):
    return HybridRetriever(bm25_index, vector_store, reranker_scorer)


# --- 3.1 Instantiation -----------------------------------------------


class TestInstantiation:
    def test_can_be_instantiated_with_stubs(self, bm25_index, vector_store, reranker_scorer):
        r = HybridRetriever(bm25_index, vector_store, reranker_scorer)
        assert r is not None
        assert r.bm25_index is bm25_index
        assert r.vector_store is vector_store
        assert r.reranker_scorer is reranker_scorer


# --- 3.2 RRF fusion + reranker ---------------------------------------


class TestRRFFusion:
    @pytest.mark.asyncio
    async def test_rrf_fusion_is_deterministic(self, retriever):
        result1 = await retriever.retrieve("test", rrf_k=60)
        result2 = await retriever.retrieve("test", rrf_k=60)
        ids1 = [d.metadata["doc_id"] for d in result1["docs"]]
        ids2 = [d.metadata["doc_id"] for d in result2["docs"]]
        assert ids1 == ids2

    @pytest.mark.asyncio
    async def test_fused_list_truncated_to_cap(self, retriever):
        result = await retriever.retrieve("test")
        leaked_fused = result["fusion_metadata"]["fusion_results"]
        assert len(leaked_fused) <= 10


# --- 3.3 Fallback -----------------------------------------------------


class TestFallback:
    @pytest.mark.asyncio
    async def test_fallback_when_bm25_not_built(self, vector_store, reranker_scorer, sample_docs_map):
        idx = MagicMock()
        idx.is_built = False
        idx.corpus_size = 0
        idx.search = MagicMock(side_effect=RuntimeError("BM25 index not built"))
        idx.get_doc = MagicMock(side_effect=lambda d: sample_docs_map.get(d))
        r = HybridRetriever(idx, vector_store, reranker_scorer)
        result = await r.retrieve("test")
        assert result["fusion_metadata"].get("fallback") == "vector_only"
        assert len(result["docs"]) > 0

    @pytest.mark.asyncio
    async def test_fallback_when_corpus_empty(self, vector_store, reranker_scorer, sample_docs_map):
        idx = MagicMock()
        idx.is_built = True
        idx.corpus_size = 0
        idx.search = MagicMock(return_value=[])
        idx.get_doc = MagicMock(side_effect=lambda d: sample_docs_map.get(d))
        r = HybridRetriever(idx, vector_store, reranker_scorer)
        result = await r.retrieve("test")
        assert result["fusion_metadata"].get("fallback") is not None


# --- 3.4 Observability ------------------------------------------------


class TestObservability:
    @pytest.mark.asyncio
    async def test_trace_metadata_contains_all_stages(self, retriever):
        result = await retriever.retrieve("test")
        meta = result["fusion_metadata"]
        for key in ("bm25_results", "vector_results", "fusion_results", "rerank_scores"):
            assert key in meta, f"missing key: {key}"