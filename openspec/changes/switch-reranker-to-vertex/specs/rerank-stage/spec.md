## MODIFIED Requirements

### Requirement: Rerank scorer is injected as a callable

The rerank stage SHALL accept the reranker as a `scorer` parameter of type `Callable[[list[tuple[str, str]]], list[float]]`. The retrieval chain SHALL inject `get_reranker_model().score`, the bound `score` method of the hosted reranker client singleton, which sends the pairs to the Vertex AI Ranking API (`semantic-ranker-default-005`) of the configured Google Cloud project, authenticated with Google application default credentials, and returns one score per pair in the order of the input pairs; a record returned without a score SHALL count as 0. The rerank stage SHALL NOT instantiate the reranker client itself, ensuring the singleton is shared with other consumers. The hosted reranker client SHALL NOT load model weights into the service process, SHALL NOT contact the network until it is first asked to score, SHALL bound each HTTP request by a client-side timeout, and SHALL NOT retry a failed request.

#### Scenario: Scorer is injected by the retrieval chain

- **WHEN** `get_retrieval_chain` assembles the pipeline
- **THEN** the rerank stage is constructed with `scorer=get_reranker_model().score` and the rerank stage does not call `get_reranker_model()` internally

#### Scenario: Scores come back in input order

- **WHEN** the hosted reranker returns its results sorted by relevance, for example passage index 2 with score 0.9, then index 0 with score 0.5, then index 1 with score 0.1
- **THEN** the scorer returns `[0.5, 0.1, 0.9]`, one score per input pair in the original pair order

#### Scenario: Record returned without a score

- **WHEN** the hosted reranker returns a record for passage index 1 without a score field
- **THEN** the scorer returns 0 for that passage

#### Scenario: Pairs with different queries are rejected

- **WHEN** the scorer receives pairs whose query strings are not all identical
- **THEN** the scorer raises an error without calling the hosted reranker, because one rerank request carries exactly one query

#### Scenario: Failed request is not retried

- **WHEN** the hosted reranker responds with an HTTP error status
- **THEN** the scorer raises an error after exactly one request, and the error message contains the status code but no request or response content
