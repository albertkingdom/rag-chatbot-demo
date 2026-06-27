## ADDED Requirements

### Requirement: Hybrid retrieval combines BM25 and vector search

The retrieval pipeline SHALL execute BM25 keyword retrieval and dense vector retrieval in parallel for every incoming rewritten query, and SHALL fuse the two ranked lists via Reciprocal Rank Fusion (RRF) before handing the fused candidate set to the downstream BGE Reranker.

#### Scenario: Normal hybrid retrieval
- **WHEN** a rewritten query arrives and both the BM25 index and the Pinecone vector store are available
- **THEN** the system retrieves `BM25_TOP_N` candidates from BM25 and `VECTOR_TOP_N` candidates from the vector store in parallel and fuses them into a single ranked list using RRF with constant `RRF_K`

##### Example: RRF fusion of two ranks
- **GIVEN** vector ranks: [A(rank 1), B(rank 2), C(rank 3)] and BM25 ranks: [B(rank 1), D(rank 2), A(rank 3)] with RRF_K=60
- **WHEN** the system applies RRF
- **THEN** the fused score for A = 1/61 + 1/63, B = 1/62 + 1/61, C = 1/63, D = 1/62, and the fused list is ordered by descending fused score: [B, A, D, C]

### Requirement: Fused candidate set feeds existing reranker

The hybrid pipeline SHALL deliver the top `FUSION_TOP_M` (default 10) documents from the fused ranking to the BGE Reranker, and the Reranker SHALL output exactly the Top 3 documents, preserving the existing generation contract.

#### Scenario: Reranker receives fused candidates
- **WHEN** the fused list contains more than 10 documents
- **THEN** the system truncates to the top 10 by fused score and passes only those to the Reranker

#### Scenario: Fused list shorter than cap
- **WHEN** the fused list contains fewer than 10 unique documents
- **THEN** the system passes the entire fused list to the Reranker without padding

### Requirement: Graceful fallback to vector-only retrieval

When the BM25 index is unavailable, malformed, or empty, the system SHALL fall back to pure vector retrieval and SHALL record `fallback: "vector_only"` in the LangSmith trace metadata for the request, without raising an error to the caller.

#### Scenario: BM25 index not built
- **WHEN** `BM25Index.search` raises `RuntimeError` indicating the index is not built
- **THEN** the hybrid retriever catches the error, proceeds with vector-only retrieval, and logs the fallback reason in trace metadata

#### Scenario: Empty BM25 corpus
- **WHEN** the BM25 corpus contains zero documents
- **THEN** the system follows the same vector-only fallback path and records the fallback reason

### Requirement: Retrieval parameters are centralized

All hybrid retrieval parameters SHALL be defined as constants in `src/config.py`: `BM25_TOP_N`, `VECTOR_TOP_N`, `RRF_K`, and `FUSION_TOP_M`. The pipeline SHALL read these constants at runtime and SHALL NOT hardcode retrieval limits inside business logic.

#### Scenario: Parameter override takes effect
- **WHEN** `BM25_TOP_N` is changed from 10 to 20 in `src/config.py`
- **THEN** the next retrieval call requests 20 candidates from the BM25 index without code changes elsewhere

### Requirement: Observability of retrieval stages

The hybrid retriever SHALL emit per-stage metadata to LangSmith including, at minimum: BM25 scores and doc_ids, vector scores and doc_ids, RRF fused scores, and the final reranked scores. This enables per-stage debugging and tuning.

#### Scenario: Trace contains all stages
- **WHEN** a hybrid retrieval completes successfully
- **THEN** the LangSmith span metadata includes keys for `bm25_results`, `vector_results`, `fusion_results`, and `rerank_scores`
