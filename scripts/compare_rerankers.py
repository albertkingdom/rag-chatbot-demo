#!/usr/bin/env python3
"""Compare rerankers on the same candidates: ranking quality, latency and score scale.

Experiment tooling, not part of the service. For every evaluation question the
hybrid retriever fetches the fused candidates once from the demo index (read
only: nothing is synced or written), then each reranker reorders the same
candidates through ``rerank_candidates``, so only the reranker differs.

Rerankers (skipped with a notice when their key or dependency is missing):
    bge-local      BAAI/bge-reranker-v2-m3 in this process via sentence-transformers
                   (CrossEncoder.predict default: sigmoid for a single output, the
                   same call the former in-process reranker made)
    pinecone-bge   bge-reranker-v2-m3 on Pinecone Inference, through the service's
                   own client (src/hosted_reranker.py)
    voyage         Voyage AI rerank API          (VOYAGE_API_KEY)
    cohere         Cohere rerank API              (COHERE_API_KEY)

Datasets:
    manual     tests/rag_eval_data.json against the demo index; the expected
               document is the manual row whose answer is the ground truth.
               Needs PINECONE_API_KEY and OPENAI_API_KEY for retrieval, and the
               sample manual already synced (scripts/eval_sample_manual.py).
    synthetic  tests/rerank_eval_data.json: fixed relevant and irrelevant
               passages, no retrieval and no index.

Per reranker it reports hit@1, hit@3 (RERANK_TOP_K), MRR, latency (median and
max per call), errors and the score range (thresholds such as
GRADE_SCORE_THRESHOLD are tuned to the BGE scale). Questions whose expected
document is not among the candidates are counted apart: no reranker can fix
those. The question set is small; treat differences of one question as noise.

Parity: every reranker after the first is compared with the first one on the
same passages: max and mean absolute score difference, questions whose top-3
order differs, those whose top-3 set is still the same, and how many questions
and passages were compared. The manual dataset fails (exit 1) when any question
has no candidates or retrieval fell back to vector only, so a parity check can
never pass without comparing anything.

Usage:
    PINECONE_INDEX_NAME=rag-demo-qa-index \\
    python -m scripts.compare_rerankers --dataset manual \\
        --providers bge-local,pinecone-bge,voyage,cohere --out /tmp/rerank.json
"""

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from dataclasses import dataclass, field
from typing import Callable

import requests

from scripts.eval_sample_manual import PRODUCTION_INDEXES, SAMPLE_MANUAL

EVAL_DATA = "tests/rag_eval_data.json"
SYNTHETIC_DATA = "tests/rerank_eval_data.json"
HTTP_TIMEOUT = 30

# Model ids can be overridden per run; defaults are the current models as of 2026-11.
DEFAULT_MODELS = {
    "bge-local": "BAAI/bge-reranker-v2-m3",
    "pinecone-bge": "bge-reranker-v2-m3",
    "voyage": "rerank-3",
    "cohere": "rerank-v5.0-pro",
}
ALL_PROVIDERS = ("bge-local", "pinecone-bge", "voyage", "cohere")

# A scorer takes the query and the passages and returns one score per passage, in order.
Scorer = Callable[[str, list[str]], list[float]]


class ProviderUnavailable(Exception):
    """The provider cannot run here (missing key or dependency)."""


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------


def _scores_by_index(items: list[dict], count: int, index_key: str, score_key: str) -> list[float]:
    """Put API results (which may be sorted by score) back in passage order."""
    scores = [float("-inf")] * count
    for item in items:
        scores[int(item[index_key])] = float(item[score_key])
    if any(s == float("-inf") for s in scores):
        raise RuntimeError(f"response scored {len(items)} of {count} passages")
    return scores


def _post_json(url: str, key: str, payload: dict) -> dict:
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=payload,
        timeout=HTTP_TIMEOUT,
    )
    if resp.status_code != 200:
        # The body may echo the request, never the key; keep it short anyway.
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
    return resp.json()


def voyage_scorer(model: str) -> Scorer:
    key = os.environ.get("VOYAGE_API_KEY")
    if not key:
        raise ProviderUnavailable("VOYAGE_API_KEY is not set")

    def score(query: str, passages: list[str]) -> list[float]:
        data = _post_json(
            "https://api.voyageai.com/v1/rerank",
            key,
            {"query": query, "documents": passages, "model": model},
        )
        return _scores_by_index(data["data"], len(passages), "index", "relevance_score")

    return score


def cohere_scorer(model: str) -> Scorer:
    key = os.environ.get("COHERE_API_KEY")
    if not key:
        raise ProviderUnavailable("COHERE_API_KEY is not set")

    def score(query: str, passages: list[str]) -> list[float]:
        data = _post_json(
            "https://api.cohere.com/v2/rerank",
            key,
            {"query": query, "documents": passages, "model": model, "top_n": len(passages)},
        )
        return _scores_by_index(data["results"], len(passages), "index", "relevance_score")

    return score


def pinecone_scorer(_model: str) -> Scorer:
    """The service's own hosted client, so parity covers the code that ships."""
    key = os.environ.get("PINECONE_API_KEY")
    if not key:
        raise ProviderUnavailable("PINECONE_API_KEY is not set")
    from src.config import RERANK_TIMEOUT_SECONDS
    from src.hosted_reranker import PineconeReranker

    client = PineconeReranker(api_key=key, timeout=RERANK_TIMEOUT_SECONDS)

    def score(query: str, passages: list[str]) -> list[float]:
        return client.score([(query, p) for p in passages])

    return score


def bge_local_scorer(_model: str) -> Scorer:
    """The model in this process; unavailable once sentence-transformers is gone."""
    try:
        from sentence_transformers import CrossEncoder
    except ImportError as e:
        raise ProviderUnavailable(f"sentence-transformers not installed: {e}") from e
    model = CrossEncoder(
        "BAAI/bge-reranker-v2-m3",
        device="cpu",
        cache_folder=os.environ.get("MODEL_CACHE_DIR"),
    )

    def score(query: str, passages: list[str]) -> list[float]:
        return [float(s) for s in model.predict([(query, p) for p in passages])]

    return score


PROVIDER_FACTORIES: dict[str, Callable[[str], Scorer]] = {
    "bge-local": bge_local_scorer,
    "pinecone-bge": pinecone_scorer,
    "voyage": voyage_scorer,
    "cohere": cohere_scorer,
}


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


@dataclass
class Case:
    question: str
    passages: list[str]
    relevant: set[int]  # indexes into passages; empty when retrieval missed
    fallback: str | None = None  # fusion_metadata["fallback"], e.g. "vector_only"


def synthetic_cases(path: str = SYNTHETIC_DATA) -> list[Case]:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    cases = []
    for item in raw:
        passages = list(item["irrelevant_docs"]) + list(item["relevant_docs"])
        first_relevant = len(item["irrelevant_docs"])
        cases.append(Case(item["question"], passages, set(range(first_relevant, len(passages)))))
    return cases


async def manual_cases(path: str = EVAL_DATA) -> list[Case]:
    """Fused candidates from the demo index, retrieved once per question (read only)."""
    from src.build_vector_store import extract_from_xlsx
    from src.services import get_hybrid_retriever

    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    doc_id_by_answer = {e["answer"]: e["uuid"] for e in extract_from_xlsx(SAMPLE_MANUAL)}
    retriever = get_hybrid_retriever()
    cases = []
    for item in raw:
        expected = doc_id_by_answer.get(item["ground_truth"])
        result = await retriever.retrieve(item["question"])
        docs = result["candidates"]
        relevant = {i for i, d in enumerate(docs) if expected and d.metadata.get("doc_id") == expected}
        fallback = (result.get("fusion_metadata") or {}).get("fallback")
        cases.append(Case(item["question"], [d.page_content for d in docs], relevant, fallback))
    return cases


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


@dataclass
class Result:
    provider: str
    model: str
    cases: int = 0
    scored: int = 0  # cases with the expected passage among the candidates
    hit1: int = 0
    hit3: int = 0
    reciprocal_ranks: list[float] = field(default_factory=list)
    latencies: list[float] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    scores: list[float] = field(default_factory=list)
    case_scores: list = field(default_factory=list)  # per case: scores in passage order, or None

    def summary(self) -> dict:
        lat = self.latencies
        return {
            "provider": self.provider,
            "model": self.model,
            "cases": self.cases,
            "scored_cases": self.scored,
            "hit@1": self.hit1,
            "hit@3": self.hit3,
            "mrr": round(statistics.fmean(self.reciprocal_ranks), 4) if self.reciprocal_ranks else None,
            "latency_ms_median": round(statistics.median(lat) * 1000, 1) if lat else None,
            "latency_ms_max": round(max(lat) * 1000, 1) if lat else None,
            "score_min": round(min(self.scores), 4) if self.scores else None,
            "score_max": round(max(self.scores), 4) if self.scores else None,
            "errors": self.errors,
        }


def evaluate(provider: str, model: str, scorer: Scorer, cases: list[Case],
             clock: Callable[[], float] = time.perf_counter) -> Result:
    """Rerank every case through the service's rerank stage and score the ranking."""
    from langchain_core.documents import Document

    from src.rerank_stage import RERANK_TOP_K, rerank_candidates

    result = Result(provider, model)
    for case in cases:
        result.cases += 1
        result.case_scores.append(None)
        if not case.passages:
            continue
        docs = [Document(page_content=p, metadata={"i": i}) for i, p in enumerate(case.passages)]
        full_scores: list[float] = []

        def pair_scorer(pairs):
            started = clock()
            scores = scorer(case.question, [p for _, p in pairs])
            result.latencies.append(clock() - started)
            full_scores[:] = scores
            return scores

        try:
            ranked = rerank_candidates(case.question, docs, pair_scorer)
        except Exception as e:  # one failing call must not end the comparison
            result.errors.append(f"{case.question}: {e}")
            continue
        result.scores.extend(full_scores)
        result.case_scores[-1] = list(full_scores)
        if not case.relevant:
            continue
        result.scored += 1
        # Full order for MRR; rerank_candidates keeps only the top RERANK_TOP_K.
        order = sorted(range(len(full_scores)), key=lambda i: full_scores[i], reverse=True)
        rank = next(pos for pos, i in enumerate(order, start=1) if i in case.relevant)
        result.reciprocal_ranks.append(1.0 / rank)
        top = [d.metadata["i"] for d in ranked["docs"]]
        result.hit1 += int(bool(top) and top[0] in case.relevant)
        result.hit3 += int(any(i in case.relevant for i in top[:RERANK_TOP_K]))
    return result


def _top3(scores: list[float]) -> list[int]:
    from src.rerank_stage import RERANK_TOP_K

    return sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:RERANK_TOP_K]


def parity(base: Result, other: Result) -> dict:
    """Compare other against base on the cases both scored."""
    diffs: list[float] = []
    compared = order_differs = same_set = 0
    for a, b in zip(base.case_scores, other.case_scores):
        if a is None or b is None or len(a) != len(b):
            continue
        compared += 1
        diffs.extend(abs(x - y) for x, y in zip(a, b))
        ta, tb = _top3(a), _top3(b)
        if ta != tb:
            order_differs += 1
            if set(ta) == set(tb):
                same_set += 1
    return {
        "baseline": base.provider,
        "provider": other.provider,
        "compared_cases": compared,
        "compared_passages": len(diffs),
        "max_abs_diff": round(max(diffs), 4) if diffs else None,
        "mean_abs_diff": round(statistics.fmean(diffs), 4) if diffs else None,
        "top3_order_differs": order_differs,
        "top3_order_differs_same_set": same_set,
    }


def format_parity(rows: list[dict]) -> str:
    lines = []
    for r in rows:
        mx = f"{r['max_abs_diff']:.4f}" if r["max_abs_diff"] is not None else "-"
        mean = f"{r['mean_abs_diff']:.4f}" if r["mean_abs_diff"] is not None else "-"
        lines.append(
            f"parity {r['provider']} vs {r['baseline']}: {r['compared_cases']} cases, "
            f"{r['compared_passages']} passages, max diff {mx}, mean diff {mean}, "
            f"top-3 order differs {r['top3_order_differs']} "
            f"(same set {r['top3_order_differs_same_set']})"
        )
    return "\n".join(lines)


def format_table(summaries: list[dict]) -> str:
    header = f"{'provider':14s} {'model':20s} {'hit@1':>7s} {'hit@3':>7s} {'MRR':>6s} {'p50 ms':>8s} {'max ms':>8s} {'scores':>17s} {'err':>4s}"
    lines = [header, "-" * len(header)]
    for s in summaries:
        n = s["scored_cases"]
        mrr = f"{s['mrr']:.3f}" if s["mrr"] is not None else "-"
        p50 = f"{s['latency_ms_median']:.0f}" if s["latency_ms_median"] is not None else "-"
        pmax = f"{s['latency_ms_max']:.0f}" if s["latency_ms_max"] is not None else "-"
        rng = f"{s['score_min']:.3f}..{s['score_max']:.3f}" if s["score_min"] is not None else "-"
        lines.append(
            f"{s['provider']:14s} {s['model'][:20]:20s} {s['hit@1']:>3d}/{n:<3d} {s['hit@3']:>3d}/{n:<3d} "
            f"{mrr:>6s} {p50:>8s} {pmax:>8s} {rng:>17s} {len(s['errors']):>4d}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", choices=("manual", "synthetic"), default="manual")
    parser.add_argument("--providers", default=",".join(ALL_PROVIDERS),
                        help="comma-separated: " + ", ".join(ALL_PROVIDERS))
    parser.add_argument("--model", action="append", default=[], metavar="PROVIDER=MODEL",
                        help="override a model id, e.g. --model cohere=rerank-v5.0-fast")
    parser.add_argument("--out", help="write the JSON report here")
    args = parser.parse_args(argv)

    providers = [p.strip() for p in args.providers.split(",") if p.strip()]
    unknown = [p for p in providers if p not in PROVIDER_FACTORIES]
    if unknown:
        print(f"[rerank-compare] ERROR: unknown providers: {', '.join(unknown)}", flush=True)
        return 2
    models = dict(DEFAULT_MODELS)
    for item in args.model:
        name, _, model = item.partition("=")
        if name not in PROVIDER_FACTORIES or not model:
            print(f"[rerank-compare] ERROR: bad --model '{item}'", flush=True)
            return 2
        models[name] = model

    if args.dataset == "manual":
        from src import config

        index_name = config.PINECONE_INDEX_NAME
        if index_name in PRODUCTION_INDEXES:
            print(f"[rerank-compare] ERROR: refusing to read the production index '{index_name}'.", flush=True)
            return 1
        missing = [k for k in ("PINECONE_API_KEY", "OPENAI_API_KEY") if not os.environ.get(k)]
        if missing:
            print(f"[rerank-compare] ERROR: retrieval needs {', '.join(missing)}.", flush=True)
            return 1
        print(f"[rerank-compare] Index: {index_name} (read only)", flush=True)
        cases = asyncio.run(manual_cases())
    else:
        cases = synthetic_cases()
    missed = sum(1 for c in cases if not c.relevant)
    print(f"[rerank-compare] {len(cases)} cases; expected passage missing from the candidates in {missed}.", flush=True)
    if args.dataset == "manual":
        empty = sum(1 for c in cases if not c.passages)
        vector_only = sum(1 for c in cases if c.fallback == "vector_only")
        if empty or vector_only:
            print(
                f"[rerank-compare] ERROR: {empty} cases without candidates and {vector_only} cases "
                "with vector-only retrieval; sync the sample manual and BM25 index first.",
                flush=True,
            )
            return 1

    results: list[Result] = []
    summaries = []
    for name in providers:
        model = models.get(name, "BAAI/bge-reranker-v2-m3")
        try:
            scorer = PROVIDER_FACTORIES[name](model)
        except ProviderUnavailable as e:
            print(f"[rerank-compare] skip {name}: {e}", flush=True)
            continue
        print(f"[rerank-compare] running {name} ({model})...", flush=True)
        results.append(evaluate(name, model, scorer, cases))
        summaries.append(results[-1].summary())

    if not summaries:
        print("[rerank-compare] ERROR: no reranker could run.", flush=True)
        return 1
    print(format_table(summaries), flush=True)
    parity_rows = [parity(results[0], r) for r in results[1:]]
    if parity_rows:
        print(format_parity(parity_rows), flush=True)
    max_chars = max((len(p) for c in cases for p in c.passages), default=0)
    for s in summaries:
        for err in s["errors"]:
            print(f"[rerank-compare]   {s['provider']} error: {err}", flush=True)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump({"dataset": args.dataset, "cases": len(cases), "missed_by_retrieval": missed,
                       "max_passage_chars": max_chars, "results": summaries, "parity": parity_rows},
                      f, ensure_ascii=False, indent=2)
        print(f"[rerank-compare] Report written to {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
