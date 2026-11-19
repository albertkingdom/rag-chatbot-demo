# load-baseline-script Specification

## Purpose

Defines the load script that generates baseline traffic against the local stack, including the safety checks that keep it off production indexes and the summary it reports.

## Requirements

### Requirement: Load script refuses unsafe preconditions before sending requests

The load script SHALL exit with a non-zero status without sending any request when the target Pinecone index name, read after loading the env file (the repository `.env` by default, overridable with `--env-file`) without overriding existing environment variables, is a production index, when `APP_API_KEY` is not set, or when the question file contains duplicate questions. It SHALL print the target index name before sending requests.

#### Scenario: Production index

- **WHEN** `PINECONE_INDEX_NAME` names a production index
- **THEN** the script exits non-zero and sends no request

#### Scenario: Production index set only in the .env file

- **WHEN** the env file sets `PINECONE_INDEX_NAME` to a production index and the variable is not set in the shell environment
- **THEN** the script exits non-zero and sends no request

#### Scenario: Duplicate questions

- **WHEN** the question file contains the same question twice
- **THEN** the script exits non-zero and sends no request

---
### Requirement: Load script sends questions sequentially and reports outcomes

The load script SHALL send each question as one `/api/v1/chat/stream` request authenticated with the `X-API-Key` header and no session cookie, waiting for each stream to end and then for the configured delay before the next request. After all requests it SHALL print the number of requests per `response_source` and outcome, and SHALL exit with status 0 even when individual requests failed.

#### Scenario: Summary output

- **WHEN** three requests end as rag success, rag success and cache success
- **THEN** the summary reports 2 for rag/success and 1 for cache/success and the exit status is 0

#### Scenario: Non-200 response

- **WHEN** a request receives HTTP 429
- **THEN** the summary counts it under `unknown` with outcome `http_error` and the script continues with the next question

#### Scenario: Failed request counted

- **WHEN** one stream ends with an `error` event
- **THEN** the summary counts it under its response source with outcome error and the script continues with the next question
