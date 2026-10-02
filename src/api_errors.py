"""Safe error contract shared by controllers and authentication middleware."""
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException

class ApiErrorResponse(BaseModel):
    detail: str
    code: Literal["unauthorized", "forbidden", "not_found", "conflict", "payload_too_large", "invalid_request", "rate_limited", "unavailable", "internal_error"]

_CODES = {401: "unauthorized", 403: "forbidden", 404: "not_found", 409: "conflict", 413: "payload_too_large", 422: "invalid_request", 429: "rate_limited", 503: "unavailable"}

def api_error(status: int, detail: str, headers: dict | None = None) -> JSONResponse:
    model = ApiErrorResponse(detail=detail, code=_CODES.get(status, "internal_error"))
    return JSONResponse(model.model_dump(), status_code=status, headers=headers)

def install_api_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        detail = exc.detail if isinstance(exc.detail, str) else "請求失敗，請稍後再試"
        return api_error(exc.status_code, detail, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        return api_error(422, "請求格式無效，請確認必填欄位、檔案或訊息長度")
