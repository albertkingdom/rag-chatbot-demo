## MODIFIED Requirements

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

### Requirement: No module-level side effects on import

Importing any module under `src/` SHALL NOT trigger network calls, model loading, external service construction, Redis connection establishment, filesystem writes, background-job submission, or frontend build execution. Connections, queues, model providers, and job runner clients SHALL be obtained lazily inside application-service or request execution.

#### Scenario: importing app does not contact infrastructure

- **WHEN** `src.app` and all `src.api` modules are imported with Redis, MongoDB, model providers, and Cloud Run unavailable
- **THEN** import succeeds without opening a connection, loading a model, submitting a job, or writing an uploaded file

#### Scenario: frontend assets are build artifacts

- **WHEN** the Python application starts from a production image
- **THEN** it serves existing compiled frontend artifacts and does not invoke npm, Vite, or another frontend build tool at runtime

## ADDED Requirements

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
