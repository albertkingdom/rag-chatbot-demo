## MODIFIED Requirements

### Requirement: Rerank stage transforms top-M candidates into top-3

The system SHALL provide a rerank stage that receives a query string, a list of candidate `Document` objects (the top-M fused output of `HybridRetriever`) and optionally a function giving the passages to score for each candidate. When no such function is given, or the function gives no passage for a candidate, that candidate SHALL have exactly one passage, its `page_content`. The stage SHALL invoke a BGE Reranker `score` callable exactly once, on the `(query, passage)` pairs of all candidates, SHALL raise an error when the scorer returns a different number of scores than the pairs it received, SHALL give each candidate the highest score among its passages, SHALL sort the candidates by descending score, and SHALL return exactly the Top 3 documents. The rerank stage SHALL NOT belong to `HybridRetriever` and SHALL NOT hold a reference to the BM25 index or the vector store.

#### Scenario: Top-M candidates are reranked to top-3

- **WHEN** the rerank stage receives 4 candidates [A, B, C, D] and the scorer returns scores [0.60, 0.95, 0.70, 0.80] for query "test"
- **THEN** the rerank stage returns docs ordered [B, D, C] (top 3 by score) and omits A

##### Example: four candidates, top-3 truncation

- **GIVEN** candidates: A(content="Doc A", score=0.60), B(content="Doc B", score=0.95), C(content="Doc C", score=0.70), D(content="Doc D", score=0.80)
- **WHEN** the rerank stage runs with query "test"
- **THEN** the returned `docs` list is [B, D, C] and `rerank_scores` contains three entries with ranks 1, 2, 3

#### Scenario: Candidate scored on several passages

- **WHEN** each candidate has two passages and the scorer is called once
- **THEN** each candidate's score is the higher of its two passage scores

##### Example: highest passage score wins

| Candidate | Passage scores | Candidate score |
| --- | --- | --- |
| A | 0.006, 0.421 | 0.421 |
| B | 0.591, 0.200 | 0.591 |
| C | 0.000, 0.079 | 0.079 |

- **THEN** the returned order is [B, A, C] and the scorer received 6 pairs in one call

#### Scenario: Scorer returns the wrong number of scores

- **WHEN** the scorer receives 6 pairs and returns 3 scores
- **THEN** the rerank stage raises an error, and the retrieval chain handles it as a rerank failure

### Requirement: Rerank stage is resourced for bounded inference latency

The number of candidate `Document` objects passed into a single rerank call SHALL be bounded by the `FUSION_TOP_M` constant defined in `src/config.py`, and the total number of passages sent in a single rerank call, counting every passage of every candidate, SHALL NOT exceed what the hosted reranker accepts per request. Each rerank call SHALL be bounded by the rerank timeout defined in `src/config.py`; a call that has not returned within the timeout SHALL be treated as a rerank failure. The service runtime SHALL NOT require a BLAS/OpenMP thread setting for reranking, because inference runs in the hosted service rather than in the service process.

#### Scenario: Passage count stays within limits

- **WHEN** the retriever returns `FUSION_TOP_M` candidates and each has a question and an answer
- **THEN** the rerank call sends their two passages each, `2 × FUSION_TOP_M` passages, in one request, and that number does not exceed the hosted reranker's per-request passage limit

#### Scenario: Too many passages for one request

- **WHEN** the scorer receives more pairs than the hosted reranker's per-request passage limit
- **THEN** the scorer raises an error without calling the hosted reranker

#### Scenario: Rerank call exceeds the timeout

- **WHEN** the hosted reranker does not respond within the configured rerank timeout
- **THEN** the retrieval chain stops waiting and handles the call as a rerank failure

## ADDED Requirements

### Requirement: FAQ entries are reranked on question and answer

The retrieval chain SHALL rerank each candidate FAQ entry on two passages, its question text (the candidate's `page_content`) and its question text followed by one space and its answer (the `answer` metadata), and SHALL use the higher score for ordering, for the reported rerank scores and for retrieval grading. A candidate whose answer is missing, empty or whitespace only SHALL be scored on its question text only. The context and sources built from the reranked documents SHALL be unchanged in form.

#### Scenario: Detail that appears only in the answer

- **GIVEN** a candidate whose question is "密碼有什麼規則？" and whose answer states the password length and character rules
- **WHEN** the user asks "CarbonM 的密碼長度與字元組成有什麼規定？"
- **THEN** the candidate is scored on both its question and its question plus answer, and its score is the higher of the two

#### Scenario: Candidate without an answer

- **WHEN** a candidate has no answer metadata, or its answer is empty or whitespace only
- **THEN** it is scored on its question text only
