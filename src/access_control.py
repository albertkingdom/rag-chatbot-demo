"""Access control for the Carbon Assistant app.

Provides API-key authentication, Redis-backed session tokens for browsers,
and per-key rate limiting. See openspec/changes/add-access-control for the
full design.

Public surface used by src/app.py:
    - build_auth_config() -> AuthConfig
    - mount_auth(app) -> None
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
from fastapi.responses import JSONResponse, RedirectResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

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
    ("GET", "/login"),
    ("POST", "/login"),
    ("POST", "/logout"),
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

    Reads AUTH_ENABLED, APP_API_KEY, RATE_LIMIT_RPM, SESSION_TTL_SECONDS and
    REDIS_URL. When AUTH_ENABLED is true but APP_API_KEY is unset, logs an
    error and returns enabled=False (degraded) instead of producing a config
    that would 401 every request.
    """
    auth_enabled = os.environ.get("AUTH_ENABLED", "true").lower() == "true"
    api_key = os.environ.get("APP_API_KEY")
    rate_limit_rpm = int(os.environ.get("RATE_LIMIT_RPM", "60"))
    session_ttl_seconds = int(os.environ.get("SESSION_TTL_SECONDS", "86400"))
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

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
        redis=redis.from_url(redis_url),
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

    On missing/invalid credential: 401 JSON (programmatic) or 302 to /login
    (browser, detected via ``Accept: text/html``). Exempt routes bypass both
    auth and rate limiting. When ``AuthConfig.enabled`` is False, everything
    is allowed (dev/test mode). Redis errors fail open (allow + log).
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
            if hmac.compare_digest(header_key, self.config.api_key):
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
        """401 JSON for programmatic clients, 302 to /login for browsers."""
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            return RedirectResponse("/login", status_code=302)
        return JSONResponse({"detail": "Missing or invalid credential"}, status_code=401)

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
        return JSONResponse(
            {"detail": "Rate limit exceeded"},
            status_code=429,
            headers={"Retry-After": str(retry_after)},
        )

    async def dispatch(self, request: Request, call_next):
        if not self.config.enabled:
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


_LOGIN_HTML = """<!doctype html>
<html lang="zh-Hant">
<head><meta charset="utf-8"><title>Carbon Assistant — 登入</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:360px;margin:80px auto;padding:0 16px}}
input{{width:100%;padding:10px;margin:8px 0;box-sizing:border-box;font-size:15px}}
button{{width:100%;padding:10px;background:#2563eb;color:#fff;border:none;border-radius:6px;font-size:15px;cursor:pointer}}
.err{{color:#b91c1c;margin-bottom:12px}}
</style></head>
<body>
<h2>Carbon Assistant 登入</h2>
{error}
<form method="post" action="/login">
  <label for="key">API Key</label>
  <input id="key" name="api_key" type="password" autocomplete="off" autofocus required>
  <button type="submit">登入</button>
</form>
</body></html>"""


def _login_form(error: str = "", status: int = 200) -> Response:
    err_block = f'<p class="err">{error}</p>' if error else ""
    return Response(
        _LOGIN_HTML.format(error=err_block),
        media_type="text/html",
        status_code=status,
    )


def _login_get(request: Request) -> Response:
    return _login_form()


async def _login_post(request: Request, config: AuthConfig) -> Response:
    """Validate the submitted API key; on success issue a session cookie.

    The raw key is compared with ``hmac.compare_digest`` and never written to
    the cookie — only the session id is. On failure, no session is created
    and no cookie is set; the form is re-rendered with HTTP 401.
    """
    form = await request.form()
    submitted = form.get("api_key", "")

    if submitted and config.api_key and hmac.compare_digest(submitted, config.api_key):
        kh = _key_hash(submitted)
        sid = create_session(config.redis, kh, config.session_ttl_seconds)
        resp = RedirectResponse("/", status_code=302)
        resp.set_cookie(
            SESSION_COOKIE, sid,
            httponly=True, samesite="lax", path="/",
        )
        return resp

    return _login_form(error="API Key 無效", status=401)


async def _logout_post(request: Request, config: AuthConfig) -> Response:
    """Revoke the session and clear the cookie, then redirect to /login."""
    sid = request.cookies.get(SESSION_COOKIE)
    if sid:
        revoke_session(config.redis, sid)
    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


def mount_auth(app: FastAPI) -> None:
    """Attach the auth/rate-limit middleware and register auth routes.

    MUST be called before gr.mount_gradio_app so the middleware wraps Gradio
    routes too.
    """
    from functools import partial

    config = build_auth_config()
    app.add_middleware(AuthRateLimitMiddleware, config=config)
    app.add_route("/health", _health, methods=["GET"])
    app.add_route("/login", _login_get, methods=["GET"])
    app.add_route("/login", partial(_login_post, config=config), methods=["POST"])
    app.add_route("/logout", partial(_logout_post, config=config), methods=["POST"])
    if not config.enabled:
        logger.warning("AUTH DISABLED — not for production")
