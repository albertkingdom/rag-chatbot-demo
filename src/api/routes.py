"""FastAPI routes consumed by the React frontend."""

from contextlib import aclosing
from typing import Literal

import hmac
import os
from urllib.parse import urlsplit

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from typing_extensions import Annotated

from ..access_control import (
    SESSION_COOKIE,
    AuthConfig,
    _key_hash,
    create_session,
    revoke_session,
    validate_session,
)
from ..api_errors import ApiErrorResponse
from ..chat_events import ErrorEvent
from ..chat_history_service import ChatHistoryService
from ..manual_service import (
    ManualConflictError,
    ManualTooLargeError,
    ManualUploadError,
    enqueue_sync,
    get_sync_status,
    save_manual,
)

router = APIRouter(prefix="/api/v1", responses={code: {"model": ApiErrorResponse} for code in (401, 403, 404, 409, 413, 422, 429, 503)})

MessageText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
ApiKeyText = Annotated[str, StringConstraints(min_length=1, max_length=4096)]


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: MessageText


class LoginRequest(BaseModel):
    api_key: ApiKeyText = Field(alias="apiKey")
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class SessionResponse(BaseModel):
    authenticated: bool
    auth_enabled: bool = Field(alias="authEnabled")
    model_config = ConfigDict(populate_by_name=True)


class MessageItem(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class MessageList(BaseModel):
    items: list[MessageItem]


class SyncJobResponse(BaseModel):
    job_id: str = Field(alias="jobId", min_length=1)
    status: Literal["queued", "running", "succeeded", "failed"]
    message: str | None = None
    model_config = ConfigDict(populate_by_name=True)


def _auth_config(request: Request) -> AuthConfig:
    return request.app.state.auth_config


def _session_id(request: Request) -> str | None:
    config = _auth_config(request)
    if not config.enabled:
        return None
    sid = request.cookies.get(SESSION_COOKIE)
    # Header authentication does not validate a concurrently supplied cookie.
    # Never use such an arbitrary client value to choose stored history.
    if sid and request.headers.get("x-api-key"):
        try:
            if not validate_session(config.redis, sid):
                return None
        except Exception as exc:
            raise HTTPException(status_code=503, detail="暫時無法確認對話，請稍後再試") from exc
    return sid


def _request_origin(request: Request) -> str:
    configured = os.environ.get("APP_PUBLIC_ORIGIN")
    if configured:
        return configured.rstrip("/")
    forwarded_proto = request.headers.get("x-forwarded-proto")
    scheme = forwarded_proto.split(",", 1)[0].strip() if forwarded_proto else request.url.scheme
    return f"{scheme}://{request.headers.get('host', request.url.netloc)}"


def _require_same_origin_for_cookie_request(request: Request) -> None:
    """Basic CSRF protection for mutations authenticated by session cookie."""
    if request.headers.get("x-api-key") or not request.cookies.get(SESSION_COOKIE):
        return

    expected = _request_origin(request)
    presented = request.headers.get("origin")
    if not presented:
        referer = request.headers.get("referer")
        if referer:
            parsed = urlsplit(referer)
            presented = f"{parsed.scheme}://{parsed.netloc}"
    if not presented or presented.rstrip("/") != expected:
        raise HTTPException(status_code=403, detail="Cross-origin request rejected")


@router.get("/auth/session", response_model=SessionResponse)
async def get_session(request: Request) -> dict:
    config = _auth_config(request)
    return {"authenticated": config.enabled is False or bool(_session_id(request)), "authEnabled": config.enabled}


@router.post("/auth/login", status_code=status.HTTP_204_NO_CONTENT)
async def login(payload: LoginRequest, request: Request) -> Response:
    config = _auth_config(request)
    if not config.enabled:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    if not config.api_key or not hmac.compare_digest(
        payload.api_key.encode("utf-8"), config.api_key.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="APP登入密碼無效")

    try:
        sid = create_session(config.redis, _key_hash(payload.api_key), config.session_ttl_seconds)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="暫時無法登入，請稍後再試") from exc
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    secure = request.url.scheme == "https" or forwarded_proto.split(",", 1)[0].strip() == "https"
    response.set_cookie(
        SESSION_COOKIE,
        sid,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    return response


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request) -> Response:
    _require_same_origin_for_cookie_request(request)
    config = _auth_config(request)
    sid = request.cookies.get(SESSION_COOKIE)
    if sid:
        try:
            revoke_session(config.redis, sid)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="暫時無法登出，請稍後再試") from exc
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.get("/conversations/current/messages", response_model=MessageList)
async def get_messages(request: Request) -> MessageList:
    sid = _session_id(request)
    if not sid:
        return MessageList(items=[])
    history = ChatHistoryService(_auth_config(request).redis).get_history(sid)
    return MessageList(items=[MessageItem.model_validate(item) for item in history])


@router.delete("/conversations/current/messages", status_code=status.HTTP_204_NO_CONTENT)
async def clear_messages(request: Request) -> Response:
    _require_same_origin_for_cookie_request(request)
    sid = _session_id(request)
    if sid and sid in _active_sessions(request):
        raise HTTPException(status_code=409, detail="目前回答尚未完成，請停止或等待後再清除")
    if sid and not ChatHistoryService(_auth_config(request).redis).clear_history(sid):
        raise HTTPException(status_code=503, detail="暫時無法清除對話，請稍後再試")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _active_sessions(request: Request) -> set[str]:
    if not hasattr(request.app.state, "active_chat_sessions"):
        request.app.state.active_chat_sessions = set()
    return request.app.state.active_chat_sessions


@router.post("/chat/stream")
async def stream_chat(payload: ChatRequest, request: Request) -> StreamingResponse:
    _require_same_origin_for_cookie_request(request)
    sid = _session_id(request)
    active = _active_sessions(request)
    if sid:
        if sid in active:
            raise HTTPException(status_code=409, detail="目前已有回答進行中，請停止或等待後再送出")
        active.add(sid)

    async def ndjson_stream():
        try:
            from ..chat_service import chat_event_stream
            async with aclosing(chat_event_stream(payload.message, history_key=sid, session_id=sid)) as events:
                async for event in events:
                    if await request.is_disconnected():
                        break
                    yield event.model_dump_json(by_alias=True) + "\n"
        except Exception:
            yield ErrorEvent().model_dump_json(by_alias=True) + "\n"
        finally:
            if sid:
                active.discard(sid)

    return StreamingResponse(
        ndjson_stream(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@router.post(
    "/admin/manuals",
    response_model=SyncJobResponse,
    response_model_by_alias=True,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_manual(request: Request, file: UploadFile = File(...)) -> SyncJobResponse:
    _require_same_origin_for_cookie_request(request)
    saved_path = None
    try:
        saved_path = await save_manual(file)
        job_id = enqueue_sync()
        return SyncJobResponse(job_id=job_id, status="queued", message="已進入同步佇列")
    except ManualConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ManualTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except ManualUploadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        if saved_path is not None:
            saved_path.unlink(missing_ok=True)
        raise HTTPException(status_code=503, detail="暫時無法啟動同步工作") from exc


@router.get(
    "/admin/jobs/{job_id}",
    response_model=SyncJobResponse,
    response_model_by_alias=True,
)
async def sync_job(job_id: str) -> SyncJobResponse:
    if not job_id or len(job_id) > 1024:
        raise HTTPException(status_code=422, detail="Invalid job id")
    try:
        return SyncJobResponse.model_validate(get_sync_status(job_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="找不到同步工作") from exc
    except ManualUploadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="暫時無法取得同步狀態") from exc
