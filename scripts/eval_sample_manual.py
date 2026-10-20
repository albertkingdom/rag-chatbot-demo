#!/usr/bin/env python3
"""Sync the bundled sample manual into a demo index and run the RAGAS evaluation.

Every step runs in this one process so the sync, the checks and the evaluation
share the same PINECONE_INDEX_NAME, DATA_SOURCE_DIR and BM25_INDEX_DIR. The run
refuses the production index, checks that the demo Pinecone index and the BM25
index hold exactly one entry per manual row, checks that hybrid retrieval uses
BM25 (no fallback) for every case and reports how many cases retrieve their
source row, and verifies that the
production index's vector IDs are unchanged afterwards.

Usage (inside the project image, with tests/ and examples/ mounted):
    PINECONE_INDEX_NAME=rag-demo-qa-index \
    DATA_SOURCE_DIR=/tmp/sample/uploaded_files BM25_INDEX_DIR=/tmp/sample/bm25 \
    python -m scripts.eval_sample_manual
"""

import asyncio
import json
import os
import shutil
import sys
import time

PRODUCTION_INDEX = "carbon-assistant-qa-index"
SAMPLE_MANUAL = "examples/carbonm_sample_manual.xlsx"
EVAL_DATA = "tests/rag_eval_data.json"


def fail(message: str) -> int:
    print(f"[eval-sample] ERROR: {message}", flush=True)
    return 1


def list_ids(pc, index_name: str) -> set[str]:
    ids: set[str] = set()
    for batch in pc.Index(index_name).list():
        ids.update(batch)
    return ids


def wait_for_count(pc, index_name: str, expected: int, timeout: float = 90) -> set[str]:
    """Serverless listing is eventually consistent; poll until it settles."""
    deadline = time.monotonic() + timeout
    while True:
        ids = list_ids(pc, index_name)
        if len(ids) == expected or time.monotonic() > deadline:
            return ids
        time.sleep(5)


async def check_retrieval(
    cases: list[dict], doc_id_by_answer: dict[str, str]
) -> tuple[list[str], list[str]]:
    """Return (problems, misses).

    A problem means BM25 did not take part (fallback or no BM25 results) and
    fails the run. A miss means the ground-truth source is not among the fused
    candidates; that is retrieval quality, which RAGAS context_recall scores,
    so misses are reported but do not fail the run.
    """
    from src.services import get_hybrid_retriever

    retriever = get_hybrid_retriever()
    problems, misses = [], []
    for case in cases:
        meta = (await retriever.retrieve(case["question"]))["fusion_metadata"]
        expected = doc_id_by_answer[case["ground_truth"]]
        fused = {r["doc_id"] for r in meta["fusion_results"]}
        if meta.get("fallback"):
            problems.append(f"{case['question']}: fallback={meta['fallback']}")
        elif not meta["bm25_results"]:
            problems.append(f"{case['question']}: no BM25 results")
        if expected not in fused:
            misses.append(case["question"])
    return problems, misses


def main() -> int:
    from src import config

    index_name = config.PINECONE_INDEX_NAME
    if index_name == PRODUCTION_INDEX:
        return fail(f"refusing to sync into the production index '{PRODUCTION_INDEX}'.")
    print(f"[eval-sample] Demo index: {index_name}", flush=True)
    print(f"[eval-sample] DATA_SOURCE_DIR={config.DATA_SOURCE_DIR} BM25_INDEX_DIR={config.BM25_INDEX_DIR}", flush=True)

    from pinecone import Pinecone

    from src.bm25_index import BM25Index
    from src.build_vector_store import extract_from_xlsx, sync_vector_store

    pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
    production_before = list_ids(pc, PRODUCTION_INDEX)
    print(f"[eval-sample] Production index has {len(production_before)} vectors.", flush=True)

    # Only the sample manual may live in the data directory.
    if os.path.isdir(config.DATA_SOURCE_DIR) and os.listdir(config.DATA_SOURCE_DIR):
        return fail(f"DATA_SOURCE_DIR '{config.DATA_SOURCE_DIR}' is not empty.")
    os.makedirs(config.DATA_SOURCE_DIR, exist_ok=True)
    manual_copy = shutil.copy(SAMPLE_MANUAL, config.DATA_SOURCE_DIR)

    result = sync_vector_store()
    print(f"[eval-sample] Sync: {result}", flush=True)
    if result.get("status") != "success":
        return fail("sync did not succeed.")

    entries = extract_from_xlsx(manual_copy)  # now carries the persisted uuids
    expected = len(entries)
    demo_ids = wait_for_count(pc, index_name, expected)
    if len(demo_ids) != expected:
        return fail(f"demo index holds {len(demo_ids)} vectors, expected {expected}.")
    bm25 = BM25Index()
    if bm25.corpus_size != expected:
        return fail(f"BM25 index holds {bm25.corpus_size} documents, expected {expected}.")
    print(f"[eval-sample] Demo index and BM25 both hold {expected} entries.", flush=True)

    with open(EVAL_DATA, encoding="utf-8") as f:
        cases = json.load(f)
    doc_id_by_answer = {e["answer"]: e["uuid"] for e in entries}
    problems, misses = asyncio.run(check_retrieval(cases, doc_id_by_answer))
    if problems:
        for p in problems:
            print(f"[eval-sample]   {p}", flush=True)
        return fail("hybrid retrieval did not use BM25 for every case.")
    print(f"[eval-sample] Hybrid retrieval used BM25 for all {len(cases)} cases.", flush=True)
    print(f"[eval-sample] Source found in fused candidates for {len(cases) - len(misses)}/{len(cases)} cases.", flush=True)
    for q in misses:
        print(f"[eval-sample]   source not retrieved: {q}", flush=True)

    from tests import evaluate_rag

    eval_status = evaluate_rag.main()

    production_after = list_ids(pc, PRODUCTION_INDEX)
    if production_after != production_before:
        return fail("production index vector IDs changed during the run.")
    print(f"[eval-sample] Production index unchanged ({len(production_after)} vectors).", flush=True)
    return eval_status


if __name__ == "__main__":
    sys.exit(main())
