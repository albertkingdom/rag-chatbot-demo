## MODIFIED Requirements

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
