"""Hosted rerank client: BAAI/bge-reranker-v2-m3 on Pinecone Inference.

Exposes the same ``score(pairs) -> list[float]`` method the in-process
cross-encoder had, so ``rerank_candidates`` and the retrieval chain inject it
unchanged. It calls the Pinecone rerank REST endpoint directly with
``requests`` so the HTTP timeout is explicit and a failed request is never
retried; the retrieval chain turns any error or timeout into a fallback.
No model weights are loaded into the service process.
"""

import requests

RERANK_URL = "https://api.pinecone.io/rerank"
API_VERSION = "2026-07"
MODEL = "bge-reranker-v2-m3"
# Pinecone accepts at most this many documents per rerank request for this model.
MAX_DOCUMENTS = 100
# The client-side HTTP timeout is this much longer than the rerank timeout, so
# a worker thread abandoned by the retrieval chain's timeout still ends soon.
HTTP_TIMEOUT_MARGIN_SECONDS = 1.0


class RerankError(RuntimeError):
    """The hosted reranker returned an error status or an incomplete result."""


class PineconeReranker:
    def __init__(self, api_key: str, timeout: float):
        self._api_key = api_key
        self._http_timeout = timeout + HTTP_TIMEOUT_MARGIN_SECONDS

    def score(self, pairs: list[tuple[str, str]]) -> list[float]:
        """Score ``(query, passage)`` pairs; one score per pair, in input order.

        All pairs must share one query (one request carries one query) and
        there may be at most ``MAX_DOCUMENTS`` of them; otherwise ``ValueError``
        is raised before any request is sent.
        """
        if not pairs:
            return []
        queries = {query for query, _ in pairs}
        if len(queries) != 1:
            raise ValueError("all pairs passed to one rerank call must share the same query")
        if len(pairs) > MAX_DOCUMENTS:
            raise ValueError(f"{len(pairs)} passages exceed the per-request limit of {MAX_DOCUMENTS}")

        query = pairs[0][0]
        resp = requests.post(
            RERANK_URL,
            headers={
                "Api-Key": self._api_key,
                "X-Pinecone-Api-Version": API_VERSION,
                "Content-Type": "application/json",
            },
            json={
                "model": MODEL,
                "query": query,
                "documents": [{"text": passage} for _, passage in pairs],
                "top_n": len(pairs),
                "return_documents": False,
                "parameters": {"truncate": "END"},
            },
            timeout=self._http_timeout,
        )
        if resp.status_code != 200:
            # Status only: the body could echo request content.
            raise RerankError(f"rerank request failed with HTTP {resp.status_code}")

        scores = [None] * len(pairs)
        for item in resp.json().get("data", []):
            index = int(item["index"])
            if 0 <= index < len(scores):
                scores[index] = float(item["score"])
        if any(s is None for s in scores):
            raise RerankError(f"rerank response scored fewer than {len(pairs)} passages")
        return scores
