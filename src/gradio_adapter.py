"""Temporary legacy display adapter over the typed chat service."""
from .access_control import SESSION_COOKIE
from .rag_pipeline import _append_source_block, _append_timing_line

async def chat_stream(message, history, request=None):
    from .chat_service import chat_event_stream
    sid = request.cookies.get(SESSION_COOKIE) if request else None
    history_key = sid or (request.session_hash if request else None)
    content = ""
    sources = []
    elapsed = None
    async for event in chat_event_stream(message, history_key=history_key, session_id=history_key, fallback_history=history):
        if event.type == "status":
            yield event.message
        elif event.type == "delta":
            content += event.text
            yield content
        elif event.type == "sources":
            sources = [item.label for item in event.items]
        elif event.type == "metadata":
            elapsed = event.elapsed_ms / 1000
        elif event.type == "done":
            answer = _append_source_block(content, sources)
            yield _append_timing_line(answer, elapsed) if elapsed is not None else answer
        elif event.type == "error":
            yield event.message
