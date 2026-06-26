## ADDED Requirements

### Requirement: Build BM25 index from Q&A documents

The system SHALL provide a `BM25Index` that builds a BM25Okapi index from a list of LangChain `Document` objects representing the Q&A knowledge base. The build operation SHALL tokenize each document's `page_content`, assign a stable `doc_id` derived from document metadata, and persist the corpus and tokenized structure to a JSON file on local disk for fast cold-start loading.

#### Scenario: Build from non-empty corpus
- **WHEN** `build_from_documents` is called with a list of 50 Q&A Documents
- **THEN** the BM25Okapi corpus is constructed from tokenized `page_content`, and a JSON persistence file is written containing the corpus and doc_id mapping

#### Scenario: Build from empty corpus
- **WHEN** `build_from_documents` is called with an empty list
- **THEN** the index is marked as not-built and no persistence file is written, so that subsequent search calls trigger the vector-only fallback

### Requirement: Search returns ranked doc_ids and scores

`BM25Index.search(query, top_n)` SHALL tokenize the query with the same tokenizer used at build time, SHALL return a list of `(doc_id, bm25_score)` tuples sorted by descending score, and SHALL return at most `top_n` results.

#### Scenario: Exact keyword match ranks first
- **WHEN** the corpus contains a document whose `page_content` includes the literal token "4.2" and the query is "4.2"
- **THEN** that document's `doc_id` appears in the result list with a score greater than zero and at a rank no later than documents lacking the token

##### Example: section number precedence
- **GIVEN** documents: D1(content="類別 4.2 排放..."), D2(content="範疇三說明..."), D3(content="4.1 簡介...")
- **WHEN** search("4.2", top_n=3) is called
- **THEN** D1 must be the first result, D2 and D3 must have zero score for the token "4.2" and must not rank above D1

### Requirement: Retrieve original document by doc_id

`BM25Index.get_doc(doc_id)` SHALL return the original `Document` object (including `page_content` and `metadata`) for the given `doc_id`, ensuring the hybrid retriever can hand full documents to the downstream Reranker.

#### Scenario: Valid doc_id lookup
- **WHEN** `get_doc` is called with a `doc_id` that exists in the index
- **THEN** the system returns the matching Document with its metadata intact

#### Scenario: Unknown doc_id
- **WHEN** `get_doc` is called with a `doc_id` not present in the index
- **THEN** the system returns `None` and SHALL NOT raise an exception

### Requirement: Synchronize BM25 index during knowledge base sync

The `sync_vector_store` function SHALL rebuild the BM25 index via `BM25Index.build_from_documents` after the Pinecone upsert/ delete phase completes, using the same in-memory document list that was synced to the vector store, so both indexes stay consistent.

#### Scenario: Sync updates both stores
- **WHEN** an admin uploads a new manual and `sync_vector_store` runs
- **THEN** Pinecone is updated first, followed by `BM25Index.build_from_documents(local_docs)`, and both reflect the new document set afterward

#### Scenario: Build failure does not break vector sync
- **WHEN** `BM25Index.build_from_documents` raises an exception during sync
- **THEN** the vector sync transaction has already committed and remains intact, the BM25 build error is logged to stdout, and the sync job reports a warning rather than failing the entire operation

### Requirement: Load persisted index on startup

The system SHALL load the persisted BM25 JSON file on `BM25Index` initialization when the file exists and is valid, avoiding a full rebuild on every cold start. When the file is missing or corrupt, the system SHALL fall back to an unbuilt state and emit a stdout warning.

#### Scenario: Valid persisted file present
- **WHEN** the persistence file exists and parses successfully
- **THEN** the index is loaded into memory and ready to answer search queries without a rebuild

#### Scenario: Corrupt persisted file
- **WHEN** the persistence file exists but fails to parse
- **THEN** the system logs a warning to stdout, leaves the index in not-built state, and the hybrid retriever follows the vector-only fallback path
