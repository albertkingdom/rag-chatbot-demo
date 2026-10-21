"""Spec: sample-knowledge-base — bundled sample manual and grounded eval data."""

import json
from pathlib import Path

import pytest

from src.build_vector_store import extract_from_xlsx

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_MANUAL = REPO_ROOT / "examples" / "carbonm_sample_manual.xlsx"
EVAL_DATA = REPO_ROOT / "tests" / "rag_eval_data.json"

# One keyword per sample-manual topic; each must appear in some question.
TOPIC_KEYWORDS = {
    "account": "密碼",
    "boundary": "邊界",
    "emission": "排放",
    "review": "審核",
    "report": "報告",
}


@pytest.fixture(scope="module")
def entries():
    return extract_from_xlsx(str(SAMPLE_MANUAL))


def test_sample_manual_has_at_least_50_entries(entries):
    assert len(entries) >= 50
    assert all(e["question"] and e["answer"] for e in entries)


def test_sample_manual_prefixes_are_stripped(entries):
    for e in entries:
        assert "問：" not in e["question"]
        assert "答：" not in e["answer"]


def test_sample_manual_covers_every_topic(entries):
    questions = " ".join(e["question"] for e in entries)
    for topic, keyword in TOPIC_KEYWORDS.items():
        assert keyword in questions, topic


def test_eval_ground_truths_come_from_sample_manual(entries):
    answers = {e["answer"] for e in entries}
    cases = json.loads(EVAL_DATA.read_text(encoding="utf-8"))
    assert len(cases) == 12
    for case in cases:
        assert case["ground_truth"] in answers, case["question"]


def test_sample_manual_ships_stable_unique_uuids(entries):
    # Fixed uuids keep doc_ids identical across separate copies and re-syncs,
    # so re-running the sync does not delete and re-embed every row.
    uuids = [e["uuid"] for e in entries]
    assert all(uuids)
    assert len(set(uuids)) == len(uuids)
