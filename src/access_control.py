"""Access control for the RAG_DEMO app.

Provides API-key authentication, Redis-backed session tokens for browsers,
and per-key rate limiting. See openspec/changes/add-access-control for the
full design.

Public surface used by src/app.py:
    - build_auth_config() -> AuthConfig
    - mount_auth(app) -> AuthConfig
"""

import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass
from typing import Optional

import redis
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from .api_errors import api_error

logger = logging.getLogger("access_control")

# Redis key prefixes.
_SESSION_PREFIX = "session:"
_RATE_PREFIX = "rate:"

# Cookie name carrying the session id (NOT the raw API key).
SESSION_COOKIE = "session_id"

# Routes that never require a credential and are never rate-limited.
# (method, path) tuples; method None means any method.
_EXEMPT_ROUTES: tuple[tuple[Optional[str], str], ...] = (
    ("GET", "/health"),
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/logout"),
)

# ---------------------------------------------------------------------------
# Session store helpers
# ---------------------------------------------------------------------------


def create_session(redis_conn: "redis.Redis", key_hash_value: str, ttl: int) -> str:
    """Create a session token, store it in Redis, and return the session id.

    The id is ``secrets.token_urlsafe(32)`` (>=128 bits). The Redis value is a
    JSON object ``{"key_hash": ..., "created_at": ...}`` so the rate-limit
    bucket can be recovered from the session without re-holding the raw key.
    """
    sid = secrets.token_urlsafe(32)
    payload = json.dumps(
        {"key_hash": key_hash_value, "created_at": int(time.time())}
    )
    redis_conn.setex(f"{_SESSION_PREFIX}{sid}", ttl, payload)
    return sid


def validate_session(redis_conn: "redis.Redis", sid: str) -> Optional[str]:
    """Return the key_hash for a session id, or None if expired/unknown."""
    raw = redis_conn.get(f"{_SESSION_PREFIX}{sid}")
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data.get("key_hash")


def revoke_session(redis_conn: "redis.Redis", sid: str) -> None:
    """Delete a session record so the cookie immediately becomes invalid."""
    redis_conn.delete(f"{_SESSION_PREFIX}{sid}")


@dataclass
class AuthConfig:
    """Resolved access-control configuration.

    ``enabled`` is the single gate the middleware checks. It is False when
    AUTH_ENABLED is false OR when APP_API_KEY is unset (degraded mode), so a
    misconfigured prod app degrades to open-with-warning rather than
    silently rejecting every request.
    """

    enabled: bool
    api_key: Optional[str]
    rate_limit_rpm: int
    session_ttl_seconds: int
    redis: "redis.Redis"


def build_auth_config() -> AuthConfig:
    """Build an AuthConfig from environment variables.

    Reads AUTH_ENABLED, APP_API_KEY, RATE_LIMIT_RPM, SESSION_TTL_SECONDS.
    Redis connection is obtained from the shared services.get_redis_conn()
    provider so the whole app uses one connection source. When AUTH_ENABLED
    is true but APP_API_KEY is unset, logs an error and returns enabled=False
    (degraded) instead of producing a config that would 401 every request.
    """
    # Lazy import to avoid a circular dependency at module load (services
    # imports config, not access_control; this is safe but kept lazy for
    # clarity).
    from .services import get_redis_conn

    auth_enabled = os.environ.get("AUTH_ENABLED", "true").lower() == "true"
    api_key = os.environ.get("APP_API_KEY")
    rate_limit_rpm = int(os.environ.get("RATE_LIMIT_RPM", "60"))
    session_ttl_seconds = int(os.environ.get("SESSION_TTL_SECONDS", "86400"))

    if auth_enabled and not api_key:
        logger.error(
            "AUTH_ENABLED=true but APP_API_KEY is unset; degrading to "
            "auth-disabled. Set APP_API_KEY (Secret Manager / .env) to enforce "
            "access control."
        )
        auth_enabled = False

    return AuthConfig(
        enabled=auth_enabled,
        api_key=api_key,
        rate_limit_rpm=rate_limit_rpm,
        session_ttl_seconds=session_ttl_seconds,
        redis=get_redis_conn(),
    )


def _key_hash(api_key: str) -> str:
    """First 16 hex chars of sha256(api_key) — the rate-limit bucket id."""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class AuthRateLimitMiddleware(BaseHTTPMiddleware):
    """Authenticate requests via API key header or session cookie, then
    enforce a per-key Redis rate limit.

    Credential resolution order:
      1. ``X-API-Key`` header  -> constant-time compare against APP_API_KEY,
         rate-limit bucket = sha256(presented key)[:16].
      2. ``session_id`` cookie -> Redis ``session:<id>`` lookup; on hit,
         rate-limit bucket = the ``key_hash`` stored at creation.

    API requests without valid credentials receive 401 JSON. Public SPA
    documents and assets bypass authentication and rate limiting; the browser
    auth guard controls navigation. When AuthConfig.enabled is False,
    everything is allowed (dev/test mode). Redis errors fail open (allow + log).
    """

    def __init__(self, app, config: AuthConfig):
        super().__init__(app)
        self.config = config

    # Exempt-route check -----------------------------------------------
    @staticmethod
    def _is_exempt(method: str, path: str) -> bool:
        m = method.upper()
        for em, ep in _EXEMPT_ROUTES:
            if (em is None or em == m) and path == ep:
                return True
        return False

    # Credential extraction -------------------------------------------
    def _authenticate(self, request: Request) -> Optional[str]:
        """Return the rate-limit key_hash if authenticated, else None.

        Side effects: none beyond Redis reads (session lookup). Raw key is
        never logged.
        """
        # Header path.
        header_key = request.headers.get("x-api-key")
        if header_key and self.config.api_key:
            if hmac.compare_digest(
                header_key.encode("utf-8"), self.config.api_key.encode("utf-8")
            ):
                return _key_hash(header_key)
            return None

        # Session-cookie path.
        sid = request.cookies.get(SESSION_COOKIE)
        if sid:
            try:
                kh = validate_session(self.config.redis, sid)
            except Exception as exc:
                logger.error("session lookup failed (fail-open): %s", exc)
                return None
            if kh:
                return kh
            return None

        return None

    # Response helpers -------------------------------------------------
    @staticmethod
    def _reject(request: Request):
        """Return the API error contract, including for HTML Accept headers."""
        return api_error(401, "Missing or invalid credential")

    # Rate limiting ---------------------------------------------------
    def _check_rate_limit(self, key_hash_value: str) -> tuple[bool, int]:
        """Fixed-window counter in Redis.

        Returns ``(allowed, retry_after_seconds)``. On Redis error, fails open
        (returns ``(True, 0)``) and logs — the credential gate remains the
        primary access control.
        """
        window = 60
        window_start = int(time.time()) // window * window
        bucket = f"{_RATE_PREFIX}{key_hash_value}:{window_start}"
        try:
            pipe = self.config.redis.pipeline()
            pipe.incr(bucket)
            pipe.expire(bucket, window)
            count, _ = pipe.execute()
        except Exception as exc:
            logger.error("rate-limit check failed (fail-open): %s", exc)
            return True, 0
        if int(count) > self.config.rate_limit_rpm:
            retry_after = window - (int(time.time()) - window_start)
            return False, max(retry_after, 1)
        return True, 0

    @staticmethod
    def _throttle(retry_after: int):
        return api_error(429, "Rate limit exceeded", {"Retry-After": str(retry_after)})

    async def dispatch(self, request: Request, call_next):
        if not self.config.enabled:
            return await call_next(request)

        # The application shell and hashed assets must load before
        # the browser has a session. Authentication remains enforced on the
        # versioned API surface.
        if request.url.path != "/api" and not request.url.path.startswith("/api/"):
            return await call_next(request)

        if self._is_exempt(request.method, request.url.path):
            return await call_next(request)

        try:
            kh = self._authenticate(request)
        except Exception as exc:
            # Redis down during header path is impossible (header path has no
            # Redis call); this guards the session path. Fail open.
            logger.error("authentication error (fail-open): %s", exc)
            return await call_next(request)

        if kh is None:
            return self._reject(request)

        allowed, retry_after = self._check_rate_limit(kh)
        if not allowed:
            return self._throttle(retry_after)

        return await call_next(request)


# ---------------------------------------------------------------------------
# Route handlers (registered by mount_auth)
# ---------------------------------------------------------------------------


def _health(request: Request) -> JSONResponse:
    """Liveness probe. Deliberately does NOT touch heavy singletons."""
    return JSONResponse({"status": "ok"})


def mount_auth(app: FastAPI) -> AuthConfig:
    """Attach API auth/rate-limit middleware and the public health route."""
    config = build_auth_config()
    app.state.auth_config = config
    app.add_middleware(AuthRateLimitMiddleware, config=config)
    app.add_route("/health", _health, methods=["GET"])
    if not config.enabled:
        logger.warning("AUTH DISABLED — not for production")
    return config
