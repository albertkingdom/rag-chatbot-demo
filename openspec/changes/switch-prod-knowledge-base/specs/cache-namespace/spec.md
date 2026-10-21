## Purpose

Scopes the semantic cache to the Pinecone index in use, so deployments or revisions that read different knowledge bases never serve each other's cached answers.

## ADDED Requirements

### Requirement: Cache keys are scoped by namespace

`PromptCacheService` SHALL accept a `namespace` that defaults to the resolved `PINECONE_INDEX_NAME`. With a non-empty namespace, data keys SHALL be `prompt_cache:<namespace>:<hash>` and the index SHALL be `prompt_cache:<namespace>:index:zset`; with an empty namespace the service SHALL use the unscoped layout `prompt_cache:<hash>` and `prompt_cache:index:zset`. Lookups SHALL only consider entries of their own namespace, and `clear_cache` SHALL remove only the current namespace's entries plus the legacy Set index, and SHALL raise when Redis fails instead of returning 0.

#### Scenario: Default namespace follows the index name

- **WHEN** a `PromptCacheService` is created without `namespace` while `PINECONE_INDEX_NAME` is `carbon-assistant-sample-index`
- **THEN** a cached entry is stored under a key starting with `prompt_cache:carbon-assistant-sample-index:`

#### Scenario: Namespaces do not share entries

- **WHEN** an answer is cached under namespace `index-a`
- **THEN** a lookup with the same embedding under namespace `index-b` misses

#### Scenario: Clearing one namespace keeps the other

- **WHEN** both `index-a` and `index-b` hold entries and `clear_cache` runs under `index-a`
- **THEN** every `index-b` entry still exists

#### Scenario: Redis failure is raised

- **WHEN** Redis raises a connection error during `clear_cache`
- **THEN** `clear_cache` raises instead of returning 0
