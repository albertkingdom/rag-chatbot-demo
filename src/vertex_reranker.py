"""Hosted rerank client: Vertex AI Ranking API (semantic-ranker-default-005).

Exposes the same ``score(pairs) -> list[float]`` method as the former Pinecone
client, so ``rerank_candidates`` and the retrieval chain inject it unchanged
(spec: rerank-stage, "Rerank scorer is injected as a callable").

Authentication uses Google application default credentials: the Cloud Run
runtime service account in production, the developer's ``gcloud auth
application-default login`` credentials locally. Nothing touches the network
until the first ``score`` call, and a failed request is never retried: the
session is built with ``max_refresh_attempts=0`` so a 401 is not answered by
refreshing the token and resending (the token is still refreshed before a
request when it is about to expire). The retrieval chain turns any error or
timeout into a fallback to the fused order.
"""

import threading
from typing import Callable, Optional

import google.auth
from google.auth.transport.requests import AuthorizedSession
from google.oauth2.credentials import Credentials as _UserCredentials

from .hosted_reranker import RerankError

MODEL = "semantic-ranker-default-005"
SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]
RANK_URL = (
    "https://discoveryengine.googleapis.com/v1/projects/{project}/locations/global/"
    "rankingConfigs/default_ranking_config:rank"
)
# Vertex accepts up to 1,000 records per request; 100 is far above what the
# retrieval chain sends (two passages for each of FUSION_TOP_M candidates).
MAX_DOCUMENTS = 100
# The client-side HTTP timeout is this much longer than the rerank timeout, so
# a worker thread abandoned by the retrieval chain's timeout still ends soon.
HTTP_TIMEOUT_MARGIN_SECONDS = 1.0


class VertexReranker:
    def __init__(
        self,
        project_id: str,
        timeout: float,
        credentials_factory: Callable = google.auth.default,
        session_factory: Callable = AuthorizedSession,
        user_credentials_type: type = _UserCredentials,
    ):
        self._project_id = project_id
        self._url = RANK_URL.format(project=project_id)
        self._http_timeout = timeout + HTTP_TIMEOUT_MARGIN_SECONDS
        self._credentials_factory = credentials_factory
        self._session_factory = session_factory
        self._user_credentials_type = user_credentials_type
        self._session: Optional[object] = None
        self._lock = threading.Lock()

    def _ensure_session(self):
        """Fetch credentials and build the session once, on first use."""
        if self._session is not None:
            return self._session
        with self._lock:
            if self._session is None:
                credentials, _ = self._credentials_factory(scopes=SCOPES)
                # End-user ADC (local development) needs a quota project for the
                # call to be accepted and billed to this project. Service account
                # credentials (Cloud Run) do not, and sending one would also
                # require serviceusage.services.use, which the runtime account
                # does not have.
                if (
                    isinstance(credentials, self._user_credentials_type)
                    and not getattr(credentials, "quota_project_id", None)
                ):
                    credentials = credentials.with_quota_project(self._project_id)
                self._session = self._session_factory(credentials, max_refresh_attempts=0)
        return self._session

    def score(self, pairs: list[tuple[str, str]]) -> list[float]:
        """Score ``(query, passage)`` pairs; one score per pair, in input order.

        All pairs must share one query (one request carries one query) and
        there may be at most ``MAX_DOCUMENTS`` of them; otherwise ``ValueError``
        is raised before any request is sent. A record returned without a
        score counts as 0.
        """
        if not pairs:
            return []
        queries = {query for query, _ in pairs}
        if len(queries) != 1:
            raise ValueError("all pairs passed to one rerank call must share the same query")
        if len(pairs) > MAX_DOCUMENTS:
            raise ValueError(f"{len(pairs)} passages exceed the per-request limit of {MAX_DOCUMENTS}")

        session = self._ensure_session()
        resp = session.post(
            self._url,
            json={
                "model": MODEL,
                "query": pairs[0][0],
                "records": [{"id": str(i), "content": passage} for i, (_, passage) in enumerate(pairs)],
            },
            timeout=self._http_timeout,
        )
        if resp.status_code != 200:
            # Status only: the body could echo request content.
            raise RerankError(f"rerank request failed with HTTP {resp.status_code}")

        scores: list[Optional[float]] = [None] * len(pairs)
        for record in resp.json().get("records", []):
            index = int(record["id"])
            if 0 <= index < len(scores):
                scores[index] = float(record.get("score", 0.0))
        if any(s is None for s in scores):
            raise RerankError(f"rerank response scored fewer than {len(pairs)} passages")
        return scores
