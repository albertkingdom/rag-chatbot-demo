## ADDED Requirements

### Requirement: chat_stream measures total response time

The system SHALL record a start timestamp at the beginning of `chat_stream` and SHALL compute the elapsed time in seconds, rounded to one decimal place, at the point each final response text is fully assembled (before that text's character-by-character streaming loop begins). The elapsed time SHALL cover the entire span from `chat_stream` invocation to that point, including guardrail checks, intent classification, cache lookups, retrieval, reranking, and generation as applicable to that response path.

#### Scenario: Elapsed time excludes the final answer's own typing animation

- **WHEN** `chat_stream` finishes assembling a final answer 3.2 seconds after being invoked, and the answer is then streamed character-by-character with a small per-character delay
- **THEN** the reported elapsed time is 3.2 seconds, not the longer wall-clock time that includes the per-character streaming delay of that same answer

### Requirement: Every response path appends a response-time line

The system SHALL append a line reading `回應時間：X.X 秒` (X.X = elapsed seconds rounded to one decimal place) to the final streamed text for every response path in `chat_stream`: the input prompt-injection guardrail message, the off-topic message, the cache-candidate guardrail message (PII/injection detected in a cached candidate), the cache-hit answer, the post-generation guardrail message (PII/injection detected in a freshly generated answer), and the successful RAG-generated answer. When a "參考資料：" source block is also appended to that same response (per the `answer-sources` capability), the response-time line SHALL appear after the source block, separated by a blank line. When no source block is appended, the response-time line SHALL appear directly after the answer text, separated by a blank line.

#### Scenario: RAG answer with sources gets timing after the source block

- **WHEN** a RAG-generated answer "密碼可以在登入頁面重設。" has sources `["密碼忘記了要怎麼重設啊？"]` appended and the elapsed time is 3.2 seconds
- **THEN** the final streamed text equals "密碼可以在登入頁面重設。\n\n參考資料：\n- 密碼忘記了要怎麼重設啊？\n\n回應時間：3.2 秒"

#### Scenario: RAG answer without sources gets timing directly after the answer

- **WHEN** a RAG-generated answer "密碼可以在登入頁面重設。" has an empty sources list and the elapsed time is 1.5 seconds
- **THEN** the final streamed text equals "密碼可以在登入頁面重設。\n\n回應時間：1.5 秒"

#### Scenario: Guardrail and off-topic messages also get a timing line

- **WHEN** `chat_stream` blocks a request via the input prompt-injection guardrail and the elapsed time is 0.1 seconds
- **THEN** the final streamed text equals the guardrail message followed by "\n\n回應時間：0.1 秒"

##### Example: timing line across all response paths

| Response path | Base text | Elapsed | Final streamed text suffix |
| --- | --- | --- | --- |
| Input injection guardrail | guardrail message | 0.1s | "...\n\n回應時間：0.1 秒" |
| Off-topic | off-topic message | 0.4s | "...\n\n回應時間：0.4 秒" |
| Cache-candidate guardrail | guardrail message | 0.3s | "...\n\n回應時間：0.3 秒" |
| Cache hit | cached answer (+ sources if any) | 0.2s | "...\n\n回應時間：0.2 秒" |
| Post-generation guardrail | guardrail message | 3.0s | "...\n\n回應時間：3.0 秒" |
| RAG success | generated answer (+ sources if any) | 3.2s | "...\n\n回應時間：3.2 秒" |
