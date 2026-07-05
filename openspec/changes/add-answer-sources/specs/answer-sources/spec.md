## ADDED Requirements

### Requirement: Deduplicated source list built from original QA content

The system SHALL build a `sources` list of strings from the reranked top-3 documents. For each document, the system SHALL take the `text` metadata field (falling back to `page_content` when `text` is absent) as the original question and the `answer` metadata field as the original answer, and combine them as `"Q: <question> A: <answer>"`. When the combined string exceeds 100 characters, the system SHALL truncate it to the first 100 characters and append `"..."`. When either the question or the answer is absent or empty, the system SHALL fall back to the `doc_id` metadata field instead of the combined string. The system SHALL remove duplicate entries while preserving first-seen order.

#### Scenario: Sources built from original Q&A content

- **WHEN** the rerank stage returns docs [B, D, C] whose `text`/`answer` metadata pairs are ("How to reset password?", "Click forgot password."), ("What is CarbonM?", "A carbon management platform."), ("How to reset password?", "Click forgot password.")
- **THEN** the `sources` list is `["Q: How to reset password? A: Click forgot password.", "Q: What is CarbonM? A: A carbon management platform."]` (duplicate removed, first occurrence order preserved)

##### Example: duplicate QA pair across two chunks

- **GIVEN** docs: B(text="How to reset password?", answer="Click forgot password."), D(text="What is CarbonM?", answer="A carbon management platform."), C(text="How to reset password?", answer="Click forgot password.")
- **WHEN** sources are built
- **THEN** `sources` equals `["Q: How to reset password? A: Click forgot password.", "Q: What is CarbonM? A: A carbon management platform."]`

#### Scenario: Long QA content is truncated to 100 characters

- **WHEN** a document's combined `"Q: <question> A: <answer>"` string is longer than 100 characters
- **THEN** that document contributes only the first 100 characters followed by `"..."` to the `sources` list

##### Example: long answer truncated

- **GIVEN** a document with text="重設密碼的信件可以撐多久？" and a 90-character answer, whose combined "Q: ... A: ..." string is 120 characters long
- **WHEN** sources are built
- **THEN** the contributed source string is exactly 103 characters long (100 characters plus "...") and ends with "..."

#### Scenario: Fallback to doc_id when question or answer metadata is missing

- **WHEN** a reranked document has an empty or missing `text`/`page_content` question, or an empty or missing `answer`, and `doc_id` = "faq-042"
- **THEN** that document contributes `"faq-042"` to the `sources` list instead of a partial or empty QA string

### Requirement: Streamed answer includes an appended source list

The system SHALL append a source list block to the final streamed answer text whenever the answer was produced via the RAG retrieval path (live generation or cache hit) and the `sources` list is non-empty. The block SHALL start with a "參考資料：" header followed by one `- <source>` line per entry, separated from the answer by a blank line. When `sources` is empty, the system SHALL NOT append any block and the streamed text SHALL be unchanged from the answer text alone.

#### Scenario: RAG-generated answer gets a source block appended

- **WHEN** `chat_stream` completes generation with `full_response` = "密碼可以在登入頁面重設。" and `sources` = ["密碼忘記了要怎麼重設啊？"]
- **THEN** the final streamed text equals "密碼可以在登入頁面重設。\n\n參考資料：\n- 密碼忘記了要怎麼重設啊？"

#### Scenario: Cache-hit answer also gets a source block appended

- **WHEN** a cache hit returns `cached_answer` = "密碼可以在登入頁面重設。" and the cached entry's `sources` = ["密碼忘記了要怎麼重設啊？"]
- **THEN** the final streamed text equals "密碼可以在登入頁面重設。\n\n參考資料：\n- 密碼忘記了要怎麼重設啊？", identical in shape to the live-generation case

#### Scenario: Guardrail and off-topic responses are unaffected

- **WHEN** `chat_stream` returns a guardrail message or an off-topic message (no RAG retrieval performed)
- **THEN** the streamed text SHALL NOT contain a "參考資料：" block

#### Scenario: Empty sources list appends nothing

- **WHEN** `sources` is an empty list for a given RAG response
- **THEN** the streamed text equals the answer text alone, with no "參考資料：" block appended

### Requirement: Cached responses store the source list

The system SHALL persist the `sources` list as part of the cached response entry when writing to the prompt cache, and SHALL include the `sources` list when reading a cache hit, so that a cache hit can render the same source block as a live RAG response without re-querying the retriever.

#### Scenario: Cache write includes sources

- **WHEN** a RAG response with `sources` = ["密碼忘記了要怎麼重設啊？"] is cached
- **THEN** the cached entry stored in Redis contains a `sources` field equal to `["密碼忘記了要怎麼重設啊？"]`

#### Scenario: Cache read returns sources

- **WHEN** a cached entry with `sources` = ["密碼忘記了要怎麼重設啊？"] is read back on a cache hit
- **THEN** the returned cache response dict contains `sources` equal to `["密碼忘記了要怎麼重設啊？"]`

### Requirement: Deployment requires a one-time prompt cache flush

Deploying this capability SHALL require a one-time flush of all existing entries under the `prompt_cache:` key namespace in Redis, performed in the same maintenance window as the code deployment, because pre-existing cache entries do not contain the `sources` field and the system SHALL NOT provide a fallback for reading entries missing this field.

#### Scenario: Pre-deployment flush removes legacy entries

- **WHEN** the deployment runbook is executed for this change
- **THEN** all keys matching `prompt_cache:*` are deleted from Redis before the new code version starts serving traffic
