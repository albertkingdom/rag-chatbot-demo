## MODIFIED Requirements

### Requirement: Hybrid retrieval combines BM25 and vector search

The retrieval pipeline SHALL execute BM25 keyword retrieval and dense vector retrieval in parallel for every incoming rewritten query, and SHALL fuse the two ranked lists via Reciprocal Rank Fusion (RRF). The `HybridRetriever` SHALL return the top `FUSION_TOP_M` fused candidates as a `candidates` list of `Document` objects together with `fusion_metadata`, and SHALL NOT execute reranking or hold any reference to a reranker. Both the BM25 retrieval path and the vector retrieval path SHALL derive each candidate's fusion `doc_id` from `metadata['doc_id']`, so that the same document produces the same fusion key on both paths and RRF combines overlapping documents into a single fused entry.

#### Scenario: Normal hybrid retrieval

- **WHEN** a rewritten query arrives and both the BM25 index and the Pinecone vector store are available
- **THEN** the system retrieves `BM25_TOP_N` candidates from BM25 and `VECTOR_TOP_N` candidates from the vector store in parallel, fuses them into a single ranked list using RRF with constant `RRF_K`, and returns the top `FUSION_TOP_M` candidates as `Document` objects in a `candidates` field

##### Example: RRF fusion of two ranks

- **GIVEN** vector ranks: [A(rank 1), B(rank 2), C(rank 3)] and BM25 ranks: [B(rank 1), D(rank 2), A(rank 3)] with RRF_K=60
- **WHEN** the system applies RRF
- **THEN** the fused score for A = 1/61 + 1/63, B = 1/62 + 1/61, C = 1/63, D = 1/62, and the fused list is ordered by descending fused score: [B, A, D, C]

#### Scenario: Overlapping document fuses via shared doc_id

- **GIVEN** a single document that is returned by both the BM25 search and the vector search for the same query
- **WHEN** the vector path derives its `doc_id` from `metadata['doc_id']` and the BM25 path uses the same `doc_id`
- **THEN** RRF treats both occurrences as the same document and sums their rank contributions into one fused entry rather than producing two separate entries
