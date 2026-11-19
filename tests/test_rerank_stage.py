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


class TestMultiplePassages:
    """Spec: rerank-stage — a candidate scored on several passages takes the
    highest of them; the scorer is called once."""

    def test_highest_passage_score_wins(self):
        docs = [_make_doc("A", "A"), _make_doc("B", "B"), _make_doc("C", "C")]
        table = {"A1": 0.006, "A2": 0.421, "B1": 0.591, "B2": 0.200, "C1": 0.000, "C2": 0.079}
        scorer = MagicMock(side_effect=lambda pairs: [table[p] for _, p in pairs])
        result = rerank_candidates("q", docs, scorer, lambda d: [d.page_content + "1", d.page_content + "2"])
        assert [d.metadata["doc_id"] for d in result["docs"]] == ["B", "A", "C"]
        scorer.assert_called_once()
        assert len(scorer.call_args[0][0]) == 6
        assert [s["score"] for s in result["rerank_scores"]] == ["0.5910", "0.4210", "0.0790"]

    def test_empty_passage_list_falls_back_to_page_content(self):
        docs = [_make_doc("A", "Doc A")]
        scorer = MagicMock(return_value=[0.5])
        rerank_candidates("q", docs, scorer, lambda d: [])
        assert scorer.call_args[0][0] == [("q", "Doc A")]

    def test_wrong_number_of_scores_raises(self):
        docs = [_make_doc("A", "A"), _make_doc("B", "B"), _make_doc("C", "C")]
        scorer = MagicMock(return_value=[0.1, 0.2, 0.3])
        with pytest.raises(ValueError):
            rerank_candidates("q", docs, scorer, lambda d: [d.page_content, d.page_content + "!"])


def test_two_passages_per_candidate_fit_one_hosted_request():
    from src.config import FUSION_TOP_M
    from src.hosted_reranker import MAX_DOCUMENTS
    assert 2 * FUSION_TOP_M <= MAX_DOCUMENTS
