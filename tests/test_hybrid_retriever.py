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
def retriever(bm25_index, vector_store):
    return HybridRetriever(bm25_index, vector_store)


# --- 3.1 Instantiation -----------------------------------------------


class TestInstantiation:
    def test_can_be_instantiated_with_stubs(self, bm25_index, vector_store):
        r = HybridRetriever(bm25_index, vector_store)
        assert r is not None
        assert r.bm25_index is bm25_index
        assert r.vector_store is vector_store
        assert not hasattr(r, "reranker_scorer")


# --- 3.2 RRF fusion ---------------------------------------------------


class TestRRFFusion:
    @pytest.mark.asyncio
    async def test_rrf_fusion_is_deterministic(self, retriever):
        result1 = await retriever.retrieve("test", rrf_k=60)
        result2 = await retriever.retrieve("test", rrf_k=60)
        ids1 = [d.metadata["doc_id"] for d in result1["candidates"]]
        ids2 = [d.metadata["doc_id"] for d in result2["candidates"]]
        assert ids1 == ids2

    @pytest.mark.asyncio
    async def test_fused_list_truncated_to_cap(self, retriever):
        result = await retriever.retrieve("test")
        leaked_fused = result["fusion_metadata"]["fusion_results"]
        assert len(leaked_fused) <= 10


# --- 4.2 / 4.3 doc_id sourcing and fusion alignment -------------------


class TestDocIdSourcing:
    @pytest.mark.asyncio
    async def test_vector_path_uses_metadata_doc_id(self, retriever):
        scored, _ = await retriever._vector_search("test", top_n=3)
        assert [doc_id for doc_id, _ in scored] == ["A", "B", "C"]

    @pytest.mark.asyncio
    async def test_vector_path_skips_docs_without_doc_id(self, bm25_index):
        vs = MagicMock()
        vs.asimilarity_search_with_score = AsyncMock(
            return_value=[
                (Document(page_content="has id", metadata={"doc_id": "A"}), 0.9),
                (Document(page_content="no id", metadata={}), 0.8),
            ]
        )
        r = HybridRetriever(bm25_index, vs)
        scored, lookup = await r._vector_search("test", top_n=2)
        assert [doc_id for doc_id, _ in scored] == ["A"]
        assert "A" in lookup
        assert lookup["A"].page_content == "has id"

    @pytest.mark.asyncio
    async def test_vector_search_returns_doc_lookup(self, retriever):
        """Task 1.1: _vector_search returns a doc_id → Document lookup dict."""
        scored, lookup = await retriever._vector_search("test", top_n=3)
        assert set(lookup.keys()) == {"A", "B", "C"}
        for doc_id, doc in lookup.items():
            assert doc.metadata["doc_id"] == doc_id

    @pytest.mark.asyncio
    async def test_overlapping_doc_fuses_into_single_entry(self, retriever):
        # bm25: B(rank1), D(rank2), A(rank3) ; vector: A(rank1), B(rank2), C(rank3)
        result = await retriever.retrieve("test", rrf_k=60)
        fused = result["fusion_metadata"]["fusion_results"]
        b_entries = [f for f in fused if f["doc_id"] == "B"]
        assert len(b_entries) == 1
        # B = bm25 rank1 (1/61) + vector rank2 (1/62); fusion scores are rounded to 6dp
        assert b_entries[0]["score"] == pytest.approx(1 / 61 + 1 / 62, abs=1e-6)


# --- 3.3 Fallback -----------------------------------------------------


class TestFallback:
    @pytest.mark.asyncio
    async def test_fallback_when_bm25_not_built(self, vector_store, sample_docs_map):
        idx = MagicMock()
        idx.is_built = False
        idx.corpus_size = 0
        idx.search = MagicMock(side_effect=RuntimeError("BM25 index not built"))
        idx.get_doc = MagicMock(side_effect=lambda d: sample_docs_map.get(d))
        r = HybridRetriever(idx, vector_store)
        result = await r.retrieve("test")
        assert result["fusion_metadata"].get("fallback") == "vector_only"
        assert len(result["candidates"]) > 0

    @pytest.mark.asyncio
    async def test_fallback_when_corpus_empty(self, vector_store, sample_docs_map):
        idx = MagicMock()
        idx.is_built = True
        idx.corpus_size = 0
        idx.search = MagicMock(return_value=[])
        idx.get_doc = MagicMock(side_effect=lambda d: sample_docs_map.get(d))
        r = HybridRetriever(idx, vector_store)
        result = await r.retrieve("test")
        assert result["fusion_metadata"].get("fallback") is not None


# --- 3.4 Observability ------------------------------------------------


class TestObservability:
    @pytest.mark.asyncio
    async def test_trace_metadata_contains_all_stages(self, retriever):
        result = await retriever.retrieve("test")
        meta = result["fusion_metadata"]
        for key in ("bm25_results", "vector_results", "fusion_results"):
            assert key in meta, f"missing key: {key}"
        assert "rerank_scores" not in meta

    @pytest.mark.asyncio
    async def test_return_shape_has_candidates_only(self, retriever):
        result = await retriever.retrieve("test")
        assert "candidates" in result
        for removed_key in ("docs", "rerank_scores", "context", "contexts"):
            assert removed_key not in result, f"unexpected key: {removed_key}"


# --- Candidate assembly (optimize-gather-candidates) --------------------


class TestCandidateAssembly:
    @pytest.mark.asyncio
    async def test_vector_only_candidate_resolved_from_lookup(self):
        """Task 2.1: vector-only doc resolved without additional query."""
        doc_c = Document(page_content="Doc C content", metadata={"doc_id": "C"})

        idx = MagicMock()
        idx.is_built = True
        idx.corpus_size = 1
        idx.search = MagicMock(return_value=[("A", 3.0)])
        idx.get_doc = MagicMock(side_effect=lambda d: None)

        vs = MagicMock()
        vs.asimilarity_search_with_score = AsyncMock(
            return_value=[(doc_c, 0.9)]
        )

        r = HybridRetriever(idx, vs)
        result = await r.retrieve("test", rrf_k=60)

        assert any(d.metadata["doc_id"] == "C" for d in result["candidates"])
        vs.similarity_search.assert_not_called()

    @pytest.mark.asyncio
    async def test_candidate_not_found_in_either_source_is_skipped(self):
        """Task 2.2: missing doc_id silently skipped."""
        idx = MagicMock()
        idx.is_built = True
        idx.corpus_size = 1
        idx.search = MagicMock(return_value=[("MISSING", 3.0)])
        idx.get_doc = MagicMock(return_value=None)

        vs = MagicMock()
        vs.asimilarity_search_with_score = AsyncMock(return_value=[])

        r = HybridRetriever(idx, vs)
        result = await r.retrieve("test", rrf_k=60)

        assert len(result["candidates"]) == 0
        vs.similarity_search.assert_not_called()
