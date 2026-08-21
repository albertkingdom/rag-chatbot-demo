## ADDED Requirements

### Requirement: Text responses are streamed in fixed-size chunks

The `chat_stream` function SHALL yield text responses in chunks of `_STREAM_CHUNK_SIZE` characters (default 10) with a delay of `_STREAM_CHUNK_DELAY` seconds (default 0.005) between each yield. Each yield SHALL contain the full accumulated text from the beginning of the response up to the current chunk boundary. The final yield SHALL contain the complete response text regardless of whether the total length is a multiple of the chunk size.

#### Scenario: Response length is a multiple of chunk size

- **WHEN** the response text is exactly 30 characters and chunk size is 10
- **THEN** the system yields 3 times: characters 1-10, characters 1-20, characters 1-30

##### Example: 30-character response with chunk size 10

- **GIVEN** response text "AAAAABBBBBCCCCCDDDDDEEEEEF" (30 chars) and chunk size 10
- **WHEN** the system streams the response
- **THEN** yield 1 contains the first 10 characters, yield 2 contains the first 20 characters, yield 3 contains all 30 characters, and the total number of yields is 3

#### Scenario: Response length is not a multiple of chunk size

- **WHEN** the response text is 25 characters and chunk size is 10
- **THEN** the system yields 3 times: characters 1-10, characters 1-20, characters 1-25, where the final yield contains the complete text

##### Example: 25-character response with chunk size 10

- **GIVEN** response text of length 25 and chunk size 10
- **WHEN** the system streams the response
- **THEN** yield 1 contains 10 characters, yield 2 contains 20 characters, yield 3 contains all 25 characters, and the total number of yields is 3

#### Scenario: All yield sites use the shared streaming function

- **WHEN** the `chat_stream` function yields a text response in any code path (RAG response, direct response, cached response, guardrail response)
- **THEN** it SHALL use the shared `_stream_text` async generator and SHALL NOT use inline character-by-character yield loops
