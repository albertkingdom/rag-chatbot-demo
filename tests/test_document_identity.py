"""Tests for stable uuid-based doc_id contract.

Covers tasks 1.1, 1.2, 2.1, 2.2, 2.3, 3.1, 3.2 of change stable-doc-id-uuid:
- extract reads the uuid column into the returned dict
- sync writes uuid into Document.metadata['doc_id']
- missing uuids are generated and persisted back to the source file
- re-sync of unchanged data preserves the doc_id set
- same question with different answers is retained as distinct docs
- Pinecone upsert uses the uuid as the vector id and stores doc_id in metadata
"""
import csv
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

from src import build_vector_store
from src.build_vector_store import (
    extract_from_csv,
    extract_from_xlsx,
    ensure_uuids_in_source,
)


# --- helpers ---------------------------------------------------------


def _write_xlsx(path, rows):
    pd.DataFrame(rows).to_excel(path, index=False)


def _write_csv(path, rows, fieldnames):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def _run_sync(monkeypatch, data_dir):
    """Run sync_vector_store against a real data dir with Pinecone mocked."""
    monkeypatch.setenv("PINECONE_API_KEY", "fake")
    with patch.object(build_vector_store, "OpenAIEmbeddings") as mock_emb, \
         patch.object(build_vector_store, "Pinecone") as mock_pc_cls, \
         patch.object(build_vector_store, "BM25Index") as mock_bm25_cls, \
         patch.object(build_vector_store, "DATA_SOURCE_DIR", str(data_dir)):

        mock_emb.return_value.embed_documents.side_effect = (
            lambda texts: [[0.1, 0.2, 0.3] for _ in texts]
        )
        mock_pc = MagicMock()
        mock_pc_cls.return_value = mock_pc
        mock_pc.list_indexes.return_value.names.return_value = [
            build_vector_store.PINECONE_INDEX_NAME
        ]
        mock_index = MagicMock()
        mock_pc.Index.return_value = mock_index
        mock_index.list.return_value = []
        mock_bm25 = MagicMock()
        mock_bm25_cls.return_value = mock_bm25

        result = build_vector_store.sync_vector_store()
    return result, mock_index, mock_bm25


def _upserted_vectors(mock_index):
    vectors = []
    for call in mock_index.upsert.call_args_list:
        vectors.extend(call.kwargs["vectors"])
    return vectors


# --- 1.1 extract reads uuid column -----------------------------------


class TestExtractReadsUuid:
    def test_xlsx_reads_uuid(self, tmp_path):
        path = tmp_path / "qa.xlsx"
        _write_xlsx(path, [{"question": "問：Q1", "answer": "答：A1", "uuid": "u-1"}])
        docs = extract_from_xlsx(str(path))
        assert docs[0]["uuid"] == "u-1"

    def test_csv_reads_uuid(self, tmp_path):
        path = tmp_path / "qa.csv"
        _write_csv(
            path,
            [{"question": "問：Q1", "answer": "答：A1", "uuid": "u-9"}],
            ["question", "answer", "uuid"],
        )
        docs = extract_from_csv(str(path))
        assert docs[0]["uuid"] == "u-9"

    def test_missing_uuid_column_yields_none(self, tmp_path):
        path = tmp_path / "qa.xlsx"
        _write_xlsx(path, [{"question": "問：Q1", "answer": "答：A1"}])
        docs = extract_from_xlsx(str(path))
        assert docs[0]["uuid"] is None


# --- 2.1 / 2.2 generate + persist uuids ------------------------------


class TestEnsureUuids:
    def test_xlsx_backfills_and_persists(self, tmp_path):
        path = tmp_path / "qa.xlsx"
        _write_xlsx(path, [{"question": "Q1", "answer": "A1"}, {"question": "Q2", "answer": "A2"}])
        added = ensure_uuids_in_source(str(path))
        assert added == 2
        df = pd.read_excel(path)
        assert "uuid" in df.columns
        assert df["uuid"].notna().all()
        assert df["uuid"].astype(str).str.len().gt(0).all()

    def test_csv_backfills_and_persists(self, tmp_path):
        path = tmp_path / "qa.csv"
        _write_csv(
            path,
            [{"question": "Q1", "answer": "A1"}],
            ["question", "answer"],
        )
        added = ensure_uuids_in_source(str(path))
        assert added == 1
        docs = extract_from_csv(str(path))
        assert docs[0]["uuid"]

    def test_existing_uuids_are_not_regenerated(self, tmp_path):
        path = tmp_path / "qa.xlsx"
        _write_xlsx(path, [{"question": "Q1", "answer": "A1", "uuid": "keep-me"}])
        added = ensure_uuids_in_source(str(path))
        assert added == 0
        df = pd.read_excel(path)
        assert df.loc[0, "uuid"] == "keep-me"

    def test_writeback_failure_aborts_sync(self, tmp_path, monkeypatch):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        _write_xlsx(data_dir / "qa.xlsx", [{"question": "Q1", "answer": "A1"}])
        monkeypatch.setenv("PINECONE_API_KEY", "fake")
        with patch.object(build_vector_store, "Pinecone") as mock_pc_cls, \
             patch.object(build_vector_store, "OpenAIEmbeddings"), \
             patch.object(build_vector_store, "DATA_SOURCE_DIR", str(data_dir)), \
             patch.object(build_vector_store, "ensure_uuids_in_source",
                          side_effect=OSError("disk full")):
            mock_pc = MagicMock()
            mock_pc_cls.return_value = mock_pc
            mock_pc.list_indexes.return_value.names.return_value = [
                build_vector_store.PINECONE_INDEX_NAME
            ]
            result = build_vector_store.sync_vector_store()
        assert result["status"] == "error"


# --- 1.2 / 3.2 doc_id flows into metadata and stores -----------------


class TestDocIdInStores:
    def test_sync_sets_doc_id_metadata_from_uuid(self, tmp_path, monkeypatch):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        _write_xlsx(data_dir / "qa.xlsx", [{"question": "問：Q1", "answer": "答：A1"}])

        result, mock_index, mock_bm25 = _run_sync(monkeypatch, data_dir)
        assert result["status"] == "success"

        # the source file now holds the persisted uuid
        persisted_uuid = str(pd.read_excel(data_dir / "qa.xlsx").loc[0, "uuid"])

        bm25_docs = mock_bm25.build_from_documents.call_args[0][0]
        assert bm25_docs[0].metadata["doc_id"] == persisted_uuid

    def test_pinecone_upsert_uses_uuid_id_and_metadata(self, tmp_path, monkeypatch):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        _write_xlsx(data_dir / "qa.xlsx", [{"question": "問：Q1", "answer": "答：A1"}])

        result, mock_index, _ = _run_sync(monkeypatch, data_dir)
        assert result["status"] == "success"

        persisted_uuid = str(pd.read_excel(data_dir / "qa.xlsx").loc[0, "uuid"])
        vectors = _upserted_vectors(mock_index)
        assert len(vectors) == 1
        assert vectors[0]["id"] == persisted_uuid
        assert vectors[0]["metadata"]["doc_id"] == persisted_uuid


# --- 2.3 re-sync stability -------------------------------------------


class TestResyncStability:
    def test_resync_preserves_doc_ids(self, tmp_path, monkeypatch):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        _write_xlsx(
            data_dir / "qa.xlsx",
            [{"question": "問：Q1", "answer": "答：A1"}, {"question": "問：Q2", "answer": "答：A2"}],
        )

        _, idx1, bm1 = _run_sync(monkeypatch, data_dir)
        ids_first = {v["id"] for v in _upserted_vectors(idx1)}

        _, idx2, bm2 = _run_sync(monkeypatch, data_dir)
        # second run: uuids already persisted, so the doc_id set is identical
        docs2 = bm2.build_from_documents.call_args[0][0]
        ids_second = {d.metadata["doc_id"] for d in docs2}

        assert ids_first == ids_second


# --- 3.1 dedup keyed by uuid -----------------------------------------


class TestDedupByUuid:
    def test_same_question_different_answer_both_kept(self, tmp_path, monkeypatch):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        _write_xlsx(
            data_dir / "qa.xlsx",
            [
                {"question": "問：同一個問題", "answer": "答：答案一"},
                {"question": "問：同一個問題", "answer": "答：答案二"},
            ],
        )

        _, _, mock_bm25 = _run_sync(monkeypatch, data_dir)
        docs = mock_bm25.build_from_documents.call_args[0][0]
        doc_ids = {d.metadata["doc_id"] for d in docs}
        answers = {d.metadata["answer"] for d in docs}
        assert len(docs) == 2
        assert len(doc_ids) == 2  # distinct uuids
        assert answers == {"答案一", "答案二"}
