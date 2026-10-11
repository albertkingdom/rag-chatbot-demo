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
- A PR must go through code review before merging.

### Commit Messages

- Commit messages MUST NOT mention any AI tools (e.g., Claude, Copilot, ChatGPT, or any other AI assistant).

### Documentation

- `architecture.md` is the canonical description of the RAG request flow, ingestion flow, parameters, and known issues. Parameter values themselves live in code (`src/config.py` and the files listed in its Key Parameters table); behavioral requirements live in `openspec/specs/`.
- Update `architecture.md` in the same change whenever you modify `src/chat_service.py`, `src/query_router.py`, `src/hybrid_retriever.py`, `src/bm25_index.py`, `src/rerank_stage.py`, `src/retrieval_grader.py`, `src/rag_pipeline.py`, `src/cache_service.py`, `src/guardrails.py`, `src/build_vector_store.py`, `src/manual_service.py`, model settings in `src/services.py`, or values in `src/config.py`.
- `README.md` stays an entry point (overview, setup, API, deployment, limits). For a new feature, add one bullet to the README feature list and put the details in `architecture.md`; do not duplicate them.
- Diagrams are written in Mermaid inside Markdown. Retired image diagrams live in `docs/archive/`.
