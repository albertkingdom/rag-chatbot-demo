# Pinecone Vector Database Structure Report

**Index Name**: carbon-assistant-qa-index
**Generated**: 2026-01-01 21:20:13

## Index Statistics

- **Total Vectors**: 126
- **Dimension**: 1536
- **Index Fullness**: 0.0

## Data Structure

### Vector ID Format

Based on the codebase (`src/build_vector_store.py:112-113`), vector IDs are generated using:

```python
qa_pair_key = (question, answer)
combined_hash = abs(hash(qa_pair_key))
doc_id = f"qa_{combined_hash}"
```

Pattern: `qa_<hash>`

Example: `qa_1234567890`

The hash is computed from a tuple of both the question and answer, ensuring that identical questions with different answers (or vice versa) produce unique IDs. Duplicate Q&A pairs are deduplicated before upserting (`build_vector_store.py:92-122`).

### Metadata Schema

Each vector contains the following metadata fields:


| Field | Type | Description |
|-------|------|-------------|
| `answer` | str | The answer text corresponding to the question |
| `text` | str | The original question text |

## Vector Storage Format

Each vector in Pinecone contains:

1. **ID**: Unique identifier (e.g., `qa_1234567890`)
2. **Values**: 1536-dimensional embedding vector (from OpenAI)
3. **Metadata**: Dictionary containing:
   - `answer`: The answer text
   - `text`: The question text

## Usage in RAG Pipeline

1. **Ingestion** (`build_vector_store.py`):
   - Q&A pairs are extracted from uploaded files (PDF, XLSX, CSV)
   - Duplicates are removed based on (question, answer) tuple
   - Questions are embedded using OpenAI Embeddings (batch: 100 docs/call)
   - Stored with hash of (question, answer) tuple as ID
   - Metadata includes both question and answer

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
