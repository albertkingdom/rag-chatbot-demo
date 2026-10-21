# sync-job-outcome Specification

## Purpose

Defines when a manual sync counts as complete, clears the semantic cache only after both the vector store and the BM25 index are rebuilt, and makes a failed sync visible as a failed job.

## Requirements

### Requirement: Clear the semantic cache after a complete sync

When `sync_vector_store` finishes the vector sync and the BM25 rebuild both successfully, the system SHALL clear the semantic cache of the current namespace and SHALL report how many entries were cleared. When the BM25 rebuild fails, the sync SHALL keep its existing warning-only behavior and SHALL NOT clear the cache. A failure while clearing the cache SHALL be logged as a warning and SHALL NOT change the sync result. A sync that reports `status=error` or raises SHALL NOT clear the cache.

#### Scenario: Complete sync clears the cache

- **WHEN** `sync_vector_store` finishes with status `success` and the BM25 rebuild succeeded
- **THEN** `clear_cache` has been called exactly once

#### Scenario: BM25 failure keeps the cache

- **WHEN** `BM25Index.build_from_documents` raises during sync
- **THEN** `sync_vector_store` returns status `success` and `clear_cache` is not called

#### Scenario: Cache clearing failure does not fail the sync

- **WHEN** `clear_cache` raises a Redis connection error during a complete sync
- **THEN** `sync_vector_store` still returns status `success`

#### Scenario: Failed sync keeps the cache

- **WHEN** `sync_vector_store` returns status `error` because a source file cannot be written back
- **THEN** `clear_cache` is not called


<!-- @trace
source: switch-prod-knowledge-base
updated: 2026-10-21
code:
  - architecture.md
  - docs/deployment/releases.md
-->

---
### Requirement: Failed sync exits non-zero

When `src/build_vector_store.py` runs as a program and `sync_vector_store` returns `status=error`, the process SHALL exit with code 1.

#### Scenario: Error result fails the job

- **WHEN** the module's main entry runs and `sync_vector_store` returns `{"status": "error", ...}`
- **THEN** the process exits with code 1

<!-- @trace
source: switch-prod-knowledge-base
updated: 2026-10-21
code:
  - architecture.md
  - docs/deployment/releases.md
-->