# app-structure Specification

## Purpose

TBD - created by archiving change 'refactor-app-singletons'. Update Purpose after archive.

## Requirements

### Requirement: Layered module separation

The application SHALL be split into modules with a single responsibility: `src/services.py` SHALL contain thread-safe singleton providers, `src/rag_pipeline.py` SHALL contain RAG domain operations, the chat application service SHALL orchestrate typed chat events and persistence, `src/api/` SHALL contain HTTP routers and schemas, `frontend/` SHALL contain the React user interface, and `src/app.py` SHALL contain application construction and mount wiring only. `src/app.py` SHALL NOT contain business logic, singleton state, API handler bodies, or frontend component definitions. It SHALL retain documented provider re-exports until the compatibility-removal task is completed.

#### Scenario: app.py is an entry point only

- **WHEN** `src/app.py` is inspected after the frontend cutover
- **THEN** it contains FastAPI construction, middleware/router/static mount wiring, compatibility re-exports, and the `__main__` guard, with no business logic or Gradio mount

#### Scenario: HTTP and chat orchestration are independent

- **WHEN** the chat application service is tested
- **THEN** it can accept a message and server-derived history key and emit typed events without constructing a FastAPI or Gradio request

#### Scenario: frontend is independently verifiable

- **WHEN** the frontend unit tests and production build run
- **THEN** they can use mocked API contracts without importing or starting Python UI code

---
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

---
### Requirement: No module-level side effects on import

Importing any module under `src/` SHALL NOT trigger network calls, model loading, external service construction, Redis connection establishment, filesystem writes, background-job submission, or frontend build execution. Connections, queues, model providers, and job runner clients SHALL be obtained lazily inside application-service or request execution.

#### Scenario: importing app does not contact infrastructure

- **WHEN** `src.app` and all `src.api` modules are imported with Redis, MongoDB, model providers, and Cloud Run unavailable
- **THEN** import succeeds without opening a connection, loading a model, submitting a job, or writing an uploaded file

#### Scenario: frontend assets are build artifacts

- **WHEN** the Python application starts from a production image
- **THEN** it serves existing compiled frontend artifacts and does not invoke npm, Vite, or another frontend build tool at runtime

---
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

---
### Requirement: Existing tests remain green against new structure

The existing test suite SHALL pass after the refactor, with test files updated to patch provider functions on `src.services` (or via the re-export shim) rather than on `src.app` private globals. No test SHALL be deleted; tests SHALL be relocated/updated only where the patch target moved.

#### Scenario: test_app_pipeline patches resolve

- **WHEN** `tests/test_app_pipeline.py` runs after refactor
- **THEN** its `monkeypatch.setattr` calls target `src.services` (or the `src.app` re-export) and the pipeline test passes

#### Scenario: test_rag_stream import fixed

- **WHEN** `tests/test_rag_stream.py` is updated to import from `src.rag_pipeline` (or `src.app` re-export) instead of the non-existent `from app import rag_chain`
- **THEN** the module imports without `ImportError`

---
### Requirement: Local web and worker share synchronization data

Docker Compose web and worker services SHALL use the same persistent volumes and DATA_SOURCE_DIR/BM25_INDEX_DIR values for uploaded manuals and BM25 index versions. Source-only development mounts SHALL NOT replace compiled frontend assets or model-cache files.

#### Scenario: A worker reads a manual uploaded by the web service

- **GIVEN** web saves a manual in its configured upload directory
- **WHEN** the worker runs the synchronization task
- **THEN** the same file is readable from the worker and its updated BM25 pointer is visible to web

##### Example: Shared local data mounts

- **GIVEN** both services mount manual_data at /app/uploaded_files and bm25_data at /app/bm25
- **WHEN** web writes /app/uploaded_files/manual.csv and worker writes /app/bm25/bm25_current.txt
- **THEN** each service can read the other's data and /app/frontend/dist/assets remains available from the image
