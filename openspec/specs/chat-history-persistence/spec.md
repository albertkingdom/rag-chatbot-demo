# chat-history-persistence Specification

## Purpose

TBD - created by archiving change 'persist-chat-history-redis'. Update Purpose after archive.

## Requirements

### Requirement: History key prefers the persistent login session over the ephemeral Gradio session hash

The system SHALL derive the Redis history key (`history_key`) from the login session cookie (`SESSION_COOKIE`, as issued and validated by `src/access_control.py`) when that cookie is present on the request. The system SHALL fall back to the Gradio `request.session_hash` only when the login session cookie is absent (e.g. `AUTH_ENABLED=false`).

#### Scenario: Login session cookie present

- **WHEN** a chat request arrives and `request.cookies` contains the `SESSION_COOKIE` value
- **THEN** the system SHALL use that cookie value as `history_key`, regardless of the current `request.session_hash`

#### Scenario: Login session cookie absent (auth disabled)

- **WHEN** a chat request arrives and `request.cookies` does not contain the `SESSION_COOKIE` value
- **THEN** the system SHALL use `request.session_hash` as `history_key`

##### Example: key derivation

| `request.cookies[SESSION_COOKIE]` | `request.session_hash` | `history_key` used |
| ---------------------------------- | ------------------------ | -------------------- |
| `"abc123"` | `"xyz789"` | `"abc123"` |
| absent | `"xyz789"` | `"xyz789"` |


<!-- @trace
source: persist-chat-history-redis
updated: 2026-07-04
code:
  - docs/interview-guide.md
  - src/config.py
  - src/ui.py
  - src/rag_pipeline.py
  - src/chat_history_service.py
tests:
  - tests/test_rag_pipeline_history.py
  - tests/test_chat_history_service.py
  - tests/test_ui_clear_history.py
-->

---
### Requirement: Session-scoped short-term chat history storage

The system SHALL store recent conversation turns (user and assistant messages) in Redis, keyed by `history_key`, with an idle time-to-live (TTL) after which the stored history SHALL expire automatically.

#### Scenario: History persists across a client-side history loss within TTL

- **WHEN** a chat request arrives with a `history_key` that has an active (non-expired) Redis-stored history, regardless of what the client-supplied `history` parameter contains
- **THEN** the system SHALL use the Redis-stored history (not the client-supplied `history` parameter) as the effective history for query rewriting and prompt history formatting

#### Scenario: New session has no stored history

- **WHEN** a chat request arrives with a `history_key` that has no corresponding Redis key (new session, or session never persisted before)
- **THEN** the system SHALL fall back to using the client-supplied `history` parameter as the effective history

#### Scenario: History expires after idle TTL

- **WHEN** a chat request arrives with a `history_key` whose Redis-stored history key has already expired (no activity for longer than the configured idle TTL)
- **THEN** the system SHALL treat the session as having no stored history and fall back to the client-supplied `history` parameter

##### Example: TTL boundary

| Time since last turn | Redis key state | Effective history source |
| --------------------- | ---------------- | ------------------------- |
| 10 minutes (TTL = 30 min) | present | Redis-stored history |
| 31 minutes (TTL = 30 min) | expired/absent | client-supplied `history` parameter |


<!-- @trace
source: persist-chat-history-redis
updated: 2026-07-04
code:
  - docs/interview-guide.md
  - src/config.py
  - src/ui.py
  - src/rag_pipeline.py
  - src/chat_history_service.py
tests:
  - tests/test_rag_pipeline_history.py
  - tests/test_chat_history_service.py
  - tests/test_ui_clear_history.py
-->

---
### Requirement: Chat turns are written back to session history on successful answers

After producing a response that is intended as genuine conversational content (a normal RAG-generated answer or a cache-hit answer), the system SHALL append the user message and the assistant response as one turn to the session's Redis-stored history and refresh the TTL.

#### Scenario: Successful RAG-generated answer is persisted

- **WHEN** `chat_stream` completes a normal retrieval-augmented generation answer for a given `history_key`
- **THEN** the system SHALL append the (user message, assistant answer) turn to that session's Redis-stored history and reset the idle TTL to the configured duration

#### Scenario: Cache-hit answer is persisted

- **WHEN** `chat_stream` returns a cached answer (prompt cache hit) for a given `history_key`
- **THEN** the system SHALL append the (user message, cached answer) turn to that session's Redis-stored history and reset the idle TTL to the configured duration

#### Scenario: Guardrail and off-topic responses are not persisted

- **WHEN** `chat_stream` returns a guardrail-blocked message (prompt injection or PII detected) or an off-topic message for a given `history_key`
- **THEN** the system SHALL NOT append that turn to the session's Redis-stored history


<!-- @trace
source: persist-chat-history-redis
updated: 2026-07-04
code:
  - docs/interview-guide.md
  - src/config.py
  - src/ui.py
  - src/rag_pipeline.py
  - src/chat_history_service.py
tests:
  - tests/test_rag_pipeline_history.py
  - tests/test_chat_history_service.py
  - tests/test_ui_clear_history.py
-->

---
### Requirement: Stored history is bounded to a maximum number of recent turns

The system SHALL retain at most a configured maximum number of most-recent conversation turns per session, discarding the oldest turns first when the limit is exceeded.

#### Scenario: History exceeding the configured turn limit is trimmed

- **WHEN** a new turn is appended to a session (identified by `history_key`) whose stored history already contains the configured maximum number of turns
- **THEN** the system SHALL discard the oldest turn(s) so that the stored history retains only the configured maximum number of most-recent turns

##### Example: trimming to the configured limit

- **GIVEN** `CHAT_HISTORY_MAX_TURNS` = 3 and a session already has 3 stored turns: T1, T2, T3 (oldest to newest)
- **WHEN** a new turn T4 is appended
- **THEN** the stored history becomes T2, T3, T4 (T1 is discarded)


<!-- @trace
source: persist-chat-history-redis
updated: 2026-07-04
code:
  - docs/interview-guide.md
  - src/config.py
  - src/ui.py
  - src/rag_pipeline.py
  - src/chat_history_service.py
tests:
  - tests/test_rag_pipeline_history.py
  - tests/test_chat_history_service.py
  - tests/test_ui_clear_history.py
-->

---
### Requirement: Redis unavailability degrades gracefully without failing the chat request

If Redis is unavailable or an operation against it raises an exception, the system SHALL NOT let that failure propagate to the caller of `chat_stream`; it SHALL log the failure and continue using a safe fallback.

#### Scenario: Redis read failure falls back to client-supplied history

- **WHEN** reading the session's stored history from Redis raises an exception
- **THEN** the system SHALL treat the session as having no stored history, fall back to the client-supplied `history` parameter, and continue processing the chat request without raising an exception to the caller

#### Scenario: Redis write failure does not interrupt the response

- **WHEN** appending a turn to the session's stored history in Redis raises an exception
- **THEN** the system SHALL log the failure and continue; the chat response already produced SHALL still be returned to the caller unaffected


<!-- @trace
source: persist-chat-history-redis
updated: 2026-07-04
code:
  - docs/interview-guide.md
  - src/config.py
  - src/ui.py
  - src/rag_pipeline.py
  - src/chat_history_service.py
tests:
  - tests/test_rag_pipeline_history.py
  - tests/test_chat_history_service.py
  - tests/test_ui_clear_history.py
-->

---
### Requirement: Clearing the visible conversation also clears the session's stored history

When the user triggers the chat UI's built-in conversation-clear control, the system SHALL delete that session's Redis-stored history in addition to clearing the visible conversation, so that a subsequent message does not silently reuse the cleared conversation's context.

#### Scenario: Clearing the conversation deletes the stored history

- **WHEN** the user triggers the chat UI's conversation-clear control for a session whose `history_key` has a stored Redis history
- **THEN** the system SHALL delete that session's Redis-stored history so that a subsequent chat request for the same `history_key` behaves as a new session with no stored history

#### Scenario: Clearing an already-empty conversation is a no-op

- **WHEN** the user triggers the chat UI's conversation-clear control for a session whose `history_key` has no stored Redis history
- **THEN** the system SHALL NOT raise an error; the operation SHALL have no observable effect on Redis

#### Scenario: Clear failure does not interrupt the UI

- **WHEN** deleting the session's stored history from Redis raises an exception
- **THEN** the system SHALL log the failure and continue without propagating the exception to the UI

<!-- @trace
source: persist-chat-history-redis
updated: 2026-07-04
code:
  - docs/interview-guide.md
  - src/config.py
  - src/ui.py
  - src/rag_pipeline.py
  - src/chat_history_service.py
tests:
  - tests/test_rag_pipeline_history.py
  - tests/test_chat_history_service.py
  - tests/test_ui_clear_history.py
-->