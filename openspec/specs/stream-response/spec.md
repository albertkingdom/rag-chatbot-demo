# stream-response Specification

## Purpose

TBD - created by archiving change 'optimize-stream-response'. Update Purpose after archive.

## Requirements

### Requirement: Text responses are streamed in fixed-size chunks

The chat application service SHALL emit answer text as append-only `delta` events whose `text` values concatenate to the completed answer. The HTTP chat endpoint SHALL serialize each typed event as one newline-delimited JSON object with media type `application/x-ndjson`. Status, sources, metadata, completion, and error information SHALL use separate event types and SHALL NOT be encoded by overloading answer text. Every successful stream SHALL end with exactly one `done` event; a post-start failure SHALL end with one safe `error` event and no `done` event.

#### Scenario: delta values reconstruct the answer

- **WHEN** a completed answer is emitted across multiple `delta` events
- **THEN** concatenating each `delta.text` in order exactly reproduces the answer without duplicated prefixes or missing text

#### Scenario: response metadata is not appended to answer text

- **WHEN** a RAG response has sources and elapsed-time metadata
- **THEN** sources and metadata are emitted through their dedicated event types and the concatenated answer deltas contain only the answer body

#### Scenario: successful stream has one terminal event

- **WHEN** any RAG, direct, cached, or guardrail response completes successfully
- **THEN** the event sequence contains exactly one final `done` event after all answer and optional source/metadata events

#### Scenario: post-start failure has a safe terminal error

- **WHEN** an exception occurs after HTTP streaming has begun
- **THEN** the server emits one `error` event with a stable public code and safe message, emits no stack trace or secret, and does not emit `done`
