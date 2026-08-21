## MODIFIED Requirements

### Requirement: Hybrid retrieval combines BM25 and vector search

The retrieval pipeline SHALL execute BM25 keyword retrieval and dense vector retrieval in parallel for every incoming rewritten query, and SHALL fuse the two ranked lists via Reciprocal Rank Fusion (RRF). The `HybridRetriever` SHALL return the top `FUSION_TOP_M` fused candidates as a `candidates` list of `Document` objects together with `fusion_metadata`, and SHALL NOT execute reranking or hold any reference to a reranker. Both the BM25 retrieval path and the vector retrieval path SHALL derive each candidate's fusion `doc_id` from `metadata['doc_id']`, so that the same document produces the same fusion key on both paths and RRF combines overlapping documents into a single fused entry.

The vector search step SHALL retain the full `Document` objects returned by the vector store in a `doc_id`-keyed lookup structure. The candidate assembly step SHALL resolve each fused `doc_id` by checking the BM25 index first, then the vector search lookup, and SHALL NOT issue additional vector store queries to resolve candidates. The `_find_in_vector_docs` fallback method SHALL be removed.

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

#### Scenario: Vector-only candidate resolved without additional query

- **GIVEN** a fused candidate with `doc_id` "X" that exists in the vector search results but not in the BM25 index
- **WHEN** the candidate assembly step resolves `doc_id` "X"
- **THEN** the system retrieves the `Document` from the vector search lookup dictionary and SHALL NOT call `similarity_search` or any other vector store query method

#### Scenario: Candidate not found in either source

- **GIVEN** a fused `doc_id` that is absent from both the BM25 index and the vector search lookup dictionary
- **WHEN** the candidate assembly step attempts to resolve it
- **THEN** the system skips the `doc_id` without error and does not include it in the returned `candidates` list
