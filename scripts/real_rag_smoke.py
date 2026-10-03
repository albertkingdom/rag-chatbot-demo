"""Opt-in live RAG acceptance; requires explicit provider/data authorization.

1 request then 2 concurrent requests. Credentials must be supplied privately by
the caller. Real routing, embeddings, vector reads, reranking and generation;
no history/cache/Mongo writes, tracing or knowledge-base synchronization.
"""
import asyncio
import argparse
import contextvars
import traceback
import json
import logging
import os
import sys
import time
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.getcwd())
os.environ.update(LANGCHAIN_TRACING_V2="false", LANGSMITH_TRACING="false")
logging.disable(logging.CRITICAL)
from src import chat_service, services
from src.chat_events import DeltaEvent, DoneEvent, ErrorEvent, MetadataEvent, SourcesEvent, StatusEvent

chat_service.CACHE_ENABLED = False
chat_service.get_redis_conn = lambda: MagicMock(get=lambda *_: None)
chat_service.get_conversation_db = lambda: MagicMock(async_save_conversation=AsyncMock())

diagnostics = contextvars.ContextVar("diagnostics", default=None)

def capture_error(*args, **kwargs):
    bucket = diagnostics.get()
    error = sys.exc_info()[1]
    if bucket is not None and error is not None:
        bucket.append({
            "type": type(error).__name__,
            "status_code": getattr(error, "status_code", None),
            "frames": [f"{os.path.basename(frame.filename)}:{frame.lineno}:{frame.name}"
                       for frame in traceback.extract_tb(error.__traceback__)],
        })

# Capture code locations only; never log provider response text or credentials.
chat_service.logger.exception = capture_error

async def ask(question):
    start = time.monotonic()
    errors = []
    diagnostics.set(errors)
    stages = []
    chars = sources = 0
    metadata = None
    terminal = False
    async for event in chat_service.chat_event_stream(question, history_key=None, session_id=None):
        if isinstance(event, ErrorEvent):
            return {"passed": False, "seconds": round(time.monotonic() - start, 2), "stages": stages, "errors": errors}
        if isinstance(event, StatusEvent):
            stages.append(event.stage)
        elif isinstance(event, DeltaEvent):
            chars += len(event.text)
        elif isinstance(event, SourcesEvent):
            sources += len(event.items)
        elif isinstance(event, MetadataEvent):
            metadata = event
        elif isinstance(event, DoneEvent):
            terminal = True
    assert terminal and chars > 0 and sources > 0
    assert metadata and metadata.response_source == "rag" and not metadata.cache_hit
    return {"passed": True, "seconds": round(time.monotonic() - start, 2), "answer_chars": chars, "sources": sources, "route": metadata.response_source}

async def main(concurrent_only=False):
    start = time.monotonic()
    await asyncio.to_thread(services.get_reranker_model)
    startup = round(time.monotonic() - start, 2)
    one = None if concurrent_only else await asyncio.wait_for(ask("碳管理系統如何設定盤查邊界？"), timeout=180)
    two = await asyncio.wait_for(asyncio.gather(
        ask("碳管理系統如何新增固定式燃料源排放？"),
        ask("碳盤查系統如何產生報告和清冊？"),
    ), timeout=240)
    print(json.dumps({"model_startup_seconds": startup, "single": one, "concurrent_two": two, "persistence": "isolated", "cache": "disabled", "tracing": "disabled"}, ensure_ascii=False))
    if (one is not None and not one["passed"]) or any(not result["passed"] for result in two):
        raise SystemExit(1)

if __name__ == "__main__":
    if os.environ.get("ALLOW_LIVE_RAG_SMOKE") != "true":
        raise SystemExit("Explicit provider/data authorization is required; set ALLOW_LIVE_RAG_SMOKE=true only after approval.")
    parser = argparse.ArgumentParser()
    parser.add_argument("--concurrent-only", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.concurrent_only))
