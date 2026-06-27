"""In-process BM25 index for hybrid search.

Wraps rank_bm25.BM25Okapi to provide a lightweight keyword retrieval
engine that runs alongside the Pinecone vector store. The corpus is
persisted as JSON so cold starts avoid re-tokenizing the entire knowledge
base.
"""

import json
import os
from pathlib import Path
from typing import Optional

from langchain_core.documents import Document


class BM25Index:
    """Manages a BM25Okapi index over Q&A Documents.

    The index is keyed by ``doc_id`` (derived from ``Document.metadata``
    or auto-generated) and serialized to ``models/bm25_corpus.json``.
    """

    def __init__(self, persist_path: str = "models/bm25_corpus.json"):
        self.persist_path = Path(persist_path)
        self._bm25 = None
        self._corpus: dict[str, Document] = {}
        self._tokenized_corpus: list[list[str]] = []
        self._doc_ids: list[str] = []
        self._built = False
        self._load()

    # ------------------------------------------------------------------
    # Tokenizer (shared by build and search)
    # ------------------------------------------------------------------

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        return text.split()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self):
        """Attempt to load the persisted corpus on cold start."""
        if not self.persist_path.exists():
            return
        try:
            with self.persist_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            self._doc_ids = data["doc_ids"]
            self._tokenized_corpus = data["tokenized_corpus"]
            self._corpus = {
                doc_id: Document(page_content=v["page_content"], metadata=v["metadata"])
                for doc_id, v in data["corpus"].items()
            }
            from rank_bm25 import BM25Okapi

            self._bm25 = BM25Okapi(self._tokenized_corpus)
            self._built = True
            print(
                f"[BM25Index] Loaded persisted index ({len(self._doc_ids)} docs) from {self.persist_path}"
            )
        except Exception as e:
            print(f"[BM25Index] Warning: failed to load persisted file ({e}). Starting unbuilt.")
            self._bm25 = None
            self._corpus = {}
            self._tokenized_corpus = []
            self._doc_ids = []
            self._built = False

    def _persist(self):
        """Write corpus + tokenized structure to disk."""
        self.persist_path.parent.mkdir(parents=True, exist_ok=True)
        serializable_corpus = {
            doc_id: {
                "page_content": doc.page_content,
                "metadata": doc.metadata,
            }
            for doc_id, doc in self._corpus.items()
        }
        with self.persist_path.open("w", encoding="utf-8") as f:
            json.dump(
                {
                    "doc_ids": self._doc_ids,
                    "tokenized_corpus": self._tokenized_corpus,
                    "corpus": serializable_corpus,
                },
                f,
                ensure_ascii=False,
            )

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def build_from_documents(self, docs: list[Document]) -> None:
        """Build (or rebuild) the BM25Okapi index from ``docs``.

        Each document's ``doc_id`` is taken from ``metadata['doc_id']``.
        A document lacking ``metadata['doc_id']`` raises ``ValueError`` —
        the index never falls back to hashing ``page_content``, which would
        produce process-unstable ids.

        If ``docs`` is empty the index is marked not-built and no
        persistence file is written.
        """
        if not docs:
            self._bm25 = None
            self._corpus = {}
            self._tokenized_corpus = []
            self._doc_ids = []
            self._built = False
            print("[BM25Index] build_from_documents called with empty list; index stays unbuilt.")
            return

        from rank_bm25 import BM25Okapi

        self._corpus = {}
        self._tokenized_corpus = []
        self._doc_ids = []

        for i, doc in enumerate(docs):
            doc_id = doc.metadata.get("doc_id")
            if not doc_id:
                raise ValueError(
                    f"Document at index {i} is missing metadata['doc_id']; "
                    "BM25Index requires a stable doc_id and does not hash page_content."
                )
            self._corpus[doc_id] = doc
            self._doc_ids.append(doc_id)
            self._tokenized_corpus.append(self._tokenize(doc.page_content))

        self._bm25 = BM25Okapi(self._tokenized_corpus)
        self._built = True
        self._persist()

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(self, query: str, top_n: int) -> list[tuple[str, float]]:
        """Return ``(doc_id, bm25_score)`` sorted by descending score.

        Raises ``RuntimeError`` when the index has not been built.
        """
        if not self._built or self._bm25 is None:
            raise RuntimeError("BM25 index not built")

        tokenized_query = self._tokenize(query)
        scores = self._bm25.get_scores(tokenized_query)
        ranked = sorted(
            ((doc_id, float(scores[i])) for i, doc_id in enumerate(self._doc_ids)),
            key=lambda x: x[1],
            reverse=True,
        )
        return ranked[:top_n]

    #-------------------------------------------------------------------
    # Lookup
    #-------------------------------------------------------------------

    def get_doc(self, doc_id: str) -> Optional[Document]:
        return self._corpus.get(doc_id)

    # ------------------------------------------------------------------
    # Status helpers
    # ------------------------------------------------------------------

    @property
    def is_built(self) -> bool:
        return self._built

    @property
    def corpus_size(self) -> int:
        return len(self._corpus)