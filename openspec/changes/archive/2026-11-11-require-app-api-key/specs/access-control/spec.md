## MODIFIED Requirements

### Requirement: Auth-disabled development mode

When `AUTH_ENABLED` is set to `false` (case-insensitive), the application SHALL bypass authentication and rate limiting, keep `/health` available, and log a startup warning that authentication is disabled and unsafe for production. When authentication is enabled (the default) and `APP_API_KEY` is nonempty, authentication, session issuance, and rate limiting SHALL be active. When authentication is enabled and `APP_API_KEY` is unset or empty, application startup SHALL fail with a diagnostic naming `APP_API_KEY`.

#### Scenario: auth disabled allows all routes

- **WHEN** `AUTH_ENABLED=false`, `APP_API_KEY` is unset, and a client requests a route without a credential
- **THEN** the request proceeds to the route handler

#### Scenario: auth disabled logs a startup warning

- **WHEN** the application starts with `AUTH_ENABLED=false`
- **THEN** the startup log warns that authentication is disabled and unsafe for production

#### Scenario: auth enabled enforces the gate

- **WHEN** `AUTH_ENABLED=true` and `APP_API_KEY` is set and a client requests a non-exempt route without a credential
- **THEN** the system responds HTTP 401

#### Scenario: missing key prevents startup

- **WHEN** the application starts with `AUTH_ENABLED=true` or the default and `APP_API_KEY` is unset or empty
- **THEN** the application fails startup and reports that `APP_API_KEY` is required
