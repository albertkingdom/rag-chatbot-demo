# document-identity Specification

## Purpose

TBD - created by archiving change 'stable-doc-id-uuid'. Update Purpose after archive.

## Requirements

### Requirement: Stable doc_id sourced from a uuid column

The Q&A source data SHALL carry a `uuid` column. The extraction functions in the knowledge-base ingestion path SHALL read this `uuid` value and populate each resulting `Document.metadata['doc_id']` with it. The `doc_id` SHALL be exactly this uuid value so that it is deterministic and identical across processes, machines, and repeated syncs, and SHALL NOT be derived from a process-local hash of document content.

#### Scenario: Extraction populates doc_id from the uuid column
- **WHEN** a source row containing a `uuid` value is extracted into a `Document`
- **THEN** the resulting `Document.metadata['doc_id']` equals that row's `uuid` value

#### Scenario: doc_id is stable across processes
- **WHEN** the same source row is extracted in two separate processes
- **THEN** both processes produce the same `doc_id` for that row


<!-- @trace
source: stable-doc-id-uuid
updated: 2026-06-27
code:
  - docs/diagrams/pinecone_structure_report.md
  - architecture.md
  - src/bm25_index.py
  - TODO.md
  - src/hybrid_retriever.py
  - requirements.txt
  - src/build_vector_store.py
tests:
  - tests/test_document_identity.py
  - tests/test_bm25_index.py
  - tests/test_hybrid_retriever.py
-->

---
### Requirement: Missing uuids are generated and persisted to the source

During `sync_vector_store`, any source row lacking a `uuid` value SHALL be assigned a newly generated uuid (uuid4). The assigned uuid SHALL be written back to the source file before the row is used to build either the vector store or the BM25 index, so that the identity is durable for all future syncs.

#### Scenario: Row without uuid is backfilled
- **WHEN** `sync_vector_store` processes a source file containing rows without a `uuid` value
- **THEN** each such row is assigned a generated uuid, the source file is rewritten with the populated `uuid` column, and that uuid is used as the row's `doc_id`

#### Scenario: Re-sync of unchanged data preserves doc_ids
- **WHEN** `sync_vector_store` runs twice over source data whose rows already have uuids and are otherwise unchanged
- **THEN** the `doc_id` set produced by the second run is identical to the first, so the upsert/delete diff reports no spurious additions or deletions


<!-- @trace
source: stable-doc-id-uuid
updated: 2026-06-27
code:
  - docs/diagrams/pinecone_structure_report.md
  - architecture.md
  - src/bm25_index.py
  - TODO.md
  - src/hybrid_retriever.py
  - requirements.txt
  - src/build_vector_store.py
tests:
  - tests/test_document_identity.py
  - tests/test_bm25_index.py
  - tests/test_hybrid_retriever.py
-->

---
### Requirement: doc_id is persisted to both stores and read consistently

The vector upsert SHALL use the row's uuid as the Pinecone vector id and SHALL include `doc_id` in the vector metadata. The BM25 index SHALL take each document's `doc_id` from `metadata['doc_id']`. Both the BM25 retrieval path and the vector retrieval path SHALL derive the fusion `doc_id` from `metadata['doc_id']`, so that the fusion key for a given document matches across both paths.

#### Scenario: Same document yields identical doc_id on both paths
- **WHEN** a single Q&A document is returned by both the BM25 search and the vector search for a query
- **THEN** the `doc_id` reported by each path is identical, so Reciprocal Rank Fusion combines the two ranks into a single fused entry rather than two separate entries

<!-- @trace
source: stable-doc-id-uuid
updated: 2026-06-27
code:
  - docs/diagrams/pinecone_structure_report.md
  - architecture.md
  - src/bm25_index.py
  - TODO.md
  - src/hybrid_retriever.py
  - requirements.txt
  - src/build_vector_store.py
tests:
  - tests/test_document_identity.py
  - tests/test_bm25_index.py
  - tests/test_hybrid_retriever.py
-->