"""Offline, end-to-end RAG evaluation with RAGAS.

Runs every question in the golden set through the EXISTING retrieval and
generation chains, collects (question, contexts, answer, ground_truth) samples,
and scores them with RAGAS using the project's existing LLM and embeddings as
the judge. Prints a per-metric summary table, writes a JSON report, and exits
non-zero if any metric falls below its threshold so it can later gate CI.

Usage:
    python -m tests.evaluate_rag

Thresholds can be overridden per metric via env vars, e.g.
    RAG_EVAL_MIN_FAITHFULNESS=1.0 python -m tests.evaluate_rag
"""
import asyncio
import json
import os
import sys

from tabulate import tabulate

DATASET_PATH = "tests/rag_eval_data.json"
REPORT_PATH = "tests/rag_eval_report.json"

METRIC_NAMES = ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]
DEFAULT_THRESHOLD = 0.70

# Credentials required by the RAGAS judge models (LLM + embeddings).
REQUIRED_CREDENTIALS = {
    "OPENROUTER_API_KEY": "LLM (OpenRouter / generation + RAGAS judge)",
    "OPENAI_API_KEY": "embeddings (OpenAI / RAGAS judge)",
}


def load_dataset(path: str) -> list[dict]:
    """Load the golden set. Returns [] if the file is missing or empty."""
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else []


def missing_credentials() -> list[str]:
    """Return the names of required credentials that are not set."""
    return [name for name in REQUIRED_CREDENTIALS if not os.environ.get(name)]


def load_thresholds() -> dict:
    """Per-metric minimum thresholds, overridable via RAG_EVAL_MIN_<METRIC>."""
    thresholds = {}
    for name in METRIC_NAMES:
        override = os.environ.get(f"RAG_EVAL_MIN_{name.upper()}")
        thresholds[name] = float(override) if override is not None else DEFAULT_THRESHOLD
    return thresholds


async def collect_samples(cases: list[dict]) -> list[dict]:
    """Run each case through the existing retrieval + generation chains."""
    from src.app import get_retrieval_chain, get_generation_chain

    retrieval_chain = get_retrieval_chain()
    generation_chain = get_generation_chain()

    samples = []
    for i, case in enumerate(cases, 1):
        question = case["question"]
        bundle = await retrieval_chain.ainvoke(question)
        contexts = bundle.get("contexts", []) or []
        context_text = bundle.get("context", "")
        answer = await generation_chain.ainvoke(
            {"context": context_text, "question": question, "history": ""}
        )
        samples.append(
            {
                "question": question,
                "answer": answer,
                "contexts": contexts,
                "ground_truth": case["ground_truth"],
            }
        )
        print(f"[evaluate] collected {i}/{len(cases)}: {question} ({len(contexts)} contexts)")
    return samples


def _build_judge_llm():
    """Derive a judge LLM from the project's existing LLM.

    Reuses the same model id and OpenRouter credentials as ``get_llm()`` (no new
    provider), but disables streaming and sets an explicit ``max_tokens`` so RAGAS
    receives complete, finished generations. The shared ``get_llm()`` is tuned for
    streamed chat and raises ``LLMDidNotFinishException`` inside RAGAS otherwise.
    """
    from langchain_openai import ChatOpenAI
    from src.app import get_llm

    base = get_llm()
    api_key = base.openai_api_key
    if hasattr(api_key, "get_secret_value"):
        api_key = api_key.get_secret_value()
    return ChatOpenAI(
        model=base.model_name,
        temperature=0,
        streaming=False,
        max_tokens=4096,
        openai_api_key=api_key,
        openai_api_base=base.openai_api_base,
    )


def score_with_ragas(samples: list[dict]) -> dict:
    """Score collected samples with RAGAS, reusing the project's LLM/embeddings."""
    from ragas import evaluate, EvaluationDataset, SingleTurnSample
    from ragas.metrics import (
        faithfulness,
        answer_relevancy,
        context_precision,
        context_recall,
    )
    from ragas.llms import LangchainLLMWrapper
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from src.app import get_embeddings

    dataset = EvaluationDataset(
        samples=[
            SingleTurnSample(
                user_input=s["question"],
                retrieved_contexts=s["contexts"],
                response=s["answer"],
                reference=s["ground_truth"],
            )
            for s in samples
        ]
    )

    result = evaluate(
        dataset=dataset,
        metrics=[faithfulness, answer_relevancy, context_precision, context_recall],
        llm=LangchainLLMWrapper(_build_judge_llm()),
        embeddings=LangchainEmbeddingsWrapper(get_embeddings()),
    )

    # Aggregate per-metric means from the per-sample results.
    df = result.to_pandas()
    metrics = {}
    for name in METRIC_NAMES:
        if name in df.columns:
            metrics[name] = round(float(df[name].mean()), 4)
    return metrics


def build_report(metrics: dict, thresholds: dict, num_cases: int) -> dict:
    """Assemble the machine-readable report and pass/fail gate."""
    passed = all(metrics.get(name, 0.0) >= thresholds[name] for name in metrics)
    return {
        "metrics": metrics,
        "thresholds": {name: thresholds[name] for name in metrics},
        "passed": passed,
        "num_cases": num_cases,
    }


def print_summary(report: dict) -> None:
    """Print an aligned per-metric table marking any failing metric."""
    rows = []
    for name, score in report["metrics"].items():
        threshold = report["thresholds"][name]
        status = "PASS" if score >= threshold else "FAIL ✗"
        rows.append([name, f"{score:.4f}", f"{threshold:.2f}", status])
    print()
    print(tabulate(rows, headers=["metric", "score", "threshold", "status"], tablefmt="github"))
    print()
    print(f"Cases: {report['num_cases']} | Overall: {'PASS ✓' if report['passed'] else 'FAIL ✗'}")


def main() -> int:
    # Step 1: dataset must exist and be non-empty before touching the pipeline.
    cases = load_dataset(DATASET_PATH)
    if not cases:
        print(f"[evaluate] ERROR: dataset '{DATASET_PATH}' is missing or empty. Nothing to evaluate.")
        return 1

    # Step 2: required judge credentials must be present before any scoring.
    missing = missing_credentials()
    if missing:
        for name in missing:
            print(f"[evaluate] ERROR: missing credential {name} — required for {REQUIRED_CREDENTIALS[name]}.")
        return 1

    # Step 3: collect samples from the real pipeline.
    print(f"[evaluate] Collecting samples for {len(cases)} case(s)...")
    samples = asyncio.run(collect_samples(cases))

    # Step 4: score with RAGAS.
    print("[evaluate] Scoring with RAGAS...")
    metrics = score_with_ragas(samples)

    # Step 5: report + threshold gate.
    thresholds = load_thresholds()
    report = build_report(metrics, thresholds, num_cases=len(samples))
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print_summary(report)
    print(f"[evaluate] Report written to '{REPORT_PATH}'.")

    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
