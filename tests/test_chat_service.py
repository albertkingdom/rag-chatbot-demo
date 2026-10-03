"""Transport-neutral chat acceptance with fake providers and persistence."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import pytest
from src import chat_service as chat

@pytest.fixture
def providers(monkeypatch):
    history = MagicMock(); history.get_history.return_value = []
    database = MagicMock(); database.async_save_conversation = AsyncMock()
    cache = MagicMock(); cache.get_cached_response.return_value = None
    router = AsyncMock(return_value=SimpleNamespace(route="rag", rewritten_query="question", reasoning="test"))
    generation = MagicMock()
    async def chunks(_payload):
        yield "完整"
        yield "回答"
    generation.astream = chunks
    retrieval = MagicMock(); retrieval.ainvoke = AsyncMock(return_value={"context":"manual", "sources":["手冊"], "rerank_scores":[1], "docs":[]})
    monkeypatch.setattr(chat, "ChatHistoryService", lambda *_: history)
    monkeypatch.setattr(chat, "get_redis_conn", lambda: MagicMock())
    monkeypatch.setattr(chat, "get_conversation_db", lambda: database)
    monkeypatch.setattr(chat, "PromptCacheService", lambda *_: cache)
    monkeypatch.setattr(chat, "route_query", router)
    monkeypatch.setattr(chat, "get_generation_chain", lambda: generation)
    monkeypatch.setattr(chat, "get_retrieval_chain", lambda: retrieval)
    monkeypatch.setattr(chat, "get_embeddings", lambda: SimpleNamespace(aembed_query=AsyncMock(return_value=[1.0])))
    monkeypatch.setattr(chat, "grade_documents", lambda *_: True)
    monkeypatch.setattr(chat, "detect_pii", lambda *_: (False, None))
    monkeypatch.setattr(chat, "detect_prompt_injection", lambda *_: (False, None))
    monkeypatch.setattr(chat, "_STREAM_CHUNK_DELAY", 0)
    return SimpleNamespace(history=history, database=database, cache=cache, router=router, generation=generation)

async def collect():
    return [event async for event in chat.chat_event_stream("question",history_key="server-session",session_id="server-session")]

@pytest.mark.parametrize("route", ["rag", "direct", "cache", "guardrail"])
def test_answer_routes_have_one_done_and_correct_metadata(providers, monkeypatch, route):
    if route == "direct": providers.router.return_value.route = "direct"
    if route == "cache": providers.cache.get_cached_response.return_value = {"answer":"快取回答", "sources":["快取手冊"], "similarity":0.99}
    if route == "guardrail": monkeypatch.setattr(chat,"detect_prompt_injection", lambda *_: (True,"input_injection"))
    events = asyncio.run(collect())
    assert events[-1].type == "done"
    assert sum(event.type == "done" for event in events) == 1
    assert next(event for event in events if event.type == "metadata").response_source == route
    assert bool([event for event in events if event.type == "sources"]) == (route in {"rag","cache"})
    providers.database.async_save_conversation.assert_awaited_once()
    if route != "guardrail": providers.history.append_turn.assert_called_once()


def test_provider_error_emits_safe_error_without_writing_partial_answer(providers):
    async def failing(_payload):
        yield "partial"
        raise RuntimeError("secret-provider-error")
    providers.generation.astream = failing
    events = asyncio.run(collect())
    assert events[-1].type == "error"
    assert not any(event.type in {"delta","done"} for event in events)
    assert "secret-provider-error" not in events[-1].model_dump_json()
    providers.history.append_turn.assert_not_called()
    providers.cache.set_cached_response.assert_not_called()
    providers.database.async_save_conversation.assert_not_awaited()


def test_cancel_during_generation_never_persists_partial_answer(providers):
    async def scenario():
        entered = asyncio.Event()
        async def slow(_payload):
            yield "partial"
            entered.set()
            await asyncio.Event().wait()
        providers.generation.astream = slow
        task = asyncio.create_task(collect())
        await asyncio.wait_for(entered.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
    asyncio.run(scenario())
    providers.history.append_turn.assert_not_called()
    providers.cache.set_cached_response.assert_not_called()
    providers.database.async_save_conversation.assert_not_awaited()


def test_two_fake_rag_requests_complete_independently(providers):
    async def scenario(): return await asyncio.gather(collect(),collect())
    results = asyncio.run(scenario())
    assert all(events[-1].type == "done" for events in results)
    assert providers.database.async_save_conversation.await_count == 2


@pytest.mark.parametrize("route", ["rag", "direct", "cache"])
def test_server_history_reaches_router_and_success_persists_plain_answer(providers, route):
    history = [{"role": "user", "content": "earlier"}, {"role": "assistant", "content": "answer"}]
    providers.history.get_history.return_value = history
    if route == "direct": providers.router.return_value.route = "direct"
    if route == "cache": providers.cache.get_cached_response.return_value = {"answer": "快取回答", "sources": ["手冊"], "similarity": 0.99}
    events = asyncio.run(collect())
    providers.history.get_history.assert_called_once_with("server-session")
    providers.router.assert_awaited_once_with("question", history)
    answer = "".join(e.text for e in events if e.type == "delta")
    providers.history.append_turn.assert_called_once_with("server-session", "question", answer)
    assert providers.database.async_save_conversation.call_args.kwargs["assistant_response"] == answer
    assert "參考資料：" not in answer and "回應時間：" not in answer


@pytest.mark.parametrize("route", ["rag", "direct", "cache"])
def test_output_guardrail_never_stores_rejected_answer_in_history_or_cache(providers, monkeypatch, route):
    if route == "direct": providers.router.return_value.route = "direct"
    if route == "cache": providers.cache.get_cached_response.return_value = {"answer": "rejected", "sources": [], "similarity": 0.99}
    monkeypatch.setattr(chat, "detect_pii", lambda *_: (True, "test-pii"))
    events = asyncio.run(collect())
    assert events[-1].type == "done"
    assert next(e for e in events if e.type == "metadata").response_source == "guardrail"
    providers.history.append_turn.assert_not_called()
    providers.cache.set_cached_response.assert_not_called()
    assert providers.database.async_save_conversation.call_args.kwargs["response_source"] == "guardrail"


@pytest.mark.parametrize("text", ["", "short", "繁體中文" * 17])
def test_answer_deltas_reconstruct_exact_body_without_accumulated_prefixes(text):
    async def run(): return [e async for e in chat._answer_events(text)]
    events = asyncio.run(run())
    assert "".join(e.text for e in events) == text
    assert all(e.type == "delta" and 0 < len(e.text) <= chat._STREAM_CHUNK_SIZE for e in events)
