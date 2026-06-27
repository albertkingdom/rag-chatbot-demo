# Pinecone Vector Database Structure Report

**Index Name**: carbon-assistant-qa-index
**Generated**: 2026-01-01 21:20:13

## Index Statistics

- **Total Vectors**: 126
- **Dimension**: 1536
- **Index Fullness**: 0.0

## Data Structure

### Vector ID Format

Vector IDs are the stable `uuid` carried by each source row. During
`sync_vector_store`, rows lacking a `uuid` are assigned a `uuid4()` and the
value is written back to the source file (`ensure_uuids_in_source`), so the
identity is durable across re-syncs. The same uuid is used as the Pinecone
vector id, stored in vector metadata as `doc_id`, and used as the BM25
`doc_id` — so both retrieval paths fuse on a matching key.

Pattern: `<uuid4>`

Example: `0037d7b5-2376-40f4-bf58-d892b3b2a423`

> Historical note: IDs were previously `qa_<hash>` derived from
> `abs(hash((question, answer)))`. Python's string `hash()` is salted per
> process (`PYTHONHASHSEED`), so the build-time and query-time ids did not
> match and RRF fusion silently degraded. The uuid scheme replaces this.

Exact duplicate Q&A pairs (same question AND answer) are still deduplicated
before upserting; rows sharing a question but differing in answer have
distinct uuids and are both retained.

### Metadata Schema

Each vector contains the following metadata fields:


| Field | Type | Description |
|-------|------|-------------|
| `answer` | str | The answer text corresponding to the question |
| `text` | str | The original question text |
| `doc_id` | str | The stable uuid (identical to the vector id); read by the retriever to fuse with BM25 |

## Vector Storage Format

Each vector in Pinecone contains:

1. **ID**: The row's stable uuid (e.g., `0037d7b5-2376-40f4-bf58-d892b3b2a423`)
2. **Values**: 1536-dimensional embedding vector (from OpenAI)
3. **Metadata**: Dictionary containing:
   - `answer`: The answer text
   - `text`: The question text
   - `doc_id`: The stable uuid (identical to the vector id)

## Usage in RAG Pipeline

1. **Ingestion** (`build_vector_store.py`):
   - Q&A pairs are extracted from uploaded files (PDF, XLSX, CSV)
   - Missing `uuid`s are generated and written back to the source file
   - Exact duplicates are removed based on (question, answer) tuple
   - Questions are embedded using OpenAI Embeddings (batch: 100 docs/call)
   - Stored with the row's stable uuid as ID
   - Metadata includes question, answer, and `doc_id` (the uuid)

2. **Retrieval** (`app.py`):
   - User query is embedded
   - Top 10 similar vectors retrieved (k=10)
   - Reranked to top 3 using BGE Reranker
   - Context used for LLM generation

## Code References

- Vector store sync: `src/build_vector_store.py:60-172`
- Deduplication logic: `src/build_vector_store.py:92-122`
- Metadata structure: `src/build_vector_store.py:116-120`
- Retrieval: `src/app.py` (chat_stream function)
