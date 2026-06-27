## Why

The RAG pipeline (hybrid retrieval → rerank → generation) currently has no end-to-end quality measurement: we can observe traces in LangSmith but cannot quantify whether retrieved context is relevant or whether answers are grounded. TODO item 7 calls for adopting RAGAS so changes to retrieval, reranking, or prompts can be evaluated against objective metrics instead of spot-checking.

## What Changes

- Add a `tests/rag_eval_data.json` golden dataset of question / ground-truth pairs covering representative user-manual questions.
- Add a semi-automatic golden-set generator (`tests/generate_rag_eval_data.py`) that samples Q&A rows from the existing knowledge-base sources in `uploaded_files/`, uses the existing LLM to paraphrase each source `question` into a colloquial user phrasing while keeping the source `answer` as `ground_truth`, and writes a **draft** `tests/rag_eval_data.json` for human review (the draft is not used until reviewed).
- Add a standalone evaluation script (`tests/evaluate_rag.py`) that runs each question through the **existing** retrieval and generation chains, collects `question`, generated `answer`, retrieved `contexts`, and `ground_truth`, then scores them with RAGAS.
- Compute four RAGAS metrics: `faithfulness`, `answer_relevancy`, `context_precision`, `context_recall`.
- Reuse the project's existing models for the RAGAS judge — the OpenRouter Gemini LLM (`get_llm`) and OpenAI embeddings (`get_embeddings`) — wrapped for RAGAS, so no new model provider is introduced.
- Print a per-metric summary table and write a JSON report to `tests/rag_eval_report.json`; exit non-zero if any metric falls below a configurable threshold so the script can gate CI later.
- Add `ragas` and `datasets` to `requirements.txt`.

## Non-Goals

- Not wiring RAGAS into the live chat request path or CI pipeline in this change — the script is run on demand (mirrors `tests/evaluate_rerank.py`).
- Not building a labeling UI. Ground truth is never invented by an LLM — it is sourced verbatim from existing curated `answer` fields in the knowledge base; only the question wording is paraphrased, and a human MUST review the generated draft before it is used for evaluation.
- Not replacing the existing component-level reranker evaluation (`tests/evaluate_rerank.py`); RAGAS measures the whole pipeline, the reranker eval stays.
- Not changing retrieval, reranking, caching, or generation behavior — evaluation is read-only over the existing chains.

## Capabilities

### New Capabilities

- `rag-evaluation`: Offline, dataset-driven evaluation of the end-to-end RAG pipeline using RAGAS metrics, runnable on demand with a pass/fail threshold gate.

### Modified Capabilities

(none)

## Impact

- Affected specs: new `rag-evaluation` capability
- Affected code:
  - New: tests/evaluate_rag.py, tests/generate_rag_eval_data.py, tests/rag_eval_data.json
  - Modified: requirements.txt
  - Removed: (none)
- Dependencies: adds `ragas` and `datasets` Python packages
- External services: RAGAS judging consumes OpenRouter (LLM) and OpenAI (embeddings) API quota during evaluation runs
