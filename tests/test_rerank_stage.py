"""Unit tests for the standalone rerank stage."""
import pytest
from unittest.mock import MagicMock

from langchain_core.documents import Document

from src.rerank_stage import rerank_candidates


def _make_doc(doc_id: str, content: str) -> Document:
    return Document(page_content=content, metadata={"doc_id": doc_id})


@pytest.fixture
def four_candidates():
    return [
        _make_doc("A", "Doc A body"),
        _make_doc("B", "Doc B body"),
        _make_doc("C", "Doc C body"),
        _make_doc("D", "Doc D body"),
    ]


class TestRerankOutput:
    def test_top3_truncation_and_order(self, four_candidates):
        scorer = MagicMock(return_value=[0.60, 0.95, 0.70, 0.80])
        result = rerank_candidates("test", four_candidates, scorer)

        ids = [d.metadata["doc_id"] for d in result["docs"]]
        assert ids == ["B", "D", "C"]
        assert len(result["docs"]) == 3

        ranks = [entry["rank"] for entry in result["rerank_scores"]]
        assert ranks == [1, 2, 3]
        assert result["rerank_scores"][0]["score"] == "0.9500"

        scorer.assert_called_once()
        pairs = scorer.call_args[0][0]
        assert pairs == [("test", "Doc A body"), ("test", "Doc B body"),
                         ("test", "Doc C body"), ("test", "Doc D body")]

    def test_return_keys_are_docs_and_rerank_scores_only(self, four_candidates):
        scorer = MagicMock(return_value=[0.60, 0.95, 0.70, 0.80])
        result = rerank_candidates("test", four_candidates, scorer)

        assert set(result.keys()) == {"docs", "rerank_scores"}
        for removed_key in ("context", "contexts"):
            assert removed_key not in result

    def test_content_truncated_to_100_chars(self):
        long_body = "x" * 200
        candidates = [_make_doc("A", long_body)]
        scorer = MagicMock(return_value=[0.42])
        result = rerank_candidates("test", candidates, scorer)

        entry = result["rerank_scores"][0]
        assert entry["content"] == "x" * 100 + "..."
        assert entry["score"] == "0.4200"


class TestEmptyCandidates:
    def test_empty_candidates_short_circuits(self):
        scorer = MagicMock(side_effect=AssertionError("scorer must not be called"))
        result = rerank_candidates("test", [], scorer)

        assert result == {"docs": [], "rerank_scores": []}
        scorer.assert_not_called()
