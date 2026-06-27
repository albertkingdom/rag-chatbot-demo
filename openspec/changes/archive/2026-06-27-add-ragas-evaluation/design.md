## Context

The RAG pipeline lives in `src/app.py` and exposes two lazily-constructed LangChain chains: `get_retrieval_chain()` (returns a bundle with `context` string and `contexts` list) and `get_generation_chain()` (a `prompt | llm | StrOutputParser` chain). The judge models already exist: `get_llm()` is an OpenRouter `ChatOpenAI` (google/gemini-2.5-flash) and `get_embeddings()` is `OpenAIEmbeddings`. The repo already has a precedent for offline, script-based evaluation: `tests/evaluate_rerank.py` plus a JSON dataset, run via `python -m` with a SUCCESS/FAILURE summary. RAGAS is the missing piece for measuring the whole pipeline rather than the reranker alone.

### Evaluation timing

RAGAS is **post-hoc and offline** — it scores the pipeline's outputs, it is not a stage inside the pipeline. Two ordering constraints follow:

- **Data-flow stage**: a sample can only be scored once it is complete. `context_precision` / `context_recall` need `contexts`, so they require retrieval to have finished; `faithfulness` / `answer_relevancy` need `answer`, so they require generation to have finished. The evaluator therefore collects the full `{question, contexts, answer, ground_truth}` tuple for every case first, then hands the materialized dataset to `ragas.evaluate(...)` in one pass (see Decision 2 and Risk 1) — it never interleaves scoring with chain calls.
- **Lifecycle stage**: evaluation runs out of band, never on the live chat request path (slow, costs judge-LLM quota). It is invoked on demand — typically after a change that affects retrieval, reranking, prompts, or models — and can later act as a CI gate via `THRESHOLDS`. It also has a prerequisite stage: a reviewed golden set (`tests/rag_eval_data.json`) must exist before evaluation, produced by `tests/generate_rag_eval_data.py` then human-reviewed.

Full order: build knowledge base → generate golden-set draft → human review → (change pipeline) → run pipeline to collect samples → RAGAS scoring → threshold gate.

## Goals / Non-Goals

**Goals**
- Quantify end-to-end RAG quality with RAGAS metrics (faithfulness, answer_relevancy, context_precision, context_recall).
- Reuse existing models as the RAGAS judge — no new model provider.
- Provide a pass/fail threshold gate so the script can later gate CI.

**Non-Goals**
- No live-path integration, no CI wiring, no labeling UI (see proposal Non-Goals).
- No change to retrieval/generation behavior — evaluation is read-only.

## Decisions

**Decision 1: Standalone script, mirroring `evaluate_rerank.py`.**
Put the evaluator at `tests/evaluate_rag.py`, runnable as `python -m tests.evaluate_rag`. Rationale: matches the existing reranker-eval convention, keeps RAGAS (a heavy dependency) off the request path. Alternative considered: a pytest test — rejected because metric scoring is slow, costs API quota, and is non-deterministic, which makes it a poor unit test but a fine on-demand script.

**Decision 2: Reuse `get_retrieval_chain()` and `get_generation_chain()` directly.**
The script imports these factories from `src.app` and calls them per question, reading `contexts` from the retrieval bundle and the streamed/awaited string from the generation chain. Rationale: guarantees the evaluation measures the real pipeline. The generation chain is invoked with `{"context", "question", "history": ""}` (empty history — evaluation cases are standalone questions). Alternative: reconstruct a parallel chain — rejected, it would drift from production behavior.

**Decision 3: Wrap existing models for RAGAS via LangChain wrappers.**
Pass `LangchainLLMWrapper(...)` and `LangchainEmbeddingsWrapper(get_embeddings())` to RAGAS metrics/`evaluate(...)`. Rationale: RAGAS accepts LangChain LLMs/embeddings through its wrapper classes, so we avoid configuring a second provider and reuse existing API keys. Alternative: let RAGAS default to OpenAI gpt models — rejected, introduces an undeclared dependency on a model the project does not otherwise use.

Refinement (found during live verification): the shared `get_llm()` is configured with `streaming=True` and no `max_tokens`, which makes RAGAS raise `LLMDidNotFinishException` (incomplete generations) and produce NaN scores. The judge therefore uses a derived LLM — same model id and OpenRouter credentials as `get_llm()` (still no new provider), but constructed with `streaming=False` and an explicit `max_tokens` (4096) for complete, finished judge outputs. `get_llm()` itself is left unchanged so production chat behavior is unaffected.

**Decision 4: Thresholds and metric set are configuration constants in the script.**
A `THRESHOLDS` dict (default 0.70 per metric) drives the exit-code gate. Rationale: keeps the gate explicit and tunable without code restructuring; CI can override later. Alternative: thresholds in `src/config.py` — deferred, this is test-only tooling and does not belong in app config yet.

**Decision 5: Bootstrap the golden set from existing source Q&A, paraphrasing only the question.**
The knowledge-base sources already are `question`/`answer` pairs (`src/build_vector_store.py` parses CSV/XLSX/PDF into `{question, answer, uuid}`). `tests/generate_rag_eval_data.py` reuses that extraction logic to sample rows, paraphrases each `question` into colloquial phrasing via the existing LLM (`get_llm()`), and copies the source `answer` verbatim as `ground_truth`. Rationale: hand-writing reference answers is slow and risks inventing facts not in the corpus; sourcing `ground_truth` from curated answers keeps it grounded, while paraphrasing the question prevents the evaluation from being a trivial exact-match against the indexed text. Alternatives considered: (a) use raw source questions unchanged — rejected, retrieval would near-always hit because the query equals an indexed document; (b) have the LLM invent both question and answer — rejected, `ground_truth` would no longer be ground truth. The generator writes a **draft** path (e.g. `tests/rag_eval_data.draft.json`) and never overwrites a reviewed `tests/rag_eval_data.json`; a human reviews and promotes the draft.

## Implementation Contract

- **Inputs**: `tests/rag_eval_data.json` — JSON array of `{"question": str, "ground_truth": str}` (≥5 cases). Required env: `OPENROUTER_API_KEY` (LLM) and `OPENAI_API_KEY` (embeddings).
- **Behavior**: `python -m tests.evaluate_rag` loads the dataset, runs each question through `get_retrieval_chain()` and `get_generation_chain()`, assembles samples `{question, answer, contexts: list[str], ground_truth}`, builds a RAGAS-compatible dataset, and runs `ragas.evaluate(...)` with metrics `faithfulness`, `answer_relevancy`, `context_precision`, `context_recall` using the wrapped existing LLM and embeddings.
- **Output shape**: writes `tests/rag_eval_report.json` of the form `{"metrics": {"faithfulness": float, "answer_relevancy": float, "context_precision": float, "context_recall": float}, "thresholds": {...}, "passed": bool, "num_cases": int}` and prints an aligned per-metric table marking any failing metric.
- **Exit codes**: `0` when every metric ≥ its threshold; non-zero when any metric is below threshold, the dataset is missing/empty, or a required API credential is absent (error names the missing credential before any scoring).
- **Failure modes**: empty retrieval → record sample with `contexts: []` and continue; missing dataset/credential → exit non-zero with a descriptive message and no scoring.
- **Generator behavior**: `python -m tests.generate_rag_eval_data [--n N] [--out PATH]` reads Q&A rows from the configured data-source directory (`DATA_SOURCE_DIR`) reusing `src/build_vector_store.py` extraction, samples min(N, available) rows, paraphrases each `question` via `get_llm()`, sets `ground_truth` to the source `answer` verbatim, and writes a JSON array to a draft path (default `tests/rag_eval_data.draft.json`). It prints a notice that the draft MUST be reviewed and promoted to `tests/rag_eval_data.json` before evaluation, and it refuses to overwrite an existing `tests/rag_eval_data.json`. No parseable Q&A rows → print error and exit non-zero without writing.
- **In scope**: `tests/evaluate_rag.py`, `tests/generate_rag_eval_data.py`, `tests/rag_eval_data.json`, adding `ragas` + `datasets` to `requirements.txt`.
- **Out of scope**: any edit to `src/app.py` chains, caching, CI config, or live chat behavior; the generator does not auto-promote its draft and does not run RAGAS itself.

## Risks / Trade-offs

- [RAGAS uses its own event loop / async internals that can clash with the chains' async APIs] → Collect all samples first using the chains' synchronous/awaited invocation in a controlled loop, then hand a fully-materialized dataset to `ragas.evaluate(...)`; do not interleave RAGAS scoring with chain calls.
- [Metric scoring consumes OpenRouter + OpenAI quota and is non-deterministic] → Keep the dataset small (≥5, not hundreds), run on demand only, and treat thresholds as guard rails not exact targets.
- [RAGAS API surface changes across versions] → Pin a known-good `ragas` version range in `requirements.txt` and import metrics by name with a clear failure message if an import is missing.
- [OpenRouter Gemini may be a weaker judge than gpt-4-class models RAGAS assumes] → Acceptable for trend tracking across pipeline changes; document that absolute scores are comparable only within the same judge.
- [LLM paraphrasing could drift a question away from the source answer's meaning] → `ground_truth` stays verbatim from the source answer; the draft is written to a separate path and a human reviews/edits questions before promoting it — the draft is never auto-used.

## Migration Plan

Additive only: new files plus two new dependencies. No data migration, no behavior change. Rollback is deleting the new files and reverting the `requirements.txt` lines.
