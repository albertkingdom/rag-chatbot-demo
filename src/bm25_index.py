"""In-process BM25 index for hybrid search.

Wraps rank_bm25.BM25Okapi to provide a lightweight keyword retrieval
engine that runs alongside the Pinecone vector store. The corpus is
persisted as JSON so cold starts avoid re-tokenizing the entire knowledge
base.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from langchain_core.documents import Document

# Name of the pointer file that names the current version file.
_POINTER_NAME = "bm25_current.txt"
# Prefix/suffix for write-once version files: bm25_corpus_<version>.json
_VERSION_PREFIX = "bm25_corpus_"
_VERSION_SUFFIX = ".json"


class BM25Index:
    """Manages a BM25Okapi index over Q&A Documents.

    The index is keyed by ``doc_id`` (from ``Document.metadata``) and
    persisted under ``index_dir`` using a write-once version file
    (``bm25_corpus_<version>.json``) plus an atomically updated pointer
    file (``bm25_current.txt``) that names the current version. This lets
    the Cloud Run Service and Job share the index over GCS without
    partial-read races, and lets the Service reload lazily after a sync.
    """

    def __init__(self, index_dir: Optional[str] = None):
        if index_dir is None:
            # Import here to avoid a hard import-time dependency for callers
            # that construct BM25Index with an explicit directory.
            from .config import BM25_INDEX_DIR, BM25_VERSION_RETENTION

            index_dir = BM25_INDEX_DIR
            self._retention = BM25_VERSION_RETENTION
        else:
            from .config import BM25_VERSION_RETENTION

            self._retention = BM25_VERSION_RETENTION
        self.index_dir = Path(index_dir)
        self.pointer_path = self.index_dir / _POINTER_NAME
        self._bm25 = None
        self._corpus: dict[str, Document] = {}
        self._tokenized_corpus: list[list[str]] = []
        self._doc_ids: list[str] = []
        self._built = False
        # Version (file name) currently loaded into memory, for staleness checks.
        self._loaded_version: Optional[str] = None
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

    def _read_pointer(self) -> Optional[str]:
        """Return the current version file name from the pointer, or None."""
        if not self.pointer_path.exists():
            return None
        try:
            name = self.pointer_path.read_text(encoding="utf-8").strip()
            return name or None
        except Exception as e:
            print(f"[BM25Index] Warning: failed to read pointer ({e}).")
            return None

    def _reset_unbuilt(self):
        self._bm25 = None
        self._corpus = {}
        self._tokenized_corpus = []
        self._doc_ids = []
        self._built = False
        self._loaded_version = None

    def _load(self):
        """Load the corpus named by the pointer file (cold start / reload)."""
        version = self._read_pointer()
        if version is None:
            self._reset_unbuilt()
            return
        version_path = self.index_dir / version
        if not version_path.exists():
            print(f"[BM25Index] Warning: pointer names missing file {version}. Starting unbuilt.")
            self._reset_unbuilt()
            return
        try:
            with version_path.open("r", encoding="utf-8") as f:
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
            self._loaded_version = version
            print(
                f"[BM25Index] Loaded persisted index ({len(self._doc_ids)} docs) from {version_path}"
            )
        except Exception as e:
            print(f"[BM25Index] Warning: failed to load persisted file ({e}). Starting unbuilt.")
            self._reset_unbuilt()

    def reload_if_stale(self) -> bool:
        """Reload the index if the pointer names a different version than loaded.

        Returns True if a reload happened, False otherwise. This lets the
        Service pick up a new index written by the sync Job without a restart.
        """
        version = self._read_pointer()
        if version is None:
            return False
        if version == self._loaded_version:
            return False
        self._load()
        return True

    @staticmethod
    def _new_version_name() -> str:
        # UTC timestamp with microseconds: sorts lexicographically by recency.
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        return f"{_VERSION_PREFIX}{stamp}{_VERSION_SUFFIX}"

    def _persist(self):
        """Write a new write-once version file, then atomically flip the pointer."""
        self.index_dir.mkdir(parents=True, exist_ok=True)
        serializable_corpus = {
            doc_id: {
                "page_content": doc.page_content,
                "metadata": doc.metadata,
            }
            for doc_id, doc in self._corpus.items()
        }
        version_name = self._new_version_name()
        version_path = self.index_dir / version_name
        # Write the version file in full before anyone can be pointed at it.
        with version_path.open("w", encoding="utf-8") as f:
            json.dump(
                {
                    "doc_ids": self._doc_ids,
                    "tokenized_corpus": self._tokenized_corpus,
                    "corpus": serializable_corpus,
                },
                f,
                ensure_ascii=False,
            )
        # Atomically update the pointer (temp file + os.replace).
        tmp_pointer = self.pointer_path.with_suffix(self.pointer_path.suffix + ".tmp")
        tmp_pointer.write_text(version_name, encoding="utf-8")
        os.replace(tmp_pointer, self.pointer_path)
        self._loaded_version = version_name
        self._prune()

    def _prune(self):
        """Retain only the most recent N version files; never delete the current one."""
        keep = self._retention
        if keep <= 0:
            return
        current = self._read_pointer()
        files = sorted(self.index_dir.glob(f"{_VERSION_PREFIX}*{_VERSION_SUFFIX}"))
        if len(files) <= keep:
            return
        for old in files[:-keep]:
            if old.name == current:
                continue
            try:
                old.unlink()
            except OSError as e:
                print(f"[BM25Index] Warning: failed to prune {old.name} ({e}).")

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