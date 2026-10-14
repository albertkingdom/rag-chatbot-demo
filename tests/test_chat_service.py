"""Transport-neutral chat acceptance with fake providers and persistence."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import pytest
from src import chat_service as chat

def rerank_scores(top):
    """Real rerank_scores shape (rank + string score); None means no documents."""
    if top is None:
        return []
    return [{"rank": 1, "score": f"{top:.4f}", "content": "手冊..."}]

def bundle(top=0.9, context="manual", sources=("手冊",)):
    """One retrieval-chain result whose top-1 rerank score is `top`."""
    return {"context": context, "sources": list(sources), "rerank_scores": rerank_scores(top), "docs": []}

@pytest.fixture
def providers(monkeypatch):
    history = MagicMock(); history.get_history.return_value = []
    database = MagicMock(); database.async_save_conversation = AsyncMock()
    cache = MagicMock(); cache.get_cached_response.return_value = None
    router = AsyncMock(return_value=SimpleNamespace(route="rag", rewritten_query="question", reasoning="test"))
    generation = MagicMock()
    generation.payloads = []
    async def chunks(payload):
        generation.payloads.append(payload)
        yield "完整"
        yield "回答"
    generation.astream = chunks
    retrieval = MagicMock(); retrieval.ainvoke = AsyncMock(return_value=bundle(0.9))
    grade = MagicMock(return_value=True)
    rewrite = AsyncMock(return_value="retry query")
    monkeypatch.setattr(chat, "ChatHistoryService", lambda *_: history)
    monkeypatch.setattr(chat, "get_redis_conn", lambda: MagicMock())
    monkeypatch.setattr(chat, "get_conversation_db", lambda: database)
    monkeypatch.setattr(chat, "PromptCacheService", lambda *_: cache)
    monkeypatch.setattr(chat, "route_query", router)
    monkeypatch.setattr(chat, "get_generation_chain", lambda: generation)
    monkeypatch.setattr(chat, "get_retrieval_chain", lambda: retrieval)
    monkeypatch.setattr(chat, "get_embeddings", lambda: SimpleNamespace(aembed_query=AsyncMock(return_value=[1.0])))
    monkeypatch.setattr(chat, "grade_documents", grade)
    monkeypatch.setattr(chat, "rewrite_for_retry", rewrite)
    monkeypatch.setattr(chat, "detect_pii", lambda *_: (False, None))
    monkeypatch.setattr(chat, "detect_prompt_injection", lambda *_: (False, None))
    monkeypatch.setattr(chat, "_STREAM_CHUNK_DELAY", 0)
    return SimpleNamespace(history=history, database=database, cache=cache, router=router, generation=generation,
                           retrieval=retrieval, grade=grade, rewrite=rewrite)

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


# ---------------------------------------------------------------------------
# Retrieval retry, low-confidence handling and retrieval metadata
# (openspec change: handle-low-confidence-retrieval)
# ---------------------------------------------------------------------------

from src import retrieval_grader
from src.rag_pipeline import LOW_CONFIDENCE_NOTE, LOW_CONFIDENCE_PREFIX


@pytest.fixture
def graded(providers, monkeypatch):
    """Real grade_documents with the threshold pinned at 0.30 (it is
    overridable by environment variable, so tests must not rely on the default)."""
    monkeypatch.setattr(retrieval_grader, "GRADE_SCORE_THRESHOLD", 0.30)
    monkeypatch.setattr(chat, "grade_documents", retrieval_grader.grade_documents)
    return providers


def delta_text(events):
    return "".join(e.text for e in events if e.type == "delta")


def saved_metadata(providers):
    return providers.database.async_save_conversation.call_args.kwargs.get("metadata")


def context_used(providers):
    return providers.generation.payloads[-1]["context"]


class TestRetryKeepsTheBetterRetrieval:
    @pytest.mark.parametrize(
        "first, retry, kept",
        [
            (0.28, 0.05, "ORIG"),   # retry is worse -> keep the original
            (0.10, 0.45, "RETRY"),  # retry is better -> use the retry
            (0.20, 0.20, "ORIG"),   # tie -> keep the earlier result
            (None, 0.15, "RETRY"),  # no documents is the lowest possible score
        ],
    )
    def test_generation_uses_the_attempt_with_the_higher_top1_score(self, graded, first, retry, kept):
        graded.retrieval.ainvoke.side_effect = [
            bundle(first, context="ORIG"),
            bundle(retry, context="RETRY"),
        ]

        asyncio.run(collect())

        assert context_used(graded) == kept
        assert graded.retrieval.ainvoke.await_count == 2

    def test_the_retry_is_made_with_the_rewritten_query(self, graded):
        graded.retrieval.ainvoke.side_effect = [bundle(0.10), bundle(0.45)]

        asyncio.run(collect())

        assert [c.args[0] for c in graded.retrieval.ainvoke.await_args_list] == ["question", "retry query"]

    def test_first_retrieval_that_passes_is_not_retried(self, graded):
        graded.retrieval.ainvoke.side_effect = [bundle(0.80)]

        asyncio.run(collect())

        assert graded.retrieval.ainvoke.await_count == 1
        graded.rewrite.assert_not_awaited()

    def test_retrying_stops_once_an_attempt_passes(self, graded, monkeypatch):
        monkeypatch.setattr(chat, "RETRIEVAL_MAX_RETRIES", 2)
        graded.retrieval.ainvoke.side_effect = [bundle(0.10), bundle(0.45, context="RETRY1")]

        asyncio.run(collect())

        assert graded.retrieval.ainvoke.await_count == 2
        assert graded.rewrite.await_count == 1
        assert context_used(graded) == "RETRY1"


class TestRedundantRetriesAreSkipped:
    @pytest.mark.parametrize("rewritten", ["question", "  question  ", "", "   "])
    def test_no_second_retrieval_for_a_repeated_or_empty_rewrite(self, graded, rewritten):
        graded.rewrite.return_value = rewritten
        graded.retrieval.ainvoke.side_effect = [bundle(0.20, context="ORIG")]

        asyncio.run(collect())

        assert graded.retrieval.ainvoke.await_count == 1
        assert graded.rewrite.await_count == 1  # the rewrite itself still happens
        assert context_used(graded) == "ORIG"
        retrieval = saved_metadata(graded)["retrieval"]
        assert retrieval["retry_count"] == 0
        assert retrieval["retry_skipped"] is True

    def test_second_rewrite_repeating_the_first_retry_query_stops_retrying(self, graded, monkeypatch):
        monkeypatch.setattr(chat, "RETRIEVAL_MAX_RETRIES", 2)
        graded.rewrite.side_effect = ["retry one", "retry one"]
        graded.retrieval.ainvoke.side_effect = [bundle(0.10), bundle(0.12)]

        asyncio.run(collect())

        assert graded.retrieval.ainvoke.await_count == 2
        retrieval = saved_metadata(graded)["retrieval"]
        assert retrieval["retry_count"] == 1
        assert retrieval["retry_skipped"] is True

    def test_retrying_disabled_is_not_reported_as_skipped(self, graded, monkeypatch):
        monkeypatch.setattr(chat, "RETRIEVAL_MAX_RETRIES", 0)
        graded.retrieval.ainvoke.side_effect = [bundle(0.10)]

        asyncio.run(collect())

        graded.rewrite.assert_not_awaited()
        assert saved_metadata(graded)["retrieval"]["retry_skipped"] is False


class TestLowConfidenceAnswers:
    @pytest.fixture(autouse=True)
    def _no_retry(self, monkeypatch):
        monkeypatch.setattr(chat, "RETRIEVAL_MAX_RETRIES", 0)

    def test_score_below_threshold_is_labelled_and_has_no_sources(self, graded):
        graded.retrieval.ainvoke.side_effect = [bundle(0.29)]

        events = asyncio.run(collect())

        assert graded.generation.payloads[-1]["confidence_note"] == LOW_CONFIDENCE_NOTE
        assert delta_text(events).startswith(LOW_CONFIDENCE_PREFIX + "\n\n")
        assert not [e for e in events if e.type == "sources"]
        assert next(e for e in events if e.type == "metadata").response_source == "rag"

    def test_score_equal_to_threshold_is_not_low_confidence(self, graded):
        graded.retrieval.ainvoke.side_effect = [bundle(0.30)]

        events = asyncio.run(collect())

        assert graded.generation.payloads[-1]["confidence_note"] == ""
        assert not delta_text(events).startswith(LOW_CONFIDENCE_PREFIX)
        assert [e for e in events if e.type == "sources"]

    def test_no_documents_still_generates_with_the_label(self, graded, monkeypatch):
        monkeypatch.setattr(chat, "RETRIEVAL_MAX_RETRIES", 1)
        graded.retrieval.ainvoke.side_effect = [bundle(None, context=""), bundle(None, context="")]

        events = asyncio.run(collect())

        payload = graded.generation.payloads[-1]
        assert payload["context"] == ""
        assert payload["confidence_note"] == LOW_CONFIDENCE_NOTE
        assert delta_text(events).startswith(LOW_CONFIDENCE_PREFIX)

    def test_streamed_history_and_saved_text_are_identical(self, graded):
        graded.retrieval.ainvoke.side_effect = [bundle(0.20)]

        events = asyncio.run(collect())

        streamed = delta_text(events)
        assert streamed == LOW_CONFIDENCE_PREFIX + "\n\n" + "完整回答"
        graded.history.append_turn.assert_called_once_with("server-session", "question", streamed)
        assert graded.database.async_save_conversation.call_args.kwargs["assistant_response"] == streamed

    def test_low_confidence_answer_is_not_cached_but_is_stored(self, graded):
        graded.retrieval.ainvoke.side_effect = [bundle(0.20)]

        asyncio.run(collect())

        graded.cache.set_cached_response.assert_not_called()
        graded.history.append_turn.assert_called_once()
        graded.database.async_save_conversation.assert_awaited_once()

    def test_guardrail_replacing_a_low_confidence_answer_keeps_existing_behavior(self, graded, monkeypatch):
        graded.retrieval.ainvoke.side_effect = [bundle(0.20)]
        monkeypatch.setattr(chat, "detect_pii", lambda *_: (True, "email"))

        events = asyncio.run(collect())

        assert next(e for e in events if e.type == "metadata").response_source == "guardrail"
        graded.history.append_turn.assert_not_called()
        graded.cache.set_cached_response.assert_not_called()
        kwargs = graded.database.async_save_conversation.call_args.kwargs
        assert kwargs["response_source"] == "guardrail"
        assert kwargs["metadata"]["pii_type"] == "email"
        assert kwargs["metadata"]["retrieval"]["low_confidence"] is True


class TestHighConfidenceAnswersAreUnchanged:
    def test_no_label_sources_sent_and_answer_cached(self, graded):
        graded.retrieval.ainvoke.side_effect = [bundle(0.80, sources=("手冊A",))]

        events = asyncio.run(collect())

        assert graded.generation.payloads[-1]["confidence_note"] == ""
        assert delta_text(events) == "完整回答"
        assert next(e for e in events if e.type == "sources").items[0].label == "手冊A"
        graded.cache.set_cached_response.assert_called_once()
        assert graded.cache.set_cached_response.call_args.args[3] == ["手冊A"]
        assert saved_metadata(graded)["retrieval"]["low_confidence"] is False


class TestRetrievalMetadata:
    def test_after_a_successful_retry(self, graded):
        graded.retrieval.ainvoke.side_effect = [bundle(0.10), bundle(0.45)]

        asyncio.run(collect())

        assert saved_metadata(graded)["retrieval"] == {
            "top1_score": 0.45,
            "attempt_scores": [0.10, 0.45],
            "retry_count": 1,
            "kept_attempt": 1,
            "low_confidence": False,
            "threshold": 0.30,
            "retry_skipped": False,
        }

    def test_for_a_low_confidence_answer(self, graded):
        graded.retrieval.ainvoke.side_effect = [bundle(0.20), bundle(0.05)]

        asyncio.run(collect())

        assert saved_metadata(graded)["retrieval"] == {
            "top1_score": 0.20,
            "attempt_scores": [0.20, 0.05],
            "retry_count": 1,
            "kept_attempt": 0,
            "low_confidence": True,
            "threshold": 0.30,
            "retry_skipped": False,
        }

    def test_when_the_retry_is_skipped(self, graded):
        graded.rewrite.return_value = "question"
        graded.retrieval.ainvoke.side_effect = [bundle(0.20)]

        asyncio.run(collect())

        assert saved_metadata(graded)["retrieval"] == {
            "top1_score": 0.20,
            "attempt_scores": [0.20],
            "retry_count": 0,
            "kept_attempt": 0,
            "low_confidence": True,
            "threshold": 0.30,
            "retry_skipped": True,
        }

    def test_no_documents_records_null_scores(self, graded):
        graded.retrieval.ainvoke.side_effect = [bundle(None), bundle(None)]

        asyncio.run(collect())

        retrieval = saved_metadata(graded)["retrieval"]
        assert retrieval["top1_score"] is None
        assert retrieval["attempt_scores"] == [None, None]
        assert retrieval["low_confidence"] is True

    @pytest.mark.parametrize("route", ["direct", "cache"])
    def test_direct_and_cache_records_carry_no_retrieval_object(self, providers, route):
        if route == "direct":
            providers.router.return_value.route = "direct"
        if route == "cache":
            providers.cache.get_cached_response.return_value = {"answer": "快取回答", "sources": ["手冊"], "similarity": 0.99}

        asyncio.run(collect())

        assert "retrieval" not in (saved_metadata(providers) or {})
