## MODIFIED Requirements

### Requirement: Thread-safe singleton providers

Each singleton provider in `src/services.py` SHALL be thread-safe under concurrent first-call access. The provider SHALL use a per-singleton `threading.Lock` with double-checked locking: check-then-lock-then-recheck. Once initialized, subsequent calls SHALL return the cached instance without acquiring the lock for the check path. The provider SHALL NOT reconstruct the singleton on every call.

#### Scenario: concurrent first calls initialize exactly once

- **WHEN** two threads call `get_reranker_model()` simultaneously while the singleton is uninitialized
- **THEN** the hosted reranker client is constructed exactly once and both threads receive the same instance

#### Scenario: subsequent calls return cached instance

- **WHEN** `get_llm()` is called after the first initialization
- **THEN** the same instance is returned without re-entering the lock-protected construction block

#### Scenario: provider raises on missing required config

- **WHEN** `get_embeddings()` is called and `OPENAI_API_KEY` is unset
- **THEN** the provider raises `ValueError` mentioning `OPENAI_API_KEY` (behavior preserved from the original app.py)

#### Scenario: reranker provider raises on missing key

- **WHEN** `get_reranker_model()` is called and `PINECONE_API_KEY` is unset
- **THEN** the provider raises `ValueError` mentioning `PINECONE_API_KEY` and does not construct a client

### Requirement: Local web and worker share synchronization data

Docker Compose web and worker services SHALL use the same persistent volumes and DATA_SOURCE_DIR/BM25_INDEX_DIR values for uploaded manuals and BM25 index versions. Source-only development mounts SHALL NOT replace compiled frontend assets.

#### Scenario: A worker reads a manual uploaded by the web service

- **GIVEN** web saves a manual in its configured upload directory
- **WHEN** the worker runs the synchronization task
- **THEN** the same file is readable from the worker and its updated BM25 pointer is visible to web

##### Example: Shared local data mounts

- **GIVEN** both services mount manual_data at /app/uploaded_files and bm25_data at /app/bm25
- **WHEN** web writes /app/uploaded_files/manual.csv and worker writes /app/bm25/bm25_current.txt
- **THEN** each service can read the other's data and /app/frontend/dist/assets remains available from the image
