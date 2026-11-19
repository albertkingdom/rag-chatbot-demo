# service-level-metrics Specification

## Purpose

Defines the service-level metrics the backend records for chat requests, retrieval and reranking, so availability, latency, answer quality and rerank degradation can be measured against the SLOs in the SLI/SLO notes.

## Requirements

### Requirement: Each streamed chat request records exactly one outcome

The backend SHALL increment the `chat_requests_total` counter exactly once for every `/api/v1/chat/stream` request whose event stream starts, labelled with `response_source` and `outcome`. The outcome SHALL be `success` when the stream emits a `done` event, `error` when the stream emits an `error` event or the event generator raises an `Exception`, and `cancelled` when the stream ends with neither (for example when the client disconnects, the generator is closed, or `CancelledError` is raised into the stream). The `response_source` label SHALL be the value of the stream's `metadata` event, or `unknown` when the stream ends before a `metadata` event. A response blocked by a guardrail that ends with `done` SHALL count as `success`. Requests rejected before the stream starts (for example HTTP 409 or 503) SHALL NOT be recorded.

#### Scenario: Completed RAG answer

- **WHEN** a chat stream emits a `metadata` event with response source `rag` and then a `done` event
- **THEN** `chat_requests_total{response_source="rag",outcome="success"}` increases by exactly 1 and no other label combination changes

#### Scenario: Outcome classification

- **WHEN** a chat stream ends
- **THEN** the recorded labels follow the classification rules

##### Example: outcome table

| Events observed before the stream ends | response_source | outcome |
| --- | --- | --- |
| delta, metadata(rag), done | rag | success |
| delta, metadata(guardrail), done | guardrail | success |
| status, error | unknown | error |
| generator raises an exception before any event | unknown | error |
| status, then generator closed by client disconnect | unknown | cancelled |
| status, then `CancelledError` raised into the stream | unknown | cancelled |

---
### Requirement: Request latency histograms record successful requests only

The backend SHALL record `chat_response_duration_seconds` and `chat_first_answer_text_seconds` histograms labelled by `response_source`, measured from the moment the stream starts. `chat_response_duration_seconds` SHALL observe the time until the `done` event. `chat_first_answer_text_seconds` SHALL observe the time until the first `delta` event, and SHALL NOT be measured from `status` or any other non-answer event. Both histograms SHALL be observed only for requests whose outcome is `success`; `chat_first_answer_text_seconds` SHALL additionally require at least one `delta` event. Both histograms SHALL share bucket boundaries that include every SLO latency threshold used by the alerting rules.

#### Scenario: Status event does not count as first answer text

- **WHEN** a successful stream emits a `status` event at 0.01 s, its first `delta` at 9.2 s and `done` at 9.6 s
- **THEN** `chat_first_answer_text_seconds` observes about 9.2 s and `chat_response_duration_seconds` observes about 9.6 s

#### Scenario: Failed request is not observed

- **WHEN** a stream emits `delta` events and then an `error` event
- **THEN** neither latency histogram receives a new observation

---
### Requirement: Retrieval outcomes are counted per RAG request

For every chat request that runs retrieval, the backend SHALL increment `rag_retrieval_total` once, labelled `low_confidence` as `true` or `false` according to the retrieval outcome, and SHALL increase `rag_retrieval_retries_total` by the number of retrieval retries performed for that request.

#### Scenario: Low-confidence retrieval after retries

- **WHEN** a RAG request performs two retrieval attempts and ends with a low-confidence outcome
- **THEN** `rag_retrieval_total{low_confidence="true"}` increases by 1 and `rag_retrieval_retries_total` increases by 1

---
### Requirement: Rerank duration is recorded with its outcome

The backend SHALL observe `rag_rerank_duration_seconds` once for every rerank attempt, including an attempt that fails because the reranker client cannot be built, labelled `outcome` as `ok` when reranking succeeds, `timeout` when the rerank timeout elapses or the HTTP request times out, and `error` for any other failure, including failure to build the reranker client. When there are no candidates and reranking is not called, the histogram SHALL NOT be observed.

#### Scenario: Rerank timeout falls back and is recorded

- **WHEN** a rerank call exceeds the configured rerank timeout
- **THEN** the request continues with the fused order and `rag_rerank_duration_seconds_count{outcome="timeout"}` increases by 1

#### Scenario: Reranker client cannot be built

- **WHEN** building the reranker client raises an exception
- **THEN** the request continues with the fused order and `rag_rerank_duration_seconds_count{outcome="error"}` increases by 1

#### Scenario: No candidates

- **WHEN** retrieval returns no candidates
- **THEN** `rag_rerank_duration_seconds` receives no observation

---
### Requirement: Metrics use bounded labels and do not change responses

Metric labels SHALL be limited to `response_source`, `outcome` and `low_confidence`, each with a fixed set of values; session identifiers, user input and document content SHALL NOT appear in any label. Recording metrics SHALL NOT change the content, order or HTTP status of chat stream events, and a failure inside metric recording SHALL NOT fail the request.

#### Scenario: Metric recording failure

- **WHEN** recording a metric raises an exception while a stream is being served
- **THEN** every event is still forwarded to the client in order and the request outcome is unaffected

#### Scenario: Event stream unchanged

- **WHEN** the same chat request is served with metrics recording in place
- **THEN** the NDJSON events emitted are identical in type, order and content to the events produced by the chat service
