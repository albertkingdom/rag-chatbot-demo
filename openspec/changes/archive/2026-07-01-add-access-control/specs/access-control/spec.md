## ADDED Requirements

### Requirement: Request authentication via API key or session token

Every request to a non-exempt application route SHALL present a valid credential. The system SHALL accept one of two credential forms: (a) an `X-API-Key` HTTP header containing the raw API key, compared against the configured key using a constant-time comparison; or (b) an `session_id` HTTP-only cookie containing a session token issued by `POST /login`, validated against the session store in Redis. A request without a valid credential SHALL be rejected with HTTP 401 and a JSON body `{"detail":"Missing or invalid credential"}`. When the request `Accept` header indicates HTML (`text/html`), the system SHALL instead respond with HTTP 302 redirecting to `/login`. The raw API key SHALL never be stored in a cookie.

#### Scenario: valid key in header grants access

- **WHEN** a client sends a request to any application route with header `X-API-Key: <valid key>`
- **THEN** the request proceeds to the route handler

#### Scenario: valid session cookie grants access

- **WHEN** a browser sends a request with an `session_id` cookie whose session exists in Redis
- **THEN** the request proceeds to the route handler without re-checking the raw API key

#### Scenario: missing credential is rejected

- **WHEN** a client sends a request to a non-exempt route without an `X-API-Key` header or `session_id` cookie
- **THEN** the system responds with HTTP 401 and JSON `{"detail":"Missing or invalid credential"}`

#### Scenario: invalid header key is rejected

- **WHEN** a client sends a request with `X-API-Key: <wrong value>`
- **THEN** the system responds with HTTP 401 and JSON `{"detail":"Missing or invalid credential"}`

#### Scenario: expired or unknown session cookie is rejected

- **WHEN** a browser sends a request with an `session_id` cookie whose session does not exist in Redis (expired or revoked)
- **THEN** the system responds HTTP 401 (programmatic) or 302 to `/login` (browser)

#### Scenario: constant-time raw key comparison

- **WHEN** the system compares a presented `X-API-Key` header value against the configured key
- **THEN** the comparison SHALL use a constant-time function so that response timing does not leak key prefix information

### Requirement: Health check endpoint exempt from authentication and rate limiting

The system SHALL expose `GET /health` returning HTTP 200 with JSON `{"status":"ok"}`. The `/health` route SHALL NOT require a credential and SHALL NOT be subject to rate limiting. The `/health` handler SHALL NOT initialize heavy singletons (reranker, embeddings, vector store) so that Cloud Run liveness probes succeed quickly on cold starts.

#### Scenario: health check without credential

- **WHEN** a client sends `GET /health` with no `X-API-Key` header and no `session_id` cookie
- **THEN** the system responds with HTTP 200 and JSON `{"status":"ok"}`

#### Scenario: health check not rate limited

- **WHEN** a client sends more than the configured rate limit of `GET /health` requests within one minute
- **THEN** every `/health` request still responds HTTP 200

### Requirement: Per-API-key rate limiting via Redis

The system SHALL enforce a per-API-key request rate limit using Redis. The rate-limit bucket key SHALL be the first 16 hex characters of the SHA-256 hash of the underlying API key, so that all sessions and header requests originating from the same key share one quota. The limit SHALL be expressed as a configurable number of requests per minute (`RATE_LIMIT_RPM`, default 60). Requests exceeding the limit SHALL be rejected with HTTP 429 and a `Retry-After` header indicating the seconds until the window resets. Rate limiting SHALL NOT apply to exempt routes (`/health`, `GET /login`, `POST /login`, `POST /logout`). If Redis is unavailable, the system SHALL fail open (allow the request) and log an error, so that the credential gate remains the primary access control.

#### Scenario: requests within limit succeed

- **WHEN** a client with a valid credential sends up to `RATE_LIMIT_RPM` requests in one minute
- **THEN** every request proceeds to the route handler

#### Scenario: requests over limit are throttled

- **WHEN** a client with a valid credential sends more than `RATE_LIMIT_RPM` requests in one minute
- **THEN** requests beyond the limit respond HTTP 429 with a `Retry-After` header

#### Scenario: rate limit is per underlying key, not per session

- **WHEN** a user logs in twice (two sessions issued from the same API key) and one session exhausts the quota
- **THEN** the other session from the same key is also throttled, while a session from a different key is not affected

#### Scenario: redis unavailable fails open

- **WHEN** Redis is unreachable and a valid-credential request arrives
- **THEN** the request proceeds to the route handler and an error is logged

### Requirement: Browser login endpoint issuing session tokens

The system SHALL expose `GET /login` returning an HTML form that accepts an API key, exempt from authentication. The system SHALL expose `POST /login` that validates the submitted key using a constant-time comparison; on success it SHALL create a session token, store it in Redis under `session:<id>` with the configured TTL and the SHA-256 hash of the originating key (for rate limiting), set an HTTP-only `session_id` cookie (scoped to the application path, with `SameSite=Lax`) containing the session id, and respond with HTTP 302 redirecting to `/`. The raw API key SHALL NOT be placed in the cookie. On an invalid key, `POST /login` SHALL respond HTTP 401 with the login form and an error message, and SHALL set no `session_id` cookie. The login form SHALL submit via `POST` so that the key is not placed in a URL or log.

#### Scenario: login form is reachable without a credential

- **WHEN** an unauthenticated browser requests `GET /login`
- **THEN** the system responds HTTP 200 with an HTML form containing a key input and a submit button

#### Scenario: successful login issues session cookie and redirects

- **WHEN** a browser submits `POST /login` with a valid key
- **THEN** the system creates a session record in Redis, sets an HTTP-only `session_id` cookie (not the raw key), and responds HTTP 302 to `/`

#### Scenario: failed login does not set cookie

- **WHEN** a browser submits `POST /login` with an invalid key
- **THEN** the system responds HTTP 401 with the login form and an error message, and sets no `session_id` cookie and creates no session record

### Requirement: Session token lifecycle and revocation

A session token SHALL be a cryptographically random value of at least 128 bits. The system SHALL store each session in Redis under key `session:<id>` with a configurable TTL (`SESSION_TTL_SECONDS`, default 86400) and a value containing the SHA-256 hash of the originating API key. Each non-exempt request presenting an `session_id` cookie SHALL validate that `session:<id>` exists in Redis; an expired or missing record SHALL be treated as an invalid credential. The system SHALL expose `POST /logout` (exempt from authentication) that deletes `session:<id>` from Redis and clears the `session_id` cookie, so that a session can be revoked without rotating the API key. Sessions SHALL expire automatically via the Redis TTL without explicit deletion.

#### Scenario: session is random and at least 128 bits

- **WHEN** the system creates a new session id
- **THEN** the id is generated by a cryptographically secure random generator and is at least 32 hex characters long

#### Scenario: session stored in Redis with TTL

- **WHEN** `POST /login` succeeds
- **THEN** a `session:<id>` key exists in Redis with TTL equal to `SESSION_TTL_SECONDS` and a value containing the originating key hash

#### Scenario: expired session is rejected

- **WHEN** a session's Redis TTL has elapsed and the browser presents the `session_id` cookie
- **THEN** the request is treated as unauthenticated (401 or 302 to `/login`)

#### Scenario: logout revokes the session

- **WHEN** a browser submits `POST /logout` with an `session_id` cookie
- **THEN** the system deletes `session:<id>` from Redis, clears the cookie, and subsequent requests with that cookie are rejected

#### Scenario: revoking one session does not revoke others

- **WHEN** a user logs out one browser while another browser holds a different session from the same key
- **THEN** the other browser's session remains valid

### Requirement: Cloud Run Service IAM restriction and secret injection

The Terraform configuration SHALL NOT grant `roles/run.invoker` to `allUsers` on the Cloud Run Service. Invoker grants SHALL be limited to a configurable list of members (`allowed_invoker_members`, default empty). The `APP_API_KEY` secret SHALL be added to the set of Secret Manager secrets and injected into the Cloud Run Service container environment alongside the existing secrets. The Service SHALL read the API key from `APP_API_KEY` at startup.

#### Scenario: no public invoker by default

- **WHEN** Terraform is applied with `allowed_invoker_members` unset
- **THEN** no `allUsers` invoker binding exists on the Cloud Run Service and only explicitly listed members can reach it

#### Scenario: api key injected from Secret Manager

- **WHEN** the Cloud Run Service starts
- **THEN** the `APP_API_KEY` environment variable is populated from Secret Manager and the application reads it

#### Scenario: operator can allow specific members

- **WHEN** the operator sets `allowed_invoker_members = ["user:demo@example.com"]`
- **THEN** Terraform grants `roles/run.invoker` to that member only

### Requirement: Auth-disabled development mode

When `AUTH_ENABLED` is set to `false` (case-insensitive) OR `APP_API_KEY` is unset, the system SHALL bypass authentication and rate limiting on all routes except that `/health` remains available. The system SHALL log a prominent startup warning stating that authentication is disabled and is not safe for production. When `AUTH_ENABLED` is `true` (default) and `APP_API_KEY` is set, authentication, session issuance, and rate limiting SHALL be active.

#### Scenario: auth disabled allows all routes

- **WHEN** `AUTH_ENABLED=false` and a client requests any route without a credential
- **THEN** the request proceeds to the route handler

#### Scenario: auth disabled logs a startup warning

- **WHEN** the application starts with `AUTH_ENABLED=false` or `APP_API_KEY` unset
- **THEN** the startup log contains a warning that authentication is disabled and not for production use

#### Scenario: auth enabled enforces the gate

- **WHEN** `AUTH_ENABLED=true` and `APP_API_KEY` is set and a client requests a non-exempt route without a credential
- **THEN** the system responds HTTP 401
