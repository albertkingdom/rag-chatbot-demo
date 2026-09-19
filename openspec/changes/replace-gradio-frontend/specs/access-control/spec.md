## MODIFIED Requirements

### Requirement: Request authentication via API key or session token

Every request to a non-exempt API route SHALL present a valid credential. The system SHALL accept either an `X-API-Key` header containing the raw configured key, compared using a constant-time function, or an HttpOnly `session_id` cookie whose opaque token is valid in Redis. Protected API requests without a valid credential SHALL receive HTTP 401 with the documented JSON error shape. Browser document navigation SHALL be handled by the SPA auth guard rather than by content negotiation redirects from API middleware. The raw API key SHALL never be stored in a cookie, URL, Web Storage, or client log.

#### Scenario: valid header key grants API access

- **WHEN** a client calls a protected API route with the valid `X-API-Key` header
- **THEN** the request proceeds without requiring a browser session cookie

#### Scenario: valid session cookie grants API access

- **WHEN** a browser calls a protected API route with a session cookie whose Redis record is valid
- **THEN** the request proceeds without re-sending the raw API key

#### Scenario: protected API without credential is rejected

- **WHEN** a client calls a protected API route without a valid header or cookie
- **THEN** the server returns HTTP 401 JSON and does not return an HTML login form or redirect response

### Requirement: Browser login endpoint issuing session tokens

The system SHALL expose `POST /api/v1/auth/login` accepting a JSON application key. It SHALL validate the key using a constant-time comparison and, on success, create a cryptographically random Redis-backed session with the configured TTL, set an HttpOnly `session_id` cookie scoped to the application with `SameSite=Lax`, and return HTTP 204. The cookie SHALL be `Secure` in production HTTPS operation. Invalid credentials SHALL return HTTP 401 without creating a session or setting a cookie. `GET /api/v1/auth/session` SHALL allow the SPA to determine authenticated or auth-disabled state without exposing the raw key or session record.

#### Scenario: successful JSON login

- **WHEN** a browser sends a valid key to `POST /api/v1/auth/login`
- **THEN** the server returns HTTP 204, creates a TTL-bound Redis session, and sets an opaque HttpOnly cookie without returning or storing the raw key client-side

#### Scenario: failed JSON login

- **WHEN** a browser sends an invalid key to `POST /api/v1/auth/login`
- **THEN** the server returns HTTP 401, creates no session, and sets no session cookie

#### Scenario: session bootstrap is unauthenticated but non-disclosing

- **WHEN** an unauthenticated SPA calls `GET /api/v1/auth/session`
- **THEN** the endpoint returns HTTP 401 when authentication is enabled and does not reveal the configured key, key hash, other sessions, or internal Redis data

### Requirement: Session token lifecycle and revocation

A session token SHALL be cryptographically random with at least 128 bits of entropy and SHALL be stored in Redis at `session:<id>` with the configured TTL and originating key hash needed for rate limiting. The system SHALL expose `POST /api/v1/auth/logout` that idempotently revokes the presented session and clears its cookie. State-changing requests authenticated by cookie SHALL pass same-origin `Origin` or `Referer` validation; requests authenticated solely by `X-API-Key` SHALL not depend on browser-origin headers.

#### Scenario: logout is idempotent

- **WHEN** a browser calls logout with a valid, expired, or absent session cookie
- **THEN** the server returns HTTP 204, clears the cookie, and leaves no valid presented session

#### Scenario: cross-origin cookie mutation is rejected

- **WHEN** a state-changing API request relies on a valid session cookie but has an origin that does not match the configured application origin
- **THEN** the server rejects the request without performing the mutation

#### Scenario: API-key automation remains compatible

- **WHEN** a non-browser client sends a valid `X-API-Key` to a state-changing API endpoint without an `Origin` header
- **THEN** authentication can succeed without cookie CSRF checks
