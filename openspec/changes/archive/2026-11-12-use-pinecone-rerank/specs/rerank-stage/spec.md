## MODIFIED Requirements

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

## ADDED Requirements

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
