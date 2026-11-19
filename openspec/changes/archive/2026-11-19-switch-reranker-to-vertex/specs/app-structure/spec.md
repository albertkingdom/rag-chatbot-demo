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

#### Scenario: reranker provider raises on missing project

- **WHEN** `get_reranker_model()` is called and `GCP_PROJECT_ID` is unset
- **THEN** the provider raises `ValueError` mentioning `GCP_PROJECT_ID` and does not construct a client
