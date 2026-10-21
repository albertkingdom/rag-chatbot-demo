#!/usr/bin/env python3
"""Sync the bundled sample manual into an index and run the RAGAS evaluation.

Every step runs in this one process so the sync, the checks and the evaluation
share the same PINECONE_INDEX_NAME, DATA_SOURCE_DIR and BM25_INDEX_DIR. The run
refuses the production index, checks that the Pinecone index and the BM25 index
hold exactly one entry per manual row, checks that hybrid retrieval uses BM25
(no fallback) for every case and reports how many cases retrieve their source
row, and checks routing: every manual and evaluation question routes to rag
and the general negatives to direct. With --audit-index it also verifies that
another index in the same Pinecone project (for example production) keeps the
same vector IDs, even when the run fails part-way.

Usage (inside the project image, with tests/ and examples/ mounted):
    PINECONE_INDEX_NAME=rag-demo-qa-index \
    DATA_SOURCE_DIR=/tmp/sample/uploaded_files BM25_INDEX_DIR=/tmp/sample/bm25 \
    python -m scripts.eval_sample_manual [--audit-index carbon-assistant-sample-index]
"""

import argparse
import asyncio
import json
import os
import shutil
import sys
import time

# Production indexes: the current one and the previous one kept for rollback.
PRODUCTION_INDEXES = ("carbon-assistant-qa-index", "carbon-assistant-sample-index")
SAMPLE_MANUAL = "examples/carbonm_sample_manual.xlsx"
EVAL_DATA = "tests/rag_eval_data.json"
# General questions that must not reach the knowledge base (spec:
# query-routing-coverage). Shared with tests/test_query_router.py.
ROUTING_NEGATIVES = [
    "什麼是機器學習？", "推薦一本好看的小說", "明天要不要帶傘？", "幫我想一個生日派對的主題",
    "遊戲角色怎麼練等", "台灣的首都是哪裡？", "怎麼學好英文？", "如何修改 Gmail 的個人資料？",
    "Excel 報表怎麼做樞紐分析？", "iPhone 如何開啟兩步驟驗證？", "數據分析要學什麼？",
    "相關係數是什麼？", "如何查證網路謠言？", "哲學的研究範疇有哪些？", "幫我寫 Python 計算相關係數",
    "Excel 如何匯入數據？", "倉庫庫存盤查要注意什麼？", "GDP 的基準年是什麼意思？", "如何煮咖啡？",
    "原始數據要怎麼清理？", "Excel 如何批次匯入資料？", "Google 帳戶沒有收到啟用信怎麼辦？",
    "哲學的研究範疇一般如何劃分？", "兩步驟驗證和雙因素驗證有什麼差別？", "工廠廠區的消防規定有哪些？",
    "論文的佐證文件要怎麼準備？", "如何在 Excel 製作數據填報表？", "Google 表單可以設定填報截止時間嗎？",
    "Google 表單的填報人員怎麼設定？",
]

REQUIRED_CREDENTIALS = ("PINECONE_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY")


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


ROUTER_ERROR_PREFIX = "LLM 路由發生錯誤"


async def check_routing(
    must_rag: list[str], must_direct: list[str]
) -> tuple[list[str], list[str], int]:
    """Route questions as a user would (no history).

    Returns (misroutes, service_errors, rule_decided): must_rag questions must
    route to rag, must_direct questions the rule stage leaves uncertain must
    route to direct. An LLM routing error is retried once and reported apart
    from misroutes. rule_decided counts must_rag questions the rule stage
    decided without the LLM.
    """
    from src import query_router

    misroutes, errors = [], []

    async def route(question):
        result = await query_router.route_query(question)
        if result.reasoning.startswith(ROUTER_ERROR_PREFIX):
            result = await query_router.route_query(question)
        return result

    async def expect(question, wanted):
        result = await route(question)
        if result.reasoning.startswith(ROUTER_ERROR_PREFIX):
            errors.append(f"{question}: {result.reasoning}")
        elif result.route != wanted:
            misroutes.append(f"{question}: {result.route} (expected {wanted}) — {result.reasoning}")

    rule_decided = 0
    for question in must_rag:
        if query_router._rule_route(question) == "rag":
            rule_decided += 1
        await expect(question, "rag")
    for question in must_direct:
        rule = query_router._rule_route(question)
        if rule == "rag":
            misroutes.append(f"{question}: rag by rule (expected direct)")
        elif rule == "uncertain":
            await expect(question, "direct")
    return misroutes, errors, rule_decided


def _pinecone_client():
    from pinecone import Pinecone

    return Pinecone(api_key=os.environ["PINECONE_API_KEY"])


def run_evaluation(pc, index_name: str) -> int:
    """Sync the sample manual into index_name, check it, then run RAGAS."""
    from src import config
    from src.bm25_index import BM25Index
    from src.build_vector_store import extract_from_xlsx, sync_vector_store

    # Only the sample manual may live in the data directory.
    if os.path.isdir(config.DATA_SOURCE_DIR) and os.listdir(config.DATA_SOURCE_DIR):
        return fail(f"DATA_SOURCE_DIR '{config.DATA_SOURCE_DIR}' is not empty.")
    os.makedirs(config.DATA_SOURCE_DIR, exist_ok=True)
    manual_copy = shutil.copy(SAMPLE_MANUAL, config.DATA_SOURCE_DIR)

    result = sync_vector_store()
    print(f"[eval-sample] Sync: {result}", flush=True)
    if result.get("status") != "success":
        return fail("sync did not succeed.")

    entries = extract_from_xlsx(manual_copy)
    expected = len(entries)
    demo_ids = wait_for_count(pc, index_name, expected)
    if len(demo_ids) != expected:
        return fail(f"index holds {len(demo_ids)} vectors, expected {expected}.")
    bm25 = BM25Index()
    if bm25.corpus_size != expected:
        return fail(f"BM25 index holds {bm25.corpus_size} documents, expected {expected}.")
    print(f"[eval-sample] Index and BM25 both hold {expected} entries.", flush=True)

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

    manual_questions = [e["question"] for e in entries]
    misroutes, errors, rule_decided = asyncio.run(
        check_routing(manual_questions + [c["question"] for c in cases], ROUTING_NEGATIVES)
    )
    for line in misroutes:
        print(f"[eval-sample]   misroute: {line}", flush=True)
    for line in errors:
        print(f"[eval-sample]   routing service error: {line}", flush=True)
    if misroutes or errors:
        return fail("routing check failed.")
    print(
        f"[eval-sample] Routing check passed; rule stage decided "
        f"{rule_decided}/{len(manual_questions) + len(cases)} knowledge-base questions.",
        flush=True,
    )

    from tests import evaluate_rag

    return evaluate_rag.main()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--audit-index",
        help="another index in the same Pinecone project whose vector IDs must "
        "be identical before and after the run (e.g. a production index)",
    )
    args = parser.parse_args(argv)

    from src import config

    index_name = config.PINECONE_INDEX_NAME
    if index_name in PRODUCTION_INDEXES:
        return fail(f"refusing to sync into the production index '{index_name}'.")
    if args.audit_index and args.audit_index == index_name:
        return fail(f"the evaluated index and --audit-index are both '{index_name}'.")
    missing = [name for name in REQUIRED_CREDENTIALS if not os.environ.get(name)]
    if missing:
        return fail(f"missing credentials: {', '.join(missing)}.")
    print(f"[eval-sample] Evaluated index: {index_name}", flush=True)
    print(f"[eval-sample] DATA_SOURCE_DIR={config.DATA_SOURCE_DIR} BM25_INDEX_DIR={config.BM25_INDEX_DIR}", flush=True)

    pc = _pinecone_client()
    audit_before = None
    if args.audit_index:
        audit_before = list_ids(pc, args.audit_index)
        print(f"[eval-sample] Audited index '{args.audit_index}' has {len(audit_before)} vectors.", flush=True)

    status = 1
    try:
        status = run_evaluation(pc, index_name)
    finally:
        # Always compare the audited index, even after an early failure or an
        # exception; an exception in flight still propagates after this block.
        if audit_before is not None:
            try:
                audit_after = list_ids(pc, args.audit_index)
            except Exception as e:
                print(f"[eval-sample] ERROR: could not re-read '{args.audit_index}': {e}", flush=True)
                status = 1
            else:
                if audit_after != audit_before:
                    print(f"[eval-sample] ERROR: '{args.audit_index}' vector IDs changed during the run.", flush=True)
                    status = 1
                else:
                    print(f"[eval-sample] Audited index unchanged ({len(audit_after)} vectors).", flush=True)
    return status


if __name__ == "__main__":
    sys.exit(main())
