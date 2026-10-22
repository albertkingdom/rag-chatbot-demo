<!-- SPECTRA:START v1.0.2 -->

# Spectra Instructions

This project uses Spectra for Spec-Driven Development(SDD). Specs live in `openspec/specs/`, change proposals in `openspec/changes/`.

## Use `/spectra-*` skills when:

- A discussion needs structure before coding → `/spectra-discuss`
- User wants to plan, propose, or design a change → `/spectra-propose`
- Tasks are ready to implement → `/spectra-apply`
- There's an in-progress change to continue → `/spectra-ingest`
- User asks about specs or how something works → `/spectra-ask`
- Implementation is done → `/spectra-archive`
- Commit only files related to a specific change → `/spectra-commit`

## Workflow

discuss? → propose → apply ⇄ ingest → archive

- `discuss` is optional — skip if requirements are clear
- Requirements change mid-work? Plan mode → `ingest` → resume `apply`

## Parked Changes

Changes can be parked（暫存）— temporarily moved out of `openspec/changes/`. Parked changes won't appear in `spectra list` but can be found with `spectra list --parked`. To restore: `spectra unpark <name>`. The `/spectra-apply` and `/spectra-ingest` skills handle parked changes automatically.

<!-- SPECTRA:END -->

## Development Rules

### Branching

- New features MUST be developed on a new branch created from `master`, `main`, or `develop`.
- Branch naming convention: `feature/{feature-name}`
- Before implementation, there must be a planning document or a completed Spectra flow (`/spectra-propose`).
- Update `README.md` to reflect new features before merging.
- Code review MUST be completed, and every blocking or important finding resolved, before a PR is opened.
- Handle review findings as described in the `review-findings` skill (`.claude/skills/review-findings/`).

### Commit Messages

- Commit messages MUST NOT mention any AI tools (e.g., Claude, Copilot, ChatGPT, or any other AI assistant).

### Documentation

- `architecture.md` is the canonical description of the RAG request flow, ingestion flow, parameters, and known issues. Parameter values themselves live in code (`src/config.py` and the files listed in its Key Parameters table); behavioral requirements live in `openspec/specs/`.
- Update `architecture.md` in the same change whenever you modify `src/chat_service.py`, `src/query_router.py`, `src/hybrid_retriever.py`, `src/bm25_index.py`, `src/rerank_stage.py`, `src/retrieval_grader.py`, `src/rag_pipeline.py`, `src/cache_service.py`, `src/guardrails.py`, `src/build_vector_store.py`, `src/manual_service.py`, model settings in `src/services.py`, or values in `src/config.py`.
- `README.md` stays an entry point (overview, setup, API, deployment, limits). For a new feature, add one bullet to the README feature list and put the details in `architecture.md`; do not duplicate them.
- Diagrams are written in Mermaid inside Markdown. Retired image diagrams live in `docs/archive/`.

## Project Map

| Path | Role |
|---|---|
| `src/chat_service.py` | Request flow: input guardrail → route → (rag: cache → retrieve → rerank → grade/retry) → generate → output guardrail → save; streams NDJSON events |
| `src/query_router.py`, `hybrid_retriever.py`, `bm25_index.py`, `rerank_stage.py`, `retrieval_grader.py` | Routing and retrieval stages |
| `src/cache_service.py`, `chat_history_service.py`, `conversation_db.py` | Redis semantic cache, short-term history, MongoDB conversation log |
| `src/guardrails.py`, `access_control.py` | Input/output guardrails; API key and session authentication |
| `src/build_vector_store.py`, `manual_service.py` | Ingestion: upload, incremental sync of Pinecone and BM25 |
| `src/config.py` | Tunable parameters (see Key Parameters in `architecture.md`) |
| `src/api/routes.py`, `chat_events.py` | HTTP routes and the typed NDJSON event models |
| `frontend/` | React + TypeScript + Vite UI (pnpm) |
| `tests/` | pytest suite (`test_*.py`) plus evaluation scripts that call real services |
| `scripts/` | Evaluation, smoke tests and requirement compilation |
| `openspec/specs/`, `openspec/changes/` | Behavioral specs and change proposals (Spectra) |
| `terraform/` | Cloud Run deployment |

## Commands

```bash
python -m pytest tests -q                                   # backend tests (no API keys needed)
cd frontend && pnpm lint && pnpm build && pnpm exec vitest run --maxWorkers=1
cd terraform && terraform fmt -check -recursive && terraform init -backend=false -input=false && terraform validate
scripts/compile_requirements.sh                             # after editing requirements.in; commit both files
docker compose up --build                                   # run the app locally (needs real API keys in .env)
```

CI (`.github/workflows/ci.yml`) runs the first three on every PR. It does not run RAGAS or call real LLMs. Evaluation (`tests/evaluate_rag.py`, `scripts/eval_sample_manual.py`) is run locally and must never target the production index.

## Specs

- A change that adds or alters behavior needs a Spectra change (`/spectra-propose`) before implementation. Specs describe observable behavior, not code structure.
- One capability per directory under `openspec/specs/`, named in kebab-case after the behavior (`semantic-cache-lookup`, `low-confidence-handling`).
- Requirements use `SHALL`/`SHALL NOT`; each requirement has at least one `#### Scenario:` written as WHEN / THEN.
- Parameter values stay in code (`src/config.py`); specs state the rule ("strictly greater than the threshold"), not the number.
- Archived changes are history. Do not edit them; open a new change instead.
- Docs-only or tooling-only changes (README, `.claude/`, CI wiring) may skip the Spectra flow; say so in the PR description.

## Skills and Hooks

Project skills in `.claude/skills/` (project-specific ones; the `spectra-*`, `langchain-*` and `langgraph-*` skills are general-purpose):

| Skill | Use when |
|---|---|
| `code-review` | Finding problems before a PR or archive; applies this repo's checklist and records the result |
| `review-findings` | Handling the findings: verify, grade, fix with new commits, re-review |
| `unit-test` | Writing or changing tests |
| `create-pr` | Pushing and opening a PR |

Hooks (`.claude/settings.json`, scripts in `.claude/hooks/`) enforce the rules above:

| Hook | Effect |
|---|---|
| `guard-bash.sh` (PreToolUse, Bash) | Blocks `git add -A`/`.`/`-u`, commits on `master`/`main`/`develop`, commit messages with AI attribution, and `gh pr create` / `spectra archive` when HEAD has no recorded code review |
| `remind-docs.sh` (PostToolUse, Edit/Write) | Reminds to update `architecture.md` when editing the files it documents |
| `review-reminder.sh` (Stop) | Once per HEAD, asks for a code review when the branch has unreviewed code commits |
| `mark-reviewed.sh` | Records the review for HEAD in `.claude/state/` (git-ignored); run by the skills, not by hand |
