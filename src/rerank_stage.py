"""Standalone rerank stage.

Receives the top-M fused candidates from :class:`HybridRetriever` and
narrows them to the Top 3 via a BGE Reranker ``score`` callable. This
stage is intentionally decoupled from the retriever so that retrieval
and reranking can be observed and tested independently.
"""

from typing import Callable, Optional

from langchain_core.documents import Document

RERANK_TOP_K = 3


def rerank_candidates(
    query: str,
    candidates: list[Document],
    scorer: Callable[[list[tuple[str, str]]], list[float]],
    passages_for: Optional[Callable[[Document], list[str]]] = None,
) -> dict:
    """Rerank ``candidates`` for ``query`` and return the Top 3.

    Args:
        query: the rewritten user query.
        candidates: top-M fused ``Document`` objects from the retriever.
        scorer: callable receiving ``[(query, passage), ...]`` and
            returning a list of float relevance scores (e.g. the bound
            ``score`` method of the BGE Reranker singleton). It is called
            exactly once, with the passages of every candidate.
        passages_for: optional callable giving the passages to score for a
            candidate. Without it, or when it gives none, the candidate is
            scored on its ``page_content``. A candidate's score is the highest
            score among its passages.

    Returns:
        ``{"docs": list[Document], "rerank_scores": list}``

        When ``candidates`` is empty, returns empty values for every key
        without invoking ``scorer``. ``context``/``contexts`` are NOT
        assembled here; the retrieval chain builds them from document
        metadata (e.g. the ``answer`` field) so this stage stays a generic
        scoring/ranking component.
    """
    if not candidates:
        return {"docs": [], "rerank_scores": []}

    passages_per_doc = [
        (passages_for(d) if passages_for else None) or [d.page_content] for d in candidates
    ]
    pairs = [(query, passage) for passages in passages_per_doc for passage in passages]
    scores = list(scorer(pairs))
    if len(scores) != len(pairs):
        # A silent zip() truncation would pair scores with the wrong documents.
        raise ValueError(f"scorer returned {len(scores)} scores for {len(pairs)} pairs")

    doc_scores, position = [], 0
    for passages in passages_per_doc:
        doc_scores.append(max(scores[position:position + len(passages)]))
        position += len(passages)

    reranked = sorted(zip(candidates, doc_scores), key=lambda x: x[1], reverse=True)[:RERANK_TOP_K]
    top_docs = [doc for doc, _ in reranked]

    rerank_scores = [
        {
            "rank": i + 1,
            "score": f"{score:.4f}",
            "content": doc.page_content[:100] + "...",
        }
        for i, (doc, score) in enumerate(reranked)
    ]

    return {
        "docs": top_docs,
        "rerank_scores": rerank_scores,
    }
