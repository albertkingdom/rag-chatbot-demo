# rerank-stage Specification

## Purpose

TBD - created by archiving change 'decouple-rerank-from-retriever'. Update Purpose after archive.

## Requirements

### Requirement: Rerank stage transforms top-M candidates into top-3

The system SHALL provide a rerank stage that receives a query string and a list of candidate `Document` objects (the top-M fused output of `HybridRetriever`), invokes a BGE Reranker `score` callable on `(query, page_content)` pairs, sorts the candidates by descending score, and returns exactly the Top 3 documents. The rerank stage SHALL NOT belong to `HybridRetriever` and SHALL NOT hold a reference to the BM25 index or the vector store.

#### Scenario: Top-M candidates are reranked to top-3

- **WHEN** the rerank stage receives 4 candidates [A, B, C, D] and the scorer returns scores [0.60, 0.95, 0.70, 0.80] for query "test"
- **THEN** the rerank stage returns docs ordered [B, D, C] (top 3 by score) and omits A

##### Example: four candidates, top-3 truncation

- **GIVEN** candidates: A(content="Doc A", score=0.60), B(content="Doc B", score=0.95), C(content="Doc C", score=0.70), D(content="Doc D", score=0.80)
- **WHEN** the rerank stage runs with query "test"
- **THEN** the returned `docs` list is [B, D, C] and `rerank_scores` contains three entries with ranks 1, 2, 3


<!-- @trace
source: decouple-rerank-from-retriever
updated: 2026-06-27
code:
  - docs/diagrams/rag_pipeline.drawio
  - src/hybrid_retriever.py
  - .spectra/touched/decouple-rerank-from-retriever.json
  - .spectra/touched/hybrid-search.json
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
### Requirement: Rerank stage output contract

The rerank stage SHALL return a dict containing `docs` (list of the top-3 `Document` objects sorted by descending reranker score) and `rerank_scores` (a list of objects each with `rank` as a 1-indexed integer, `score` as a string formatted to 4 decimal places, and `content` as the first 100 characters of the document's `page_content` followed by `...`). The rerank stage SHALL NOT assemble `context` or `contexts`; those are assembled by the retrieval chain from document metadata so that application-specific formatting (e.g. using the `answer` metadata field) stays in the chain, keeping the rerank stage a generic scoring/ranking component.

#### Scenario: Rerank stage returns docs and scores only

- **WHEN** the rerank stage selects docs [B, D, C] as the top 3
- **THEN** the returned dict contains exactly the keys `docs` and `rerank_scores`, and does NOT contain `context` or `contexts`


<!-- @trace
source: decouple-rerank-from-retriever
updated: 2026-06-27
code:
  - docs/diagrams/rag_pipeline.drawio
  - src/hybrid_retriever.py
  - .spectra/touched/decouple-rerank-from-retriever.json
  - .spectra/touched/hybrid-search.json
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
### Requirement: Retrieval chain assembles context from answer metadata

The retrieval chain (`get_retrieval_chain`) SHALL, after the rerank stage produces the top-3 `docs`, assemble `context` (the joined answer text) and `contexts` (the list of answer texts) from each document's `answer` metadata field, falling back to empty strings when the field is absent. The final dict returned to `chat_stream` SHALL contain `context`, `contexts`, `docs`, `rerank_scores`, and `fusion_metadata`. This preserves the existing generation contract where the LLM receives answer text rather than question text.

#### Scenario: Context built from answer metadata

- **WHEN** the rerank stage returns docs [B, D, C] whose `metadata["answer"]` values are "B ans", "D ans", "C ans"
- **THEN** the retrieval chain produces `context` equal to "B ans\n\nD ans\n\nC ans" and `contexts` equal to ["B ans", "D ans", "C ans"]


<!-- @trace
source: decouple-rerank-from-retriever
updated: 2026-06-27
code:
  - docs/diagrams/rag_pipeline.drawio
  - src/hybrid_retriever.py
  - .spectra/touched/decouple-rerank-from-retriever.json
  - .spectra/touched/hybrid-search.json
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
### Requirement: Empty candidates short-circuit without invoking scorer

When the rerank stage receives an empty `candidates` list, it SHALL return `{"docs": [], "rerank_scores": []}` immediately and SHALL NOT invoke the scorer callable. This prevents undefined behavior on empty input and preserves the downstream generation "I don't know" path.

#### Scenario: No candidates returned by retriever

- **WHEN** the retriever returns an empty `candidates` list (both BM25 and vector returned nothing usable)
- **THEN** the rerank stage returns empty `docs` and `rerank_scores` without calling the scorer


<!-- @trace
source: decouple-rerank-from-retriever
updated: 2026-06-27
code:
  - docs/diagrams/rag_pipeline.drawio
  - src/hybrid_retriever.py
  - .spectra/touched/decouple-rerank-from-retriever.json
  - .spectra/touched/hybrid-search.json
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
### Requirement: Rerank scorer is injected as a callable

The rerank stage SHALL accept the reranker as a `scorer` parameter of type `Callable[[list[tuple[str, str]]], list[float]]`. The retrieval chain SHALL inject `get_reranker_model().score`, the bound `score` method of the hosted reranker client singleton, which sends the pairs to the Pinecone Inference rerank endpoint running the bge-reranker-v2-m3 model and returns one score per pair in the order of the input pairs. The rerank stage SHALL NOT instantiate the reranker client itself, ensuring the singleton is shared with other consumers. The hosted reranker client SHALL NOT load model weights into the service process, SHALL bound each HTTP request by a client-side timeout, and SHALL NOT retry a failed request.

#### Scenario: Scorer is injected by the retrieval chain

- **WHEN** `get_retrieval_chain` assembles the pipeline
- **THEN** the rerank stage is constructed with `scorer=get_reranker_model().score` and the rerank stage does not call `get_reranker_model()` internally

#### Scenario: Scores come back in input order

- **WHEN** the hosted reranker returns its results sorted by relevance, for example passage index 2 with score 0.9, then index 0 with score 0.5, then index 1 with score 0.1
- **THEN** the scorer returns `[0.5, 0.1, 0.9]`, one score per input pair in the original pair order

#### Scenario: Pairs with different queries are rejected

- **WHEN** the scorer receives pairs whose query strings are not all identical
- **THEN** the scorer raises an error without calling the hosted reranker, because one rerank request carries exactly one query

#### Scenario: Failed request is not retried

- **WHEN** the hosted reranker responds with an HTTP error status
- **THEN** the scorer raises an error after exactly one request

---
### Requirement: Rerank stage is resourced for bounded inference latency

The number of candidate `Document` objects passed into a single rerank call SHALL be bounded by the `FUSION_TOP_M` constant defined in `src/config.py`, and a single rerank call SHALL NOT send more passages than the hosted reranker accepts per request. Each rerank call SHALL be bounded by the rerank timeout defined in `src/config.py`; a call that has not returned within the timeout SHALL be treated as a rerank failure. The service runtime SHALL NOT require a BLAS/OpenMP thread setting for reranking, because inference runs in the hosted service rather than in the service process.

#### Scenario: Candidate count stays within limits

- **WHEN** the retriever returns `FUSION_TOP_M` candidates
- **THEN** the rerank call sends exactly those candidates in one request, and that number does not exceed the hosted reranker's per-request passage limit

#### Scenario: Too many passages for one request

- **WHEN** the scorer receives more pairs than the hosted reranker's per-request passage limit
- **THEN** the scorer raises an error without calling the hosted reranker

#### Scenario: Rerank call exceeds the timeout

- **WHEN** the hosted reranker does not respond within the configured rerank timeout
- **THEN** the retrieval chain stops waiting and handles the call as a rerank failure

---
### Requirement: Rerank failure falls back to fused order

When obtaining the reranker client raises an error, the scorer raises an error, or the rerank call times out, the retrieval chain SHALL NOT fail the request. It SHALL return the first `RERANK_TOP_K` candidates in the fused order produced by `HybridRetriever` as `docs`, an empty `rerank_scores` list, the unchanged `fusion_metadata`, and a `rerank_error` value naming the failure kind: `timeout` when the configured rerank timeout elapsed or the request itself timed out, otherwise `error`. Context and contexts SHALL be assembled from those fallback documents in the same way as for reranked documents. When the retriever returns no candidates, the retrieval chain SHALL return empty `docs` and `rerank_scores` without obtaining the reranker client and without a `rerank_error` value. The retrieval chain SHALL log the failure kind, the error type name and the elapsed time at warning level, and SHALL NOT log passage content, the query text, the error message, a traceback or credentials. When reranking succeeds, the result SHALL NOT contain a `rerank_error` value.

#### Scenario: Hosted reranker raises an error

- **WHEN** the retriever returns candidates [A, B, C, D, E] in fused order and the scorer raises an error
- **THEN** the retrieval chain returns `docs` [A, B, C], `rerank_scores` [], the retriever's `fusion_metadata`, and `rerank_error` "error", and the request continues

#### Scenario: Reranker client cannot be obtained

- **WHEN** the retriever returns candidates and `get_reranker_model()` raises an error
- **THEN** the retrieval chain returns the first `RERANK_TOP_K` fused candidates, `rerank_scores` [], and `rerank_error` "error"

#### Scenario: Hosted reranker times out

- **WHEN** the scorer does not return within the configured rerank timeout
- **THEN** the retrieval chain returns the first `RERANK_TOP_K` fused candidates, `rerank_scores` [], and `rerank_error` "timeout"

#### Scenario: No candidates skip the reranker

- **WHEN** the retriever returns no candidates
- **THEN** the retrieval chain returns empty `docs`, `rerank_scores`, `contexts` and `sources`, an empty `context`, the retriever's `fusion_metadata`, does not call `get_reranker_model()`, and the result has no `rerank_error` value

#### Scenario: Failure log carries no content

- **WHEN** a rerank failure is logged
- **THEN** the log record is at warning level, contains the failure kind, the error type name and the elapsed time, carries no traceback, and does not contain the query text, any passage content, the error message or any API key

#### Scenario: Successful rerank has no error marker

- **WHEN** the scorer returns scores for every candidate within the timeout
- **THEN** the retrieval chain result contains reranked `docs` and non-empty `rerank_scores` and no `rerank_error` value
