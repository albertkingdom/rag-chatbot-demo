"""Semi-automatic golden-set generator for RAG evaluation.

Reads the existing knowledge-base Q&A sources (reusing the extraction logic in
``src/build_vector_store.py``), samples rows, paraphrases each source ``question``
into a colloquial user phrasing via the existing LLM, and keeps the source
``answer`` verbatim as ``ground_truth``. The result is written to a *draft* file
that a human MUST review and promote to ``tests/rag_eval_data.json`` before it is
used for evaluation.

Usage:
    python -m tests.generate_rag_eval_data [--n N] [--out PATH]
"""
import argparse
import json
import os
import random
import sys

from src.config import DATA_SOURCE_DIR
from src.build_vector_store import (
    extract_from_csv,
    extract_from_xlsx,
    extract_from_pdf,
)
from src.app import get_llm

ACTIVE_DATASET_PATH = "tests/rag_eval_data.json"
DEFAULT_DRAFT_PATH = "tests/rag_eval_data.draft.json"

PARAPHRASE_PROMPT = """You are helping build an evaluation set for a Q&A assistant.
Rewrite the following knowledge-base question into a natural, colloquial way a real
user would phrase it. Keep the original meaning and language exactly; do not add or
remove information. Return ONLY the rewritten question, with no quotes or prefix.

Original question: {question}

Rewritten question:"""


def load_source_qa(data_dir: str) -> list[dict]:
    """Extract Q&A rows from every supported source file under data_dir."""
    rows: list[dict] = []
    if not os.path.isdir(data_dir):
        return rows
    for filename in sorted(os.listdir(data_dir)):
        file_path = os.path.join(data_dir, filename)
        if filename.endswith(".csv"):
            rows.extend(extract_from_csv(file_path))
        elif filename.endswith(".xlsx"):
            rows.extend(extract_from_xlsx(file_path))
        elif filename.endswith(".pdf"):
            rows.extend(extract_from_pdf(file_path))
    return rows


def paraphrase_question(llm, question: str) -> str:
    """Paraphrase a single question; fall back to the original on any error."""
    try:
        response = llm.invoke(PARAPHRASE_PROMPT.format(question=question))
        rewritten = response.content.strip()
        return rewritten or question
    except Exception as e:  # noqa: BLE001 - log and keep the original wording
        print(f"[generate] WARN: paraphrase failed, keeping original ({e})")
        return question


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a draft RAG eval golden set.")
    parser.add_argument("--n", type=int, default=10, help="Number of cases to sample (default: 10)")
    parser.add_argument("--out", default=DEFAULT_DRAFT_PATH, help=f"Output path (default: {DEFAULT_DRAFT_PATH})")
    args = parser.parse_args()

    # Never overwrite a reviewed, active dataset.
    if os.path.abspath(args.out) == os.path.abspath(ACTIVE_DATASET_PATH) and os.path.exists(ACTIVE_DATASET_PATH):
        print(
            f"[generate] ERROR: refusing to overwrite existing '{ACTIVE_DATASET_PATH}'. "
            f"Write to a draft path (default '{DEFAULT_DRAFT_PATH}') and review before promoting."
        )
        return 1

    rows = load_source_qa(DATA_SOURCE_DIR)
    if not rows:
        print(f"[generate] ERROR: no parseable Q&A rows found under '{DATA_SOURCE_DIR}'. Nothing written.")
        return 1

    sample_size = min(args.n, len(rows))
    sampled = random.sample(rows, sample_size)
    print(f"[generate] Sampling {sample_size} of {len(rows)} available Q&A rows...")

    llm = get_llm()
    cases = []
    for i, row in enumerate(sampled, 1):
        paraphrased = paraphrase_question(llm, row["question"])
        cases.append({"question": paraphrased, "ground_truth": row["answer"]})
        print(f"[generate] {i}/{sample_size}: {paraphrased}")

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(cases, f, ensure_ascii=False, indent=2)

    print(f"\n[generate] Wrote draft with {len(cases)} case(s) to '{args.out}'.")
    print(
        f"[generate] NOTICE: this is a DRAFT. Review and edit the questions, then "
        f"copy it to '{ACTIVE_DATASET_PATH}' before running evaluation."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
