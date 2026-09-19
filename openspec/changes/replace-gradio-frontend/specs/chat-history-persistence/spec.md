## MODIFIED Requirements

### Requirement: Clearing the visible conversation also clears the session's stored history

The chat-history service used by the versioned API SHALL report whether clearing Redis history succeeded. `DELETE /api/v1/conversations/current/messages` SHALL return HTTP 204 only after Redis confirms deletion or absence of the current session history. If Redis clearing fails, the API SHALL return a safe HTTP 503 response and the frontend SHALL retain the visible conversation and offer retry. The failure SHALL be logged without exposing Redis internals to the user.

#### Scenario: Clear history succeeds

- **WHEN** an authenticated user clears the current conversation and Redis confirms deletion or that the key is absent
- **THEN** the API returns HTTP 204 and the frontend removes the visible conversation

#### Scenario: Redis clear fails

- **WHEN** Redis raises an error while clearing the current conversation
- **THEN** the API returns HTTP 503, the frontend retains the visible conversation, and the next question is not falsely presented as having no prior context
