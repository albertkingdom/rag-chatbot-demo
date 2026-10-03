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


### Requirement: Per-API-key rate limiting via Redis

The system SHALL enforce a per-API-key request rate limit using Redis. The rate-limit bucket key SHALL be the first 16 hex characters of the SHA-256 hash of the underlying API key, so that all sessions and header requests originating from the same key share one quota. The limit SHALL be expressed as a configurable number of requests per minute (`RATE_LIMIT_RPM`, default 60). Requests exceeding the limit SHALL be rejected with HTTP 429 and a `Retry-After` header indicating the seconds until the window resets. Rate limiting SHALL apply to protected API routes and SHALL NOT apply to public SPA documents/assets, `/health`, `POST /api/v1/auth/login`, or `POST /api/v1/auth/logout`. If Redis is unavailable, the system SHALL fail open (allow the request) and log an error, so that the credential gate remains the primary access control.

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


### Requirement: Cloud Run Service IAM invoker and secret injection

The Terraform configuration SHALL grant `roles/run.invoker` to `allUsers` on the Cloud Run Service, hardcoded (not a configurable variable). Access control for this Service is enforced at the application layer (API key + session, see `AuthRateLimitMiddleware`), not via IAM — end users authenticate with a shared `APP_API_KEY` and have no GCP principal, so restricting `roles/run.invoker` at the IAM layer would block them from ever reaching the app-layer login page. The `APP_API_KEY` secret SHALL be added to the set of Secret Manager secrets and injected into the Cloud Run Service container environment alongside the existing secrets. The Service SHALL read the API key from `APP_API_KEY` at startup.

*Correction (2026-07-02): the original version of this requirement (SHALL NOT grant `allUsers`, configurable `allowed_invoker_members` defaulting to empty) was found to conflict with the app's own access model — an empty invoker list blocks the Cloud Run Service at the IAM layer before any request reaches the app-layer login page, making the Service unreachable for real users rather than more secure. Corrected to hardcode `allUsers` and rely on the app-layer credential as the actual gate.*

#### Scenario: invoker is public, app layer is the real gate

- **WHEN** Terraform is applied
- **THEN** an `allUsers` invoker binding exists on the Cloud Run Service, and unauthenticated requests reach the application, where protected API requests receive HTTP 401 JSON and browser document navigation is handled by the SPA auth guard

#### Scenario: api key injected from Secret Manager

- **WHEN** the Cloud Run Service starts
- **THEN** the `APP_API_KEY` environment variable is populated from Secret Manager and the application reads it
