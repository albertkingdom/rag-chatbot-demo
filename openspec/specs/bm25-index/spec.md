# bm25-index Specification

## Purpose

TBD - created by archiving change 'hybrid-search'. Update Purpose after archive.

## Requirements

### Requirement: Build BM25 index from Q&A documents

The system SHALL provide a `BM25Index` that builds a BM25Okapi index from a list of LangChain `Document` objects representing the Q&A knowledge base. The build operation SHALL tokenize each document's `page_content`, assign each document's `doc_id` from `metadata['doc_id']`, and persist the corpus and tokenized structure to a JSON file on local disk for fast cold-start loading. The build operation SHALL NOT fall back to hashing `page_content` to produce a `doc_id`; if a document lacks `metadata['doc_id']`, the build SHALL raise an error rather than silently generating an unstable id.

#### Scenario: Build from non-empty corpus
- **WHEN** `build_from_documents` is called with a list of 50 Q&A Documents each carrying `metadata['doc_id']`
- **THEN** the BM25Okapi corpus is constructed from tokenized `page_content`, each document is keyed by its `metadata['doc_id']`, and a JSON persistence file is written containing the corpus and doc_id mapping

#### Scenario: Build from empty corpus
- **WHEN** `build_from_documents` is called with an empty list
- **THEN** the index is marked as not-built and no persistence file is written, so that subsequent search calls trigger the vector-only fallback

#### Scenario: Document missing doc_id is rejected
- **WHEN** `build_from_documents` is called with a Document that lacks `metadata['doc_id']`
- **THEN** the build raises an error and does not produce a hash-derived id


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
### Requirement: Tokenizer performs Chinese word segmentation

The `BM25Index` tokenizer SHALL segment Chinese text into word-level tokens using a Chinese word segmentation step (rather than splitting on whitespace), so that Chinese queries and documents that share terms produce overlapping tokens. Whitespace splitting SHALL NOT be used as the tokenizer, because Chinese text contains no inter-word spaces and would collapse each query or document into a single monolithic token, causing `BM25Okapi.get_scores` to return zero for every document. The same tokenizer SHALL be applied at build time and at search time so that corpus tokens and query tokens are comparable. The tokenizer SHALL normalize full-width characters to half-width and lowercase Latin characters before segmentation so that surface variants of the same term match.

#### Scenario: Chinese query produces non-zero scores for matching documents

- **WHEN** the index is built over Chinese Q&A documents and `search` is called with a Chinese query that shares a term with one of those documents
- **THEN** the matching document's `doc_id` appears in the results with a `bm25_score` greater than zero, ranked no later than documents that share no term with the query

##### Example: password query segments into overlapping terms

- **GIVEN** a document with `page_content` "如果我忘記我的密碼，我該怎麼辦？" indexed in the corpus
- **WHEN** `search` is called with query "忘記密碼怎麼辦？"
- **THEN** the tokenizer yields word-level tokens (including terms such as "密碼" and "忘記") that overlap the document's tokens, and that document's `bm25_score` is greater than zero (not the all-zero output produced by whitespace splitting)

#### Scenario: same tokenizer at build and search

- **WHEN** a document is tokenized during `build_from_documents` and the same text is tokenized during `search`
- **THEN** both paths produce the identical token list, so corpus tokens and query tokens are drawn from the same segmentation


<!-- @trace
source: fix-bm25-chinese-tokenization
updated: 2026-07-01
code:
  - src/bm25_index.py
  - requirements.txt
  - Dockerfile
  - terraform/main.tf
tests:
  - tests/test_bm25_index.py
-->

---
### Requirement: Search returns ranked doc_ids and scores

`BM25Index.search(query, top_n)` SHALL tokenize the query with the same tokenizer used at build time, SHALL return a list of `(doc_id, bm25_score)` tuples sorted by descending score, and SHALL return at most `top_n` results.

#### Scenario: Exact keyword match ranks first
- **WHEN** the corpus contains a document whose `page_content` includes the literal token "4.2" and the query is "4.2"
- **THEN** that document's `doc_id` appears in the result list with a score greater than zero and at a rank no later than documents lacking the token

##### Example: section number precedence
- **GIVEN** documents: D1(content="類別 4.2 排放..."), D2(content="範疇三說明..."), D3(content="4.1 簡介...")
- **WHEN** search("4.2", top_n=3) is called
- **THEN** D1 must be the first result, D2 and D3 must have zero score for the token "4.2" and must not rank above D1


<!-- @trace
source: hybrid-search
updated: 2026-06-27
code:
  - .spectra/changes/hybrid-search.started
  - TODO.md
  - docs/diagrams/rag_pipeline.png
  - .spectra/touched/hybrid-search.json
  - .spectra.yaml
  - docs/diagrams/rag_pipeline.drawio.png
  - src/app.py
  - src/build_vector_store.py
  - docs/interview-guide.md
  - src/hybrid_retriever.py
  - src/config.py
  - docs/diagrams/rag_pipeline.svg
  - src/rerank_stage.py
  - requirements.txt
  - CLAUDE.md
  - architecture.md
  - .spectra/touched/decouple-rerank-from-retriever.json
  - .spectra/changes/decouple-rerank-from-retriever.started
  - docs/diagrams/rag_pipeline.drawio
  - src/bm25_index.py
tests:
  - tests/test_rerank_stage.py
  - tests/test_bm25_index.py
  - tests/test_app_pipeline.py
  - tests/test_hybrid_retriever.py
-->

---
### Requirement: Retrieve original document by doc_id

`BM25Index.get_doc(doc_id)` SHALL return the original `Document` object (including `page_content` and `metadata`) for the given `doc_id`, ensuring the hybrid retriever can hand full documents to the downstream Reranker.

#### Scenario: Valid doc_id lookup
- **WHEN** `get_doc` is called with a `doc_id` that exists in the index
- **THEN** the system returns the matching Document with its metadata intact

#### Scenario: Unknown doc_id
- **WHEN** `get_doc` is called with a `doc_id` not present in the index
- **THEN** the system returns `None` and SHALL NOT raise an exception


<!-- @trace
source: hybrid-search
updated: 2026-06-27
code:
  - .spectra/changes/hybrid-search.started
  - TODO.md
  - docs/diagrams/rag_pipeline.png
  - .spectra/touched/hybrid-search.json
  - .spectra.yaml
  - docs/diagrams/rag_pipeline.drawio.png
  - src/app.py
  - src/build_vector_store.py
  - docs/interview-guide.md
  - src/hybrid_retriever.py
  - src/config.py
  - docs/diagrams/rag_pipeline.svg
  - src/rerank_stage.py
  - requirements.txt
  - CLAUDE.md
  - architecture.md
  - .spectra/touched/decouple-rerank-from-retriever.json
  - .spectra/changes/decouple-rerank-from-retriever.started
  - docs/diagrams/rag_pipeline.drawio
  - src/bm25_index.py
tests:
  - tests/test_rerank_stage.py
  - tests/test_bm25_index.py
  - tests/test_app_pipeline.py
  - tests/test_hybrid_retriever.py
-->

---
### Requirement: Synchronize BM25 index during knowledge base sync

The `sync_vector_store` function SHALL rebuild the BM25 index via `BM25Index.build_from_documents` after the Pinecone upsert/ delete phase completes, using the same in-memory document list that was synced to the vector store, so both indexes stay consistent.

#### Scenario: Sync updates both stores
- **WHEN** an admin uploads a new manual and `sync_vector_store` runs
- **THEN** Pinecone is updated first, followed by `BM25Index.build_from_documents(local_docs)`, and both reflect the new document set afterward

#### Scenario: Build failure does not break vector sync
- **WHEN** `BM25Index.build_from_documents` raises an exception during sync
- **THEN** the vector sync transaction has already committed and remains intact, the BM25 build error is logged to stdout, and the sync job reports a warning rather than failing the entire operation


<!-- @trace
source: hybrid-search
updated: 2026-06-27
code:
  - .spectra/changes/hybrid-search.started
  - TODO.md
  - docs/diagrams/rag_pipeline.png
  - .spectra/touched/hybrid-search.json
  - .spectra.yaml
  - docs/diagrams/rag_pipeline.drawio.png
  - src/app.py
  - src/build_vector_store.py
  - docs/interview-guide.md
  - src/hybrid_retriever.py
  - src/config.py
  - docs/diagrams/rag_pipeline.svg
  - src/rerank_stage.py
  - requirements.txt
  - CLAUDE.md
  - architecture.md
  - .spectra/touched/decouple-rerank-from-retriever.json
  - .spectra/changes/decouple-rerank-from-retriever.started
  - docs/diagrams/rag_pipeline.drawio
  - src/bm25_index.py
tests:
  - tests/test_rerank_stage.py
  - tests/test_bm25_index.py
  - tests/test_app_pipeline.py
  - tests/test_hybrid_retriever.py
-->

---
### Requirement: Load persisted index on startup

The system SHALL load the persisted BM25 JSON file on `BM25Index` initialization when the file exists and is valid, avoiding a full rebuild on every cold start. When the file is missing or corrupt, the system SHALL fall back to an unbuilt state and emit a stdout warning.

#### Scenario: Valid persisted file present
- **WHEN** the persistence file exists and parses successfully
- **THEN** the index is loaded into memory and ready to answer search queries without a rebuild

#### Scenario: Corrupt persisted file
- **WHEN** the persistence file exists but fails to parse
- **THEN** the system logs a warning to stdout, leaves the index in not-built state, and the hybrid retriever follows the vector-only fallback path

<!-- @trace
source: hybrid-search
updated: 2026-06-27
code:
  - .spectra/changes/hybrid-search.started
  - TODO.md
  - docs/diagrams/rag_pipeline.png
  - .spectra/touched/hybrid-search.json
  - .spectra.yaml
  - docs/diagrams/rag_pipeline.drawio.png
  - src/app.py
  - src/build_vector_store.py
  - docs/interview-guide.md
  - src/hybrid_retriever.py
  - src/config.py
  - docs/diagrams/rag_pipeline.svg
  - src/rerank_stage.py
  - requirements.txt
  - CLAUDE.md
  - architecture.md
  - .spectra/touched/decouple-rerank-from-retriever.json
  - .spectra/changes/decouple-rerank-from-retriever.started
  - docs/diagrams/rag_pipeline.drawio
  - src/bm25_index.py
tests:
  - tests/test_rerank_stage.py
  - tests/test_bm25_index.py
  - tests/test_app_pipeline.py
  - tests/test_hybrid_retriever.py
-->