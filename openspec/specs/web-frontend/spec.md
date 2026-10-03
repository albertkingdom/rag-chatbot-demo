# web-frontend Specification

## Purpose

Define the user-visible, API, security, streaming, upload, accessibility, and deployment contract for replacing the Gradio interface with a standalone same-origin web frontend.

## Requirements

### Requirement: The application provides a responsive standalone web frontend

The system SHALL provide a React and TypeScript single-page application for login, chat, conversation clearing, logout, and manual upload workflows. The application SHALL remain usable without horizontal overflow at a 360 CSS-pixel viewport and at supported desktop widths. Primary mobile actions SHALL have an effective touch target of at least 44 by 44 CSS pixels, editable fields SHALL use at least 16px text, and the composer action SHALL remain usable when the virtual keyboard is open. All primary actions SHALL be keyboard operable, SHALL have visible focus, and asynchronous status and errors SHALL be exposed through suitable live regions without relying on color alone.

#### Scenario: Mobile chat remains operable

- **GIVEN** an authenticated user opens the application at a 360 CSS-pixel viewport
- **WHEN** the user asks a question and receives a streamed answer
- **THEN** the message list, composer, stop action, answer, sources, and error/status regions remain visible and operable without horizontal page scrolling

#### Scenario: Keyboard-only login and chat

- **GIVEN** a user does not use a pointing device
- **WHEN** the user logs in, submits a question, stops a stream, clears history, and logs out
- **THEN** every action can be completed by keyboard with a visible focus indicator

---
### Requirement: Browser authentication uses an opaque server session

The system SHALL accept the shared application key through the login API, compare it on the server, store only an opaque session identifier in an HttpOnly cookie, and SHALL NOT store the raw application key in a URL, Web Storage, or client log. An expired or invalid session SHALL result in a single transition to the login page without an automatic retry loop.

#### Scenario: Successful browser login

- **WHEN** a user submits the correct application key to the login API
- **THEN** the server returns success with an HttpOnly session cookie and the browser enters the chat application without persisting the raw key client-side

#### Scenario: Session expires during use

- **WHEN** a protected API request returns 401 because the session has expired
- **THEN** the frontend aborts any active stream, clears in-memory private state, and routes to the login page without repeatedly retrying the request

---
### Requirement: Chat responses use typed NDJSON streaming events

The system SHALL expose chat through a versioned POST endpoint returning `application/x-ndjson`. Each non-empty line SHALL contain exactly one JSON event with a supported type. Answer text SHALL be emitted as append-only `delta` values. Display status, sources, response metadata, completion, and post-start errors SHALL use distinct event types. A successful stream SHALL end with exactly one `done` event.

#### Scenario: RAG answer streams with sources and metadata

- **WHEN** an authenticated user submits a question that follows the RAG path
- **THEN** the stream emits zero or more status and delta events, a sources event when sources exist, response metadata including elapsed time and response source, and exactly one final done event

#### Scenario: Direct answer has no empty sources panel

- **WHEN** an answer follows a direct or guardrail path with no sources
- **THEN** the stream does not emit an empty sources event and the frontend does not render an empty sources region

#### Scenario: Failure after streaming starts

- **WHEN** a provider or application error occurs after response headers have been sent
- **THEN** the stream emits a safe terminal error event, does not expose secrets or a stack trace, and does not emit a done event

---
### Requirement: Conversation history is owned by the authenticated session

The system SHALL derive the current conversation history key from the authenticated server session and SHALL NOT accept an arbitrary history key from the browser. The frontend SHALL restore completed user and assistant turns after refresh. Clearing the conversation SHALL clear both the visible conversation and its Redis-backed history, and clearing an already empty conversation SHALL be idempotent.

#### Scenario: Refresh restores completed conversation

- **GIVEN** an authenticated session has completed chat turns
- **WHEN** the page is refreshed
- **THEN** the frontend retrieves and displays those turns in their original role order

#### Scenario: Clear prevents hidden context reuse

- **GIVEN** an authenticated session has conversation history
- **WHEN** the user confirms clearing and the delete API succeeds
- **THEN** the visible list and server history are empty and the next question does not reuse the cleared turns

#### Scenario: Aborted partial answer is not persisted

- **WHEN** a client disconnects or aborts while the assistant answer is still incomplete
- **THEN** no partial assistant turn is appended to Redis history, response cache, or the conversation database; if the server has already completed a full valid answer, that answer SHALL be persisted according to the existing route behavior

#### Scenario: Header authentication does not validate an arbitrary history cookie

- **GIVEN** a request has a valid X-API-Key header and an unknown session_id cookie
- **WHEN** the current conversation API selects its history key
- **THEN** the unknown cookie SHALL NOT be used to read or mutate Redis history

##### Example: Unknown cookie is ignored for history selection

- **GIVEN** session:unvalidated-client-value is absent from Redis
- **WHEN** GET /api/v1/conversations/current/messages is authenticated by X-API-Key and carries session_id=unvalidated-client-value
- **THEN** the API returns an empty items list and never reads chat_history:unvalidated-client-value

---
### Requirement: Manual upload is validated and reports background job status

The system SHALL accept only configured supported manual file types and sizes, SHALL normalize the client filename to prevent path traversal, and SHALL avoid silently overwriting an existing file. A valid upload SHALL return a job identifier and the frontend SHALL show queued, running, succeeded, or failed status until a terminal result or polling timeout.

This side-project requirement SHALL NOT imply antivirus scanning, deep archive-bomb detection, per-user upload ownership, or a separate administrator role. The system SHALL authorize any authenticated holder of the shared application key to use the upload function.

#### Scenario: Valid manual starts a sync job

- **WHEN** an authenticated user uploads a valid supported manual
- **THEN** the server stores it within the configured data directory, enqueues the configured local or GCP runner, returns HTTP 202 with a job identifier, and the frontend polls until a terminal state

#### Scenario: Unsafe or invalid file is rejected

- **WHEN** an upload is unsupported, oversized, or attempts path traversal
- **THEN** the server rejects it with the documented 4xx status, does not enqueue a job, and leaves no usable file outside or inside the data directory

---
### Requirement: Production serves the frontend and API from one origin

The production FastAPI application SHALL serve the compiled SPA and the versioned API from one origin. API, authentication, and health routes SHALL be resolved before the SPA fallback. Unknown API routes SHALL return an API 404 rather than the SPA document. The runtime container SHALL NOT require a Node.js process.

#### Scenario: SPA deep link resolves

- **WHEN** a browser requests a known client-side route directly
- **THEN** FastAPI returns the SPA entry document and the client router renders the intended page

#### Scenario: Unknown API path is not masked

- **WHEN** a client requests an unknown path beneath `/api/`
- **THEN** the server returns a structured API 404 and does not return `index.html`
