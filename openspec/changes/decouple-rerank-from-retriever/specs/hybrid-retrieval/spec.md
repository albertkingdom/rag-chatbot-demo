## MODIFIED Requirements

### Requirement: Hybrid retrieval combines BM25 and vector search

The retrieval pipeline SHALL execute BM25 keyword retrieval and dense vector retrieval in parallel for every incoming rewritten query, and SHALL fuse the two ranked lists via Reciprocal Rank Fusion (RRF). The `HybridRetriever` SHALL return the top `FUSION_TOP_M` fused candidates as a `candidates` list of `Document` objects together with `fusion_metadata`, and SHALL NOT execute reranking or hold any reference to a reranker.

#### Scenario: Normal hybrid retrieval

- **WHEN** a rewritten query arrives and both the BM25 index and the Pinecone vector store are available
- **THEN** the system retrieves `BM25_TOP_N` candidates from BM25 and `VECTOR_TOP_N` candidates from the vector store in parallel, fuses them into a single ranked list using RRF with constant `RRF_K`, and returns the top `FUSION_TOP_M` candidates as `Document` objects in a `candidates` field

##### Example: RRF fusion of two ranks

- **GIVEN** vector ranks: [A(rank 1), B(rank 2), C(rank 3)] and BM25 ranks: [B(rank 1), D(rank 2), A(rank 3)] with RRF_K=60
- **WHEN** the system applies RRF
- **THEN** the fused score for A = 1/61 + 1/63, B = 1/62 + 1/61, C = 1/63, D = 1/62, and the fused list is ordered by descending fused score: [B, A, D, C]

### Requirement: Observability of retrieval stages

The hybrid retriever SHALL emit per-stage metadata to LangSmith including, at minimum: BM25 scores and doc_ids, vector scores and doc_ids, and RRF fused scores. The retriever SHALL NOT emit `rerank_scores`, since reranking is performed by a separate downstream stage that owns that metadata.

#### Scenario: Trace contains retrieval stages

- **WHEN** a hybrid retrieval completes successfully
- **THEN** the LangSmith span metadata for the retriever includes keys for `bm25_results`, `vector_results`, and `fusion_results`, and does NOT include `rerank_scores`

## REMOVED Requirements

### Requirement: Fused candidate set feeds existing reranker

**Reason**: Reranking is now the responsibility of a dedicated `rerank-stage` capability. The `HybridRetriever` no longer invokes the reranker; the fused top-M candidates are returned to the caller (the retrieval chain), which routes them to the rerank stage.

**Migration**: The guarantee that "the top `FUSION_TOP_M` documents are delivered to the BGE Reranker and the Reranker outputs exactly Top 3" is now specified by the `rerank-stage` capability. The end-to-end behavior (top-M → top-3) is preserved; only the component ownership changes.
