# app-structure Specification

## Purpose

TBD - created by archiving change 'refactor-app-singletons'. Update Purpose after archive.

## Requirements

### Requirement: Layered module separation

The application SHALL be split into modules with a single responsibility: `src/services.py` (thread-safe singleton providers), `src/rag_pipeline.py` (RAG business logic: prompt, format, rewrite, chat_stream), `src/ui.py` (Gradio Blocks definition + BOM/upload handlers), and `src/app.py` (FastAPI entry point only). `src/app.py` SHALL NOT contain business logic, singleton state, or UI definition beyond the mount wiring. `src/app.py` MAY re-export provider functions from `src/services.py` for backward compatibility with existing imports.

#### Scenario: app.py is an entry point only

- **WHEN** `src/app.py` is inspected
- **THEN** it contains only FastAPI construction, `mount_auth(app)`, `gr.mount_gradio_app`, and the `__main__` guard — no singleton variables, no `get_*` factory bodies, no `_QA_PROMPT`, no Gradio `with gr.Blocks` block, no `chat_stream`/`bom_mapper_func`/`upload_manual_func` definitions

#### Scenario: business logic lives in rag_pipeline

- **WHEN** `src/rag_pipeline.py` is inspected
- **THEN** it contains `_QA_PROMPT`, `_format_docs`, `format_history`, `rewrite_query`, and `chat_stream`, and imports providers from `src/services.py` rather than holding singleton state

#### Scenario: backward-compatible re-exports

- **WHEN** a caller does `from src.app import get_embeddings`
- **THEN** the import succeeds because `src/app.py` re-exports `get_embeddings` from `src/services.py`

### Requirement: Thread-safe singleton providers

Each singleton provider in `src/services.py` SHALL be thread-safe under concurrent first-call access. The provider SHALL use a per-singleton `threading.Lock` with double-checked locking: check-then-lock-then-recheck. Once initialized, subsequent calls SHALL return the cached instance without acquiring the lock for the check path. The provider SHALL NOT reconstruct the singleton on every call.

#### Scenario: concurrent first calls initialize exactly once

- **WHEN** two threads call `get_reranker_model()` simultaneously while the singleton is uninitialized
- **THEN** the BGE model is constructed exactly once and both threads receive the same instance

#### Scenario: subsequent calls return cached instance

- **WHEN** `get_llm()` is called after the first initialization
- **THEN** the same instance is returned without re-entering the lock-protected construction block

#### Scenario: provider raises on missing required config

- **WHEN** `get_embeddings()` is called and `OPENAI_API_KEY` is unset
- **THEN** the provider raises `ValueError` mentioning `OPENAI_API_KEY` (behavior preserved from the original app.py)

### Requirement: No module-level side effects on import

Importing any module under `src/` SHALL NOT trigger network calls, model loading, external service construction, or Redis connection establishment. `src/bom_mapper.py` SHALL NOT construct an LLM at import time; the LLM SHALL be obtained lazily inside `classify_bom_headers` via `get_llm()` from `src/services.py`. `src/ui.py` SHALL NOT establish a Redis connection at import time; the Redis connection and RQ queue SHALL be obtained lazily via `get_redis_conn()` from `src/services.py` inside handlers. `src/rag_pipeline.py` SHALL NOT establish a Redis connection at import time; it SHALL use `get_redis_conn()` inside `chat_stream`.

#### Scenario: importing bom_mapper does not construct an LLM

- **WHEN** `import src.bom_mapper` is executed with no `OPENROUTER_API_KEY` set
- **THEN** the import succeeds without raising and no `ChatOpenAI` instance is constructed

#### Scenario: classify_bom_headers obtains LLM lazily

- **WHEN** `classify_bom_headers` is called
- **THEN** it obtains the LLM via `get_llm()` inside the function body, not from a module-level variable

#### Scenario: importing ui does not establish a Redis connection

- **WHEN** `import src.ui` is executed with no Redis server running
- **THEN** the import succeeds without raising and no `redis.from_url` call is made

#### Scenario: importing rag_pipeline does not establish a Redis connection

- **WHEN** `import src.rag_pipeline` is executed with no Redis server running
- **THEN** the import succeeds without raising and no `redis.from_url` call is made

### Requirement: Single Redis connection source

The application SHALL obtain Redis connections through a single provider `get_redis_conn()` in `src/services.py`. `src/ui.py`, `src/rag_pipeline.py`, and `src/access_control.py` SHALL NOT call `redis.from_url(...)` directly; they SHALL call `get_redis_conn()`. The `REDIS_URL` environment variable SHALL be read once in `src/config.py` as the `REDIS_URL` constant, not re-read in each consumer. `get_redis_conn()` SHALL be a thread-safe lazy singleton (initialize once, cache the connection).

#### Scenario: no direct redis.from_url in consumers

- **WHEN** `grep -n "redis.from_url" src/ui.py src/rag_pipeline.py src/access_control.py` is run
- **THEN** no matches are returned

#### Scenario: get_redis_conn returns one shared instance

- **WHEN** `get_redis_conn()` is called twice
- **THEN** both calls return the same connection instance

#### Scenario: REDIS_URL centralized in config

- **WHEN** `src/config.py` is inspected
- **THEN** it defines `REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")` and consumers import it from there

### Requirement: Existing tests remain green against new structure

The existing test suite SHALL pass after the refactor, with test files updated to patch provider functions on `src.services` (or via the re-export shim) rather than on `src.app` private globals. No test SHALL be deleted; tests SHALL be relocated/updated only where the patch target moved.

#### Scenario: test_app_pipeline patches resolve

- **WHEN** `tests/test_app_pipeline.py` runs after refactor
- **THEN** its `monkeypatch.setattr` calls target `src.services` (or the `src.app` re-export) and the pipeline test passes

#### Scenario: test_rag_stream import fixed

- **WHEN** `tests/test_rag_stream.py` is updated to import from `src.rag_pipeline` (or `src.app` re-export) instead of the non-existent `from app import rag_chain`
- **THEN** the module imports without `ImportError`
