"""Unit tests for BM25Index. Covers tasks 2.1-2.4 and 4.1 (sync integration)."""
import json
import os
import pytest
from unittest.mock import patch, MagicMock
from langchain_core.documents import Document

from src.bm25_index import BM25Index


# --- Fixtures --------------------------------------------------------


@pytest.fixture
def tmp_persist_path(tmp_path):
    return str(tmp_path / "bm25_corpus.json")


PINECONE_TEST_INDEX = "carbon-assistant-qa-index"


@pytest.fixture
def sample_docs():
    return [
        Document(page_content="類別 4.2 排放說明", metadata={"answer": "A1", "text": "Q1", "doc_id": "D1"}),
        Document(page_content="範疇三說明", metadata={"answer": "A2", "text": "Q2", "doc_id": "D2"}),
        Document(page_content="4.1 簡介", metadata={"answer": "A3", "text": "Q3", "doc_id": "D3"}),
    ]


def _make_docs(n: int = 50) -> list[Document]:
    return [
        Document(page_content=f"question {i} about carbon emission {i % 7}", metadata={"doc_id": f"qa_{i}"})
        for i in range(n)
    ]


# --- 2.1 Build from documents ---------------------------------------


class TestBuildFromDocuments:
    def test_build_writes_persisted_file(self, tmp_persist_path):
        idx = BM25Index(persist_path=tmp_persist_path)
        idx.build_from_documents(_make_docs(50))
        assert os.path.exists(tmp_persist_path)
        with open(tmp_persist_path, encoding="utf-8") as f:
            data = json.load(f)
        assert "doc_ids" in data and "tokenized_corpus" in data and "corpus" in data
        assert idx.is_built is True

    def test_build_empty_corpus_stays_unbuilt(self, tmp_persist_path):
        idx = BM25Index(persist_path=tmp_persist_path)
        idx.build_from_documents([])
        assert idx.is_built is False
        assert idx.corpus_size == 0
        assert not os.path.exists(tmp_persist_path)

    def test_build_keys_documents_by_metadata_doc_id(self, tmp_persist_path, sample_docs):
        idx = BM25Index(persist_path=tmp_persist_path)
        idx.build_from_documents(sample_docs)
        results = idx.search("4.2", top_n=3)
        assert results[0][0] == "D1"  # doc_id taken from metadata, not a hash

    def test_build_rejects_document_without_doc_id(self, tmp_persist_path):
        idx = BM25Index(persist_path=tmp_persist_path)
        bad_docs = [Document(page_content="no id here", metadata={"text": "Q", "answer": "A"})]
        with pytest.raises(ValueError, match="doc_id"):
            idx.build_from_documents(bad_docs)
        assert idx.is_built is False
        assert not os.path.exists(tmp_persist_path)


# --- 2.2 Search ------------------------------------------------------


class TestSearch:
    def test_exact_keyword_match_ranks_first(self, tmp_persist_path, sample_docs):
        idx = BM25Index(persist_path=tmp_persist_path)
        idx.build_from_documents(sample_docs)
        results = idx.search("4.2", top_n=3)
        assert len(results) <= 3
        top_doc_id, top_score = results[0]
        assert top_doc_id == "D1"
        assert top_score > 0


# --- 2.3 Get doc -----------------------------------------------------


class TestGetDoc:
    def test_get_doc_returns_document(self, tmp_persist_path, sample_docs):
        idx = BM25Index(persist_path=tmp_persist_path)
        idx.build_from_documents(sample_docs)
        doc = idx.get_doc("D1")
        assert doc is not None
        assert doc.page_content == "類別 4.2 排放說明"
        assert doc.metadata["doc_id"] == "D1"

    def test_get_doc_unknown_returns_none(self, tmp_persist_path, sample_docs):
        idx = BM25Index(persist_path=tmp_persist_path)
        idx.build_from_documents(sample_docs)
        assert idx.get_doc("UNKNOWN") is None


# --- 2.4 Cold-start load ---------------------------------------------


class TestColdStartLoad:
    def test_load_valid_persisted_file(self, tmp_persist_path, sample_docs):
        idx1 = BM25Index(persist_path=tmp_persist_path)
        idx1.build_from_documents(sample_docs)

        idx2 = BM25Index(persist_path=tmp_persist_path)
        assert idx2.is_built is True
        result = idx2.search("4.2", top_n=3)
        assert result[0][0] == "D1"

    def test_corrupt_persisted_file_falls_back(self, tmp_persist_path, tmp_path):
        os.makedirs(os.path.dirname(tmp_persist_path), exist_ok=True)
        with open(tmp_persist_path, "w") as f:
            f.write("{not valid json")
        idx = BM25Index(persist_path=tmp_persist_path)
        assert idx.is_built is False

    def test_search_when_not_built_raises(self, tmp_persist_path):
        idx = BM25Index(persist_path=tmp_persist_path)
        with pytest.raises(RuntimeError, match="not built"):
            idx.search("anything", top_n=5)

# --- 4.1 sync_vector_store integration -------------------------------


class TestSyncIntegration:
    """Tests that sync_vector_store rebuilds BM25 after Pinecone sync."""

    @staticmethod
    def _patch_common(mock_pc_cls, mock_bm25_cls):
        mock_pc = MagicMock()
        mock_pc_cls.return_value = mock_pc
        mock_pc.list_indexes.return_value.names.return_value = [PINECONE_TEST_INDEX]
        mock_pc.list_indexes.return_value.specs.return_value = []
        mock_index = MagicMock()
        mock_pc.Index.return_value = mock_index
        mock_index.list.return_value = []

        mock_bm25 = MagicMock()
        mock_bm25_cls.return_value = mock_bm25
        mock_bm25_cls.side_effect = lambda *a, **kw: mock_bm25
        return mock_bm25, mock_pc

    def test_sync_updates_bm25_index(self, monkeypatch):
        from src import build_vector_store

        monkeypatch.setenv("PINECONE_API_KEY", "fake")
        fake_docs = [{"question": f"Q{i}", "answer": f"A{i}"} for i in range(5)]

        with patch.object(build_vector_store, "OpenAIEmbeddings"), \
             patch.object(build_vector_store, "Pinecone") as mock_pc_cls, \
             patch.object(build_vector_store, "BM25Index") as mock_bm25_cls, \
             patch("os.listdir", return_value=["fake.csv"]), \
             patch("os.path.join", return_value="/fake/fake.csv"), \
             patch.object(build_vector_store, "extract_from_csv", return_value=fake_docs), \
             patch.object(build_vector_store, "DATA_SOURCE_DIR", "/fake_dir"), \
             patch.object(build_vector_store, "ensure_uuids_in_source", return_value=0), \
             patch("os.makedirs"):

            mock_bm25, _ = self._patch_common(mock_pc_cls, mock_bm25_cls)
            result = build_vector_store.sync_vector_store()

        assert result["status"] == "success"
        mock_bm25.build_from_documents.assert_called_once()
        assert len(mock_bm25.build_from_documents.call_args[0][0]) == 5

    def test_sync_build_failure_does_not_break_vector_sync(self, monkeypatch):
        from src import build_vector_store

        monkeypatch.setenv("PINECONE_API_KEY", "fake")
        fake_docs = [{"question": f"Q{i}", "answer": f"A{i}"} for i in range(3)]

        with patch.object(build_vector_store, "OpenAIEmbeddings"), \
             patch.object(build_vector_store, "Pinecone") as mock_pc_cls, \
             patch.object(build_vector_store, "BM25Index") as mock_bm25_cls, \
             patch("os.listdir", return_value=["fake.xlsx"]), \
             patch("os.path.join", return_value="/fake/fake.xlsx"), \
             patch.object(build_vector_store, "extract_from_xlsx", return_value=fake_docs), \
             patch.object(build_vector_store, "DATA_SOURCE_DIR", "/fake_dir"), \
             patch.object(build_vector_store, "ensure_uuids_in_source", return_value=0), \
             patch("os.makedirs"):

            mock_bm25, mock_pc = self._patch_common(mock_pc_cls, mock_bm25_cls)
            mock_bm25.build_from_documents.side_effect = RuntimeError("disk full")
            result = build_vector_store.sync_vector_store()

        assert result["status"] == "success"
        mock_index = mock_pc.Index.return_value
        assert mock_index.upsert.called
