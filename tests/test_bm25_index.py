"""Unit tests for BM25Index. Covers build/search/persist/cold-start and the
Chinese word-segmentation tokenizer (spec: Tokenizer performs Chinese word
segmentation).
"""
import json
import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from langchain_core.documents import Document

from src.bm25_index import BM25Index


# --- Fixtures --------------------------------------------------------


@pytest.fixture
def tmp_index_dir(tmp_path):
    return str(tmp_path / "bm25_index")


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
    def test_build_writes_persisted_file(self, tmp_index_dir):
        idx = BM25Index(index_dir=tmp_index_dir)
        idx.build_from_documents(_make_docs(50))
        # Pointer file + at least one write-once version file exist.
        assert (Path(tmp_index_dir) / "bm25_current.txt").exists()
        version_files = list(Path(tmp_index_dir).glob("bm25_corpus_*.json"))
        assert len(version_files) >= 1
        data = json.loads(version_files[0].read_text(encoding="utf-8"))
        assert "doc_ids" in data and "tokenized_corpus" in data and "corpus" in data
        assert idx.is_built is True

    def test_build_empty_corpus_stays_unbuilt(self, tmp_index_dir):
        idx = BM25Index(index_dir=tmp_index_dir)
        idx.build_from_documents([])
        assert idx.is_built is False
        assert idx.corpus_size == 0
        assert not (Path(tmp_index_dir) / "bm25_current.txt").exists()

    def test_build_keys_documents_by_metadata_doc_id(self, tmp_index_dir, sample_docs):
        idx = BM25Index(index_dir=tmp_index_dir)
        idx.build_from_documents(sample_docs)
        results = idx.search("4.2", top_n=3)
        assert results[0][0] == "D1"  # doc_id taken from metadata, not a hash

    def test_build_rejects_document_without_doc_id(self, tmp_index_dir):
        idx = BM25Index(index_dir=tmp_index_dir)
        bad_docs = [Document(page_content="no id here", metadata={"text": "Q", "answer": "A"})]
        with pytest.raises(ValueError, match="doc_id"):
            idx.build_from_documents(bad_docs)
        assert idx.is_built is False
        assert not (Path(tmp_index_dir) / "bm25_current.txt").exists()


# --- 2.2 Search ------------------------------------------------------


class TestSearch:
    def test_exact_keyword_match_ranks_first(self, tmp_index_dir, sample_docs):
        idx = BM25Index(index_dir=tmp_index_dir)
        idx.build_from_documents(sample_docs)
        results = idx.search("4.2", top_n=3)
        assert len(results) <= 3
        top_doc_id, top_score = results[0]
        assert top_doc_id == "D1"
        assert top_score > 0


# --- 2.3 Get doc -----------------------------------------------------


class TestGetDoc:
    def test_get_doc_returns_document(self, tmp_index_dir, sample_docs):
        idx = BM25Index(index_dir=tmp_index_dir)
        idx.build_from_documents(sample_docs)
        doc = idx.get_doc("D1")
        assert doc is not None
        assert doc.page_content == "類別 4.2 排放說明"
        assert doc.metadata["doc_id"] == "D1"

    def test_get_doc_unknown_returns_none(self, tmp_index_dir, sample_docs):
        idx = BM25Index(index_dir=tmp_index_dir)
        idx.build_from_documents(sample_docs)
        assert idx.get_doc("UNKNOWN") is None


# --- 2.4 Cold-start load ---------------------------------------------


class TestColdStartLoad:
    def test_load_valid_persisted_file(self, tmp_index_dir, sample_docs):
        idx1 = BM25Index(index_dir=tmp_index_dir)
        idx1.build_from_documents(sample_docs)

        idx2 = BM25Index(index_dir=tmp_index_dir)
        assert idx2.is_built is True
        result = idx2.search("4.2", top_n=3)
        assert result[0][0] == "D1"

    def test_corrupt_persisted_file_falls_back(self, tmp_index_dir):
        index_dir = Path(tmp_index_dir)
        index_dir.mkdir(parents=True, exist_ok=True)
        version_name = "bm25_corpus_corrupt.json"
        (index_dir / "bm25_current.txt").write_text(version_name, encoding="utf-8")
        (index_dir / version_name).write_text("{not valid json", encoding="utf-8")
        idx = BM25Index(index_dir=tmp_index_dir)
        assert idx.is_built is False

    def test_search_when_not_built_raises(self, tmp_index_dir):
        idx = BM25Index(index_dir=tmp_index_dir)
        with pytest.raises(RuntimeError, match="not built"):
            idx.search("anything", top_n=5)


# --- 3.1 / 3.2 Chinese tokenization ----------------------------------
# Spec: Tokenizer performs Chinese word segmentation.


class TestChineseTokenization:
    """Spec scenarios for Chinese word segmentation."""

    def test_chinese_query_produces_nonzero_scores_for_matching_docs(self, tmp_index_dir):
        # Spec example: a doc sharing 密碼 with the query must score > 0 and
        # rank no later than a doc sharing no term with the query.
        docs = [
            Document(page_content="如果我忘記我的密碼，我該怎麼辦？", metadata={"doc_id": "D_pwd"}),
            Document(page_content="如何登入系統？請輸入帳號。", metadata={"doc_id": "D_login"}),
            Document(page_content="碳排放範疇三的計算方式。", metadata={"doc_id": "D_carbon"}),
        ]
        idx = BM25Index(index_dir=tmp_index_dir)
        idx.build_from_documents(docs)
        results = dict(idx.search("忘記密碼怎麼辦？", top_n=3))
        assert results["D_pwd"] > 0
        # Doc with no shared term ranks no earlier than the matching doc.
        assert results["D_pwd"] >= results.get("D_carbon", 0.0)

    def test_same_tokenizer_at_build_and_search(self, tmp_index_dir):
        text = "如果我忘記我的密碼，我該怎麼辦？"
        docs = [
            Document(page_content=text, metadata={"doc_id": "D1"}),
            Document(page_content="碳排放範疇三的計算方式。", metadata={"doc_id": "D2"}),
            Document(page_content="如何聯絡客服人員？", metadata={"doc_id": "D3"}),
        ]
        idx = BM25Index(index_dir=tmp_index_dir)
        idx.build_from_documents(docs)
        # Build path stored tokens equal to _tokenize(text).
        build_tokens = idx._tokenized_corpus[0]
        assert build_tokens == BM25Index._tokenize(text)
        # Searching with the same text self-matches D1 with a positive score
        # (terms are in 1 of 3 docs, so IDF > 0), which only happens if query
        # and corpus tokens come from the same tokenizer.
        results = idx.search(text, top_n=3)
        assert results[0][0] == "D1"
        assert results[0][1] > 0

    def test_full_width_and_lowercase_normalization(self):
        # 全形 Latin → 半形 + lowercase so surface variants match.
        assert BM25Index._tokenize("ＡＢＣ") == BM25Index._tokenize("abc")
        assert "abc" in BM25Index._tokenize("ＡＢＣ")


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
