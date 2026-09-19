"""FastAPI routes consumed by the React frontend."""

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
)
from ..chat_history_service import ChatHistoryService
from ..manual_service import (
    ManualTooLargeError,
    ManualUploadError,
    enqueue_sync,
    get_sync_status,
    save_manual,
)

router = APIRouter(prefix="/api/v1")

MessageText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
ApiKeyText = Annotated[str, StringConstraints(min_length=1, max_length=4096)]


class ChatRequest(BaseModel):
    message: MessageText


class LoginRequest(BaseModel):
    api_key: ApiKeyText = Field(alias="apiKey")
    model_config = ConfigDict(populate_by_name=True)


class MessageItem(BaseModel):
    role: str
    content: str


class MessageList(BaseModel):
    items: list[MessageItem]


class SyncJobResponse(BaseModel):
    job_id: str = Field(alias="jobId", min_length=1)
    status: str
    message: str | None = None
    model_config = ConfigDict(populate_by_name=True)


def _auth_config(request: Request) -> AuthConfig:
    return request.app.state.auth_config


def _session_id(request: Request) -> str | None:
    config = _auth_config(request)
    if not config.enabled:
        return None
    return request.cookies.get(SESSION_COOKIE)


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


@router.get("/auth/session")
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

    sid = create_session(config.redis, _key_hash(payload.api_key), config.session_ttl_seconds)
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
        revoke_session(config.redis, sid)
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
    if sid and not ChatHistoryService(_auth_config(request).redis).clear_history(sid):
        raise HTTPException(status_code=503, detail="暫時無法清除對話，請稍後再試")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/chat/stream")
async def stream_chat(payload: ChatRequest, request: Request) -> StreamingResponse:
    _require_same_origin_for_cookie_request(request)
    sid = _session_id(request)

    async def ndjson_stream():
        # Keep the model/RAG dependency graph out of lightweight API imports
        # and health checks. It is initialized only when a question arrives.
        from ..chat_service import chat_event_stream

        async for event in chat_event_stream(
            payload.message,
            history_key=sid,
            session_id=sid,
        ):
            if await request.is_disconnected():
                break
            yield event.model_dump_json(by_alias=True) + "\n"

    return StreamingResponse(
        ndjson_stream(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
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
