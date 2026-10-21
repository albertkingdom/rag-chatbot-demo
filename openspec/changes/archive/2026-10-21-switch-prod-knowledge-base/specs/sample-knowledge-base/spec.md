## MODIFIED Requirements

### Requirement: Configurable Pinecone index name

The system SHALL read the Pinecone index name from the `PINECONE_INDEX_NAME` environment variable and SHALL fall back to `rag-demo-qa-index` when the variable is unset or empty. The chat retrieval path, the manual sync job, and the duplicate-removal scripts SHALL use the same resolved name, and the scripts SHALL load `.env` before resolving it. The production deployment SHALL set `PINECONE_INDEX_NAME` to `carbon-assistant-sample-index`, `DATA_SOURCE_DIR` to `/mnt/data/sample/uploaded_files` and `BM25_INDEX_DIR` to `/mnt/data/sample/models` explicitly on both the web service and the sync job from shared values.

#### Scenario: Default index name

- **WHEN** `PINECONE_INDEX_NAME` is not set
- **THEN** the configured index name is `rag-demo-qa-index`

#### Scenario: Overridden index name

- **WHEN** `PINECONE_INDEX_NAME` is set to `my-index`
- **THEN** the configured index name is `my-index`

#### Scenario: Empty value falls back

- **WHEN** `PINECONE_INDEX_NAME` is set to an empty string
- **THEN** the configured index name is `rag-demo-qa-index`

#### Scenario: Sync writes only to the configured index

- **WHEN** `sync_vector_store` runs with the configured index name `rag-demo-qa-index`
- **THEN** every Pinecone index handle it opens is `rag-demo-qa-index`

#### Scenario: Production uses its own index and data directories

- **WHEN** the Terraform configuration is planned
- **THEN** both the web service and the sync job define `PINECONE_INDEX_NAME` as `carbon-assistant-sample-index`, `DATA_SOURCE_DIR` as `/mnt/data/sample/uploaded_files` and `BM25_INDEX_DIR` as `/mnt/data/sample/models`

### Requirement: Isolated sample evaluation run

The sample evaluation runner SHALL refuse to run when the resolved index name is one of the production indexes `carbon-assistant-qa-index` or `carbon-assistant-sample-index`, SHALL verify after syncing that both the demo Pinecone index and the BM25 index contain exactly one entry per sample-manual row, SHALL exit non-zero when hybrid retrieval for any evaluation case falls back or returns no BM25 results, SHALL report how many cases retrieve their ground-truth source row among the fused candidates without failing on misses, and SHALL fail before any Pinecone call when `PINECONE_API_KEY`, `OPENAI_API_KEY` or `OPENROUTER_API_KEY` is missing or empty. The runner SHALL read no index other than the evaluated one unless `--audit-index <name>` is given; with it, the runner SHALL refuse an audit target equal to the evaluated index, and SHALL compare the audited index's vector IDs before and after the run even when the run fails early or raises, exiting non-zero on any difference while letting an original exception propagate.

#### Scenario: Production index names are rejected

- **WHEN** the runner starts with `PINECONE_INDEX_NAME` set to `carbon-assistant-qa-index` or `carbon-assistant-sample-index`
- **THEN** it exits non-zero before any Pinecone call

#### Scenario: No audit by default

- **WHEN** the runner starts without `--audit-index`
- **THEN** it never reads any Pinecone index other than the evaluated one

#### Scenario: Audit survives a failing run

- **WHEN** the runner starts with `--audit-index carbon-assistant-sample-index` and the evaluation raises an exception
- **THEN** the audited index's vector IDs are still compared and the original exception propagates

#### Scenario: Index contents mismatch

- **WHEN** the demo index or the BM25 index holds a different number of entries than the sample manual after syncing
- **THEN** the runner exits non-zero without running the evaluation
