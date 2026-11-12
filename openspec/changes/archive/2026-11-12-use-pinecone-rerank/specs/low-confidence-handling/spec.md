## MODIFIED Requirements

### Requirement: Retry keeps the better retrieval

When a retrieval attempt does not pass grading and a retry is performed, the system SHALL keep the retrieval result whose top-1 rerank score is highest across all executed attempts. A retry result SHALL replace the kept result only when its top-1 score is strictly greater, so that on equal scores the earlier result is kept. A result without a top-1 score, because it has no documents or because its rerank failed, SHALL be treated as having the lowest possible score. When the configured maximum number of retries is greater than 1, the system SHALL stop retrying as soon as the kept result passes grading.

#### Scenario: Retry is worse than the original

- **WHEN** the original retrieval has top-1 score 0.28 and the retry has top-1 score 0.05
- **THEN** generation uses the original retrieval result

#### Scenario: Retry is better than the original

- **WHEN** the original retrieval has top-1 score 0.10 and the retry has top-1 score 0.45
- **THEN** generation uses the retry result

##### Example: which attempt is kept

| Original top-1 | Retry top-1 | Kept attempt |
| -------------- | ----------- | ------------ |
| 0.28           | 0.05        | original     |
| 0.10           | 0.45        | retry        |
| 0.20           | 0.20        | original     |
| none (empty)   | 0.15        | retry        |
| 0.20           | none (rerank failed) | original |

#### Scenario: Retrying stops once an attempt passes

- **GIVEN** a maximum of 2 retries and a grading threshold of 0.30
- **WHEN** the original retrieval scores 0.10 and the first retry scores 0.45
- **THEN** no second retry is performed and the first retry result is kept

### Requirement: Low-confidence answers are labelled and carry no sources

The system SHALL treat the final kept retrieval as low confidence when grading of that result fails, meaning its top-1 rerank score is below the grading threshold or it has no top-1 score because it contains no documents or its rerank failed; a score equal to the threshold passes. For a low-confidence retrieval the system SHALL still run generation and SHALL pass a non-empty low-confidence note into the generation input. The note SHALL instruct the model to quote the retrieved material only where it explicitly answers the question; for a question about the CarbonM product itself (features, versions, prices, whether something is supported) that the material does not answer, to state that it cannot confirm rather than speculate or assert that something exists or does not exist; to use general knowledge only for general questions and then briefly; not to invent CarbonM-specific operation steps, screens or menu names; and not to repeat that the knowledge base has no answer. After generation and before the output guardrail runs, the system SHALL prepend a fixed notice followed by one blank line to the answer; the notice SHALL state that the knowledge base has no directly relevant information and that the following text is general reference only. Unless the output guardrail replaces the answer, the delivered answer, the conversation history and the saved conversation record SHALL all contain the notice, the system SHALL NOT emit a sources event, and the response source reported to the client SHALL remain rag. When the output guardrail replaces a low-confidence answer, the existing guardrail behavior SHALL apply unchanged. The fixed notice SHALL NOT match any prompt-injection or personal-information detection rule.

#### Scenario: Score below threshold

- **GIVEN** a grading threshold of 0.30
- **WHEN** the final kept retrieval has top-1 score 0.29
- **THEN** the generation input contains a non-empty low-confidence note, the delivered answer starts with the fixed notice, and the event stream contains no sources event

#### Scenario: Score equal to threshold

- **GIVEN** a grading threshold of 0.30
- **WHEN** the final kept retrieval has top-1 score 0.30
- **THEN** the generation input contains an empty low-confidence note, the answer does not start with the fixed notice, and the sources event is emitted

#### Scenario: No documents retrieved

- **WHEN** both the original and the retry retrieval return no documents
- **THEN** the retrieval is treated as low confidence and generation still runs with an empty context, a non-empty low-confidence note and the fixed notice prepended

#### Scenario: Rerank failed

- **WHEN** the kept retrieval has fallback documents but no top-1 score because its rerank failed
- **THEN** the retrieval is treated as low confidence and generation runs with an empty context, a non-empty low-confidence note and the fixed notice prepended, and no sources event is emitted

#### Scenario: Notice is stored with the turn

- **WHEN** a low-confidence answer is delivered
- **THEN** the history turn and the saved conversation record contain the same text, including the fixed notice, that was streamed to the client

#### Scenario: Guardrail replaces a low-confidence answer

- **WHEN** the output guardrail replaces an answer that was produced from a low-confidence retrieval
- **THEN** the guardrail message is delivered, no history turn is appended, no cache entry is written, the saved record's response source is guardrail, and its metadata contains the retrieval object with low_confidence true

#### Scenario: Callers that omit the note keep working

- **WHEN** the generation chain is invoked without a low-confidence note, as the direct-answer path and the offline evaluation script do
- **THEN** the prompt renders successfully, contains no low-confidence instruction, and is textually identical to the prompt rendered before this change

### Requirement: Retrieval confidence is recorded with each RAG conversation

Every conversation record saved from the RAG path, including one whose answer was replaced by the output guardrail message, SHALL carry a retrieval object in its metadata with these fields: top1_score (the kept result's top-1 score, or null when it has no top-1 score because it has no documents or its rerank failed), attempt_scores (the top-1 score of every executed retrieval in execution order, original first, with null for a result without a top-1 score), retry_count (the number of retrievals executed after the first), kept_attempt (an integer, 0 for the original retrieval, 1 for the first retry, and so on), low_confidence (boolean), threshold (the grading threshold in effect) and retry_skipped (true only when retrying ended because a rewritten query was empty or repeated an earlier query; false when retrying ended because of a rerank failure). Existing metadata fields written by the guardrail path SHALL be preserved. Records for cache hits and for the direct path SHALL NOT carry this object.

#### Scenario: Record after a successful retry

- **GIVEN** a grading threshold of 0.30
- **WHEN** the original retrieval scored 0.10, one retry scored 0.45 and the retry was kept
- **THEN** the saved retrieval object has top1_score 0.45, attempt_scores [0.10, 0.45], retry_count 1, kept_attempt 1, low_confidence false, threshold 0.30 and retry_skipped false

#### Scenario: Record for a low-confidence answer

- **GIVEN** a grading threshold of 0.30
- **WHEN** the original retrieval scored 0.20 and the retry scored 0.05
- **THEN** the saved retrieval object has top1_score 0.20, attempt_scores [0.20, 0.05], retry_count 1, kept_attempt 0, low_confidence true, threshold 0.30 and retry_skipped false

#### Scenario: Record when the retry is skipped

- **GIVEN** a grading threshold of 0.30
- **WHEN** the original retrieval scored 0.20 and the rewritten query equals the original query
- **THEN** the saved retrieval object has top1_score 0.20, attempt_scores [0.20], retry_count 0, kept_attempt 0, low_confidence true, threshold 0.30 and retry_skipped true

#### Scenario: Record when the first rerank fails

- **GIVEN** a grading threshold of 0.30
- **WHEN** the original retrieval's rerank failed
- **THEN** the saved retrieval object has top1_score null, attempt_scores [null], retry_count 0, kept_attempt 0, low_confidence true, threshold 0.30 and retry_skipped false

#### Scenario: Guardrail replacement keeps both kinds of metadata

- **WHEN** the output guardrail replaces a RAG answer because of detected personal information
- **THEN** the saved metadata contains both the guardrail fields and the retrieval object

## ADDED Requirements

### Requirement: Rerank failure is treated as low confidence without retry

When any executed retrieval attempt carries a `rerank_error` value, the system SHALL perform no further retry for that request, because a retry calls the same unavailable reranker and only adds latency. The kept result SHALL be chosen by the existing keep-the-better rule. When the kept result is the attempt whose rerank failed, the retrieval SHALL be low confidence and SHALL follow every existing low-confidence behavior: generation receives an empty context, the fixed notice is prepended, no sources event is emitted, and the answer is not written to the semantic cache.

#### Scenario: First attempt fails to rerank

- **WHEN** the first retrieval attempt returns `rerank_error` "timeout" and `RETRIEVAL_MAX_RETRIES` is 1
- **THEN** the system does not call the retry query rewrite, does not retrieve again, marks the retrieval as low confidence, generates with an empty context, prepends the fixed notice, emits no sources event and does not write the answer to the semantic cache

#### Scenario: Retry attempt fails to rerank

- **GIVEN** `RETRIEVAL_MAX_RETRIES` is 2 and a grading threshold of 0.30
- **WHEN** the first attempt scores 0.10 and the first retry returns `rerank_error` "error"
- **THEN** no second retry is performed, the first attempt is kept, and the retrieval is low confidence
