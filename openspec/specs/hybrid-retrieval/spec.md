# hybrid-retrieval Specification

## Purpose

TBD - created by archiving change 'hybrid-search'. Update Purpose after archive.

## Requirements

### Requirement: Hybrid retrieval combines BM25 and vector search

The retrieval pipeline SHALL execute BM25 keyword retrieval and dense vector retrieval in parallel for every incoming rewritten query, and SHALL fuse the two ranked lists via Reciprocal Rank Fusion (RRF). The `HybridRetriever` SHALL return the top `FUSION_TOP_M` fused candidates as a `candidates` list of `Document` objects together with `fusion_metadata`, and SHALL NOT execute reranking or hold any reference to a reranker.

#### Scenario: Normal hybrid retrieval

- **WHEN** a rewritten query arrives and both the BM25 index and the Pinecone vector store are available
- **THEN** the system retrieves `BM25_TOP_N` candidates from BM25 and `VECTOR_TOP_N` candidates from the vector store in parallel, fuses them into a single ranked list using RRF with constant `RRF_K`, and returns the top `FUSION_TOP_M` candidates as `Document` objects in a `candidates` field

##### Example: RRF fusion of two ranks

- **GIVEN** vector ranks: [A(rank 1), B(rank 2), C(rank 3)] and BM25 ranks: [B(rank 1), D(rank 2), A(rank 3)] with RRF_K=60
- **WHEN** the system applies RRF
- **THEN** the fused score for A = 1/61 + 1/63, B = 1/62 + 1/61, C = 1/63, D = 1/62, and the fused list is ordered by descending fused score: [B, A, D, C]


<!-- @trace
source: decouple-rerank-from-retriever
updated: 2026-06-27
code:
  - docs/diagrams/rag_pipeline.drawio
  - src/hybrid_retriever.py
  - .spectra/touched/decouple-rerank-from-retriever.json
  - .spectra/touched/hybrid-search.json
  - docs/interview-guide.md
  - docs/diagrams/rag_pipeline.png
  - .spectra/changes/decouple-rerank-from-retriever.started
  - docs/diagrams/rag_pipeline.drawio.png
  - src/app.py
  - src/rerank_stage.py
  - docs/diagrams/rag_pipeline.svg
  - .spectra/changes/hybrid-search.started
tests:
  - tests/test_app_pipeline.py
  - tests/test_hybrid_retriever.py
  - tests/test_rerank_stage.py
-->

---
### Requirement: Graceful fallback to vector-only retrieval

When the BM25 index is unavailable, malformed, or empty, the system SHALL fall back to pure vector retrieval and SHALL record `fallback: "vector_only"` in the LangSmith trace metadata for the request, without raising an error to the caller.

#### Scenario: BM25 index not built
- **WHEN** `BM25Index.search` raises `RuntimeError` indicating the index is not built
- **THEN** the hybrid retriever catches the error, proceeds with vector-only retrieval, and logs the fallback reason in trace metadata

#### Scenario: Empty BM25 corpus
- **WHEN** the BM25 corpus contains zero documents
- **THEN** the system follows the same vector-only fallback path and records the fallback reason


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
### Requirement: Retrieval parameters are centralized

All hybrid retrieval parameters SHALL be defined as constants in `src/config.py`: `BM25_TOP_N`, `VECTOR_TOP_N`, `RRF_K`, and `FUSION_TOP_M`. The pipeline SHALL read these constants at runtime and SHALL NOT hardcode retrieval limits inside business logic.

#### Scenario: Parameter override takes effect
- **WHEN** `BM25_TOP_N` is changed from 10 to 20 in `src/config.py`
- **THEN** the next retrieval call requests 20 candidates from the BM25 index without code changes elsewhere


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
### Requirement: Observability of retrieval stages

The hybrid retriever SHALL emit per-stage metadata to LangSmith including, at minimum: BM25 scores and doc_ids, vector scores and doc_ids, and RRF fused scores. The retriever SHALL NOT emit `rerank_scores`, since reranking is performed by a separate downstream stage that owns that metadata.

#### Scenario: Trace contains retrieval stages

- **WHEN** a hybrid retrieval completes successfully
- **THEN** the LangSmith span metadata for the retriever includes keys for `bm25_results`, `vector_results`, and `fusion_results`, and does NOT include `rerank_scores`

<!-- @trace
source: decouple-rerank-from-retriever
updated: 2026-06-27
code:
  - docs/diagrams/rag_pipeline.drawio
  - src/hybrid_retriever.py
  - .spectra/touched/decouple-rerank-from-retriever.json
  - .spectra/touched/hybrid-search.json
  - docs/interview-guide.md
  - docs/diagrams/rag_pipeline.png
  - .spectra/changes/decouple-rerank-from-retriever.started
  - docs/diagrams/rag_pipeline.drawio.png
  - src/app.py
  - src/rerank_stage.py
  - docs/diagrams/rag_pipeline.svg
  - .spectra/changes/hybrid-search.started
tests:
  - tests/test_app_pipeline.py
  - tests/test_hybrid_retriever.py
  - tests/test_rerank_stage.py
-->