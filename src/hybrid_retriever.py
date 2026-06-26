"""Hybrid retriever combining BM25 keyword search and dense vector retrieval.

The two retrieval paths run in parallel (``asyncio.gather``); their
rankings are fused via Reciprocal Rank Fusion (RRF) and the top-M
fused candidates are handed to the existing BGE Reranker.
"""

import asyncio
from typing import Any, Callable

from langchain_core.documents import Document

from src.config import FUSION_TOP_M, BM25_TOP_N, VECTOR_TOP_N, RRF_K


class HybridRetriever:
    """Orchestrates BM25 + vector retrieval, RRF fusion, and reranking."""

    def __init__(
        self,
        bm25_index,
        vector_store,
        reranker_scorer: Callable[[list[tuple[str, str]]], list[float]],
    ):
        """
        Args:
            bm25_index: a :class:`BM25Index` instance (or compatible).
            vector_store: a LangChain vector store with ``similarity_search``
                (sync) and ``asimilarity_search`` (async) semantics.
            reranker_scorer: callable receiving a list of ``(query, text)``
                pairs and returning a list of float relevance scores
                (the BGE Reranker ``score`` function).
        """
        self.bm25_index = bm25_index
        self.vector_store = vector_store
        self.reranker_scorer = reranker_scorer

    # ------------------------------------------------------------------
    # RRF
    # ------------------------------------------------------------------

    @staticmethod
    def _rrf_fuse(
        bm25_results: list[tuple[str, float]],
        vector_results: list[tuple[str, float]],
        rrf_k: int,
    ) -> list[tuple[str, float]]:
        """Return ``(doc_id, fused_score)`` sorted by descending fused score.

        Ranks are 1-indexed. A document appearing in only one list still
        receives a contribution from that list.
        """
        scores: dict[str, float] = {}

        for rank, (doc_id, _) in enumerate(bm25_results, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (rrf_k + rank)

        for rank, (doc_id, _) in enumerate(vector_results, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (rrf_k + rank)

        return sorted(scores.items(), key=lambda x: x[1], reverse=True)

    # ------------------------------------------------------------------
    # Vector search wrapper (sync→async bridge)
    # ------------------------------------------------------------------

    async def _vector_search(self, query: str, top_n: int) -> list[tuple[str, float]]:
        """Return ``(doc_id, score)`` from the vector store.

        Falls back to ``similarity_search_with_score`` when
        ``asimilarity_search_with_score`` is unavailable.
        """
        try:
            results = await self.vector_store.asimilarity_search_with_score(query, k=top_n)
        except AttributeError:
            results = self.vector_store.similarity_search_with_score(query, k=top_n)

        scored: list[tuple[str, float]] = []
        for doc, score in results:
            doc_id = doc.metadata.get("doc_id") or f"qa_{abs(hash(doc.page_content))}"
            scored.append((doc_id, float(score)))
        return scored

    # ------------------------------------------------------------------
    # Main entry
    # ------------------------------------------------------------------

    async def retrieve(
        self,
        query: str,
        vector_top_n: int = VECTOR_TOP_N,
        bm25_top_n: int = BM25_TOP_N,
        rrf_k: int = RRF_K,
    ) -> dict[str, Any]:
        """Run hybrid retrieval and return a result dict.

        Returns:
            ``{"docs": list[Document]          # top 3 after reranking
               "rerank_scores": list     # per-doc reranker scores
               "fusion_metadata": dict  # per-stage observability
              }``
        """
        bm25_results: list[tuple[str, float]] = []
        bm25_error: str | None = None

        # --- BM25 (may raise / be empty) --------------------------
        try:
            if not self.bm25_index.is_built or self.bm25_index.corpus_size == 0:
                bm25_error = "bm25_unavailable"
            else:
                bm25_results = self.bm25_index.search(query, top_n=bm25_top_n)
                if not bm25_results:
                    bm25_error = "bm25_empty_results"
        except RuntimeError as exc:
            bm25_error = f"bm25_error: {exc}"

        # --- Vector (async) --------------------------------------
        vector_results = await self._vector_search(query, top_n=vector_top_n)

        # --- Fusion ----------------------------------------------
        fallback = None
        if bm25_error:
            fallback = "vector_only"
            fused = [(doc_id, 1.0 / (rrf_k + rank)) for rank, (doc_id, _) in enumerate(vector_results, start=1)]
        else:
            fused = self._rrf_fuse(bm25_results, vector_results, rrf_k)

        fused = fused[:FUSION_TOP_M]  # NOTE truncate to reranker cap regardless of fallback

        # --- Gather candidate Documents --------------------------
        candidates: list[Document] = []
        for doc_id, _ in fused:
            doc = self.bm25_index.get_doc(doc_id)
            if doc is None:
                doc = self._find_in_vector_docs(doc_id, query, vector_top_n)
            if doc is not None:
                candidates.append(doc)

        # --- Rerank → Top 3 --------------------------------------
        rerank_scores_raw: list[float] = []
        if candidates:
            pairs = [(query, d.page_content) for d in candidates]
            rerank_scores_raw = list(self.reranker_scorer(pairs))

        reranked = sorted(zip(candidates, rerank_scores_raw), key=lambda x: x[1], reverse=True)[:3]
        top_docs = [doc for doc, _ in reranked]

        rerank_scores = [
            {
                "rank": i + 1,
                "score": f"{score:.4f}",
                "content": doc.page_content[:100] + "...",
            }
            for i, (doc, score) in enumerate(reranked)
        ]

        # --- Observability metadata ------------------------------
        fusion_metadata: dict[str, Any] = {
            "bm25_results": [
                {"doc_id": doc_id, "score": round(score, 4)}
                for doc_id, score in bm25_results
            ],
            "vector_results": [
                {"doc_id": doc_id, "score": round(score, 4)}
                for doc_id, score in vector_results
            ],
            "fusion_results": [
                {"doc_id": doc_id, "score": round(score, 6)}
                for doc_id, score in fused
            ],
            "rerank_scores": rerank_scores,
        }
        if fallback:
            fusion_metadata["fallback"] = fallback

        # --- Compose context string (same contract as old chain) -
        context = "\n\n".join(doc.page_content for doc in top_docs)
        contexts = [doc.page_content for doc in top_docs]

        return {
            "context": context,
            "contexts": contexts,
            "docs": top_docs,
            "rerank_scores": rerank_scores,
            "fusion_metadata": fusion_metadata,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_in_vector_docs(self, doc_id: str, query: str, top_n: int):
        """Best-effort lookup for a doc id that BM25Index didn't have.

        This happens when a document came from the vector store only;
        we re-issue a small search and match by id.
        """
        try:
            docs = self.vector_store.similarity_search(query, k=top_n)
        except Exception:
            return None
        for doc in docs:
            if doc.metadata.get("doc_id") == doc_id:
                return doc
            if f"qa_{abs(hash(doc.page_content))}" == doc_id:
                return doc
        return None