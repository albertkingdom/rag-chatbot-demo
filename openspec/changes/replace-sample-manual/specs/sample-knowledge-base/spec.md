## Purpose

The repository ships a self-authored sample manual that anyone can upload to build the knowledge base, keeps that data isolated from production through a configurable Pinecone index name whose default is a demo index, and grounds the evaluation dataset in the sample manual.

## ADDED Requirements

### Requirement: Bundled sample manual

The repository SHALL include a self-authored sample manual at `examples/carbonm_sample_manual.xlsx` with columns `question` and `answer`, containing at least 50 rows that cover account and login, inventory boundaries and multi-site management, emission sources and emission factors, data entries and review, and reports and inventories. The file SHALL be parseable by the existing XLSX extractor so that uploading it through the document management page produces one knowledge-base entry per row.

#### Scenario: Sample manual parses into Q&A rows

- **WHEN** `extract_from_xlsx` is called on `examples/carbonm_sample_manual.xlsx`
- **THEN** it returns at least 50 entries, each with a non-empty `question` and `answer`
- **AND** no `question` or `answer` retains the `問：` or `答：` prefix

##### Example: row prefix handling

- **GIVEN** a row with question `1. 問：如何新增廠區？` and answer `答：進入「組織設定」頁面後點選「新增廠區」。`
- **WHEN** the row is extracted
- **THEN** the entry is `{"question": "如何新增廠區？", "answer": "進入「組織設定」頁面後點選「新增廠區」。"}`

### Requirement: Configurable Pinecone index name

The system SHALL read the Pinecone index name from the `PINECONE_INDEX_NAME` environment variable and SHALL fall back to `rag-demo-qa-index` when the variable is unset or empty. The chat retrieval path, the manual sync job, and the duplicate-removal scripts SHALL use the same resolved name, and the scripts SHALL load `.env` before resolving it. The production deployment SHALL set `PINECONE_INDEX_NAME` to `carbon-assistant-qa-index` explicitly on both the web service and the sync job from a single shared value.

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

#### Scenario: Production keeps its index

- **WHEN** the Terraform configuration is planned
- **THEN** both the web service and the sync job define `PINECONE_INDEX_NAME` with the value `carbon-assistant-qa-index`

### Requirement: Isolated sample evaluation run

The sample evaluation runner SHALL refuse to run when the resolved index name equals `carbon-assistant-qa-index`, SHALL verify after syncing that both the demo Pinecone index and the BM25 index contain exactly one entry per sample-manual row, and SHALL verify that the set of vector IDs in `carbon-assistant-qa-index` is identical before and after the run.

#### Scenario: Production index name is rejected

- **WHEN** the runner starts with `PINECONE_INDEX_NAME=carbon-assistant-qa-index`
- **THEN** it exits non-zero before any Pinecone write

#### Scenario: Index contents mismatch

- **WHEN** the demo index or the BM25 index holds a different number of entries than the sample manual after syncing
- **THEN** the runner exits non-zero without running the evaluation

### Requirement: Evaluation dataset grounded in the sample manual

The evaluation dataset `tests/rag_eval_data.json` SHALL contain exactly 12 cases, and every case's `ground_truth` SHALL equal, verbatim, the `answer` of one row in the sample manual after prefix stripping. Each of the five sample-manual topics SHALL be represented by at least 2 cases.

#### Scenario: Ground truths match the sample manual

- **WHEN** each `ground_truth` in `tests/rag_eval_data.json` is compared against the extracted sample-manual answers
- **THEN** every `ground_truth` matches one extracted answer exactly
