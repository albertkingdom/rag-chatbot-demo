"""
Tests for chat_stream's Redis-backed short-term history wiring:
- history_key derivation (session cookie preferred over session_hash)
- reading stored history from Redis to override the client-supplied history
- writing turns back to Redis only on genuine successful answers
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src import rag_pipeline
from src.access_control import SESSION_COOKIE


def make_request(cookies=None, session_hash="hash-1"):
    return SimpleNamespace(cookies=cookies or {}, session_hash=session_hash)


async def drain(agen):
    chunks = []
    async for chunk in agen:
        chunks.append(chunk)
    return chunks


@pytest.fixture(autouse=True)
def common_mocks():
    """Patch dependencies that are irrelevant to history wiring so chat_stream
    can run end-to-end without hitting real services."""
    with patch.object(rag_pipeline, "get_redis_conn", return_value=MagicMock()), \
         patch.object(rag_pipeline, "get_conversation_db") as mock_db_factory, \
         patch.object(rag_pipeline, "get_intent_classifier", return_value=None), \
         patch.object(rag_pipeline, "detect_prompt_injection", return_value=(False, None)), \
         patch.object(rag_pipeline, "detect_pii", return_value=(False, None)), \
         patch.object(rag_pipeline, "get_embeddings") as mock_embeddings_factory, \
         patch.object(rag_pipeline, "get_retrieval_chain") as mock_retrieval_factory, \
         patch.object(rag_pipeline, "get_generation_chain") as mock_generation_factory, \
         patch.object(rag_pipeline, "CACHE_ENABLED", False):

        mock_db = MagicMock()
        mock_db.async_save_conversation = AsyncMock()
        mock_db_factory.return_value = mock_db

        mock_embeddings = MagicMock()
        mock_embeddings.aembed_query = AsyncMock(return_value=[0.1, 0.2, 0.3])
        mock_embeddings_factory.return_value = mock_embeddings

        mock_retrieval_chain = MagicMock()
        mock_retrieval_chain.ainvoke = AsyncMock(
            return_value={"context": "some context", "contexts": [], "sources": []}
        )
        mock_retrieval_factory.return_value = mock_retrieval_chain

        async def fake_astream(_payload):
            for chunk in ["answer"]:
                yield chunk

        mock_generation_chain = MagicMock()
        mock_generation_chain.astream = fake_astream
        mock_generation_factory.return_value = mock_generation_chain

        yield {"db": mock_db}


class TestHistoryKeyDerivation:
    async def _run_and_capture_history_key(self, request):
        with patch.object(rag_pipeline, "ChatHistoryService") as mock_service_cls:
            mock_instance = MagicMock()
            mock_instance.get_history.return_value = []
            mock_service_cls.return_value = mock_instance

            await drain(rag_pipeline.chat_stream("hello", [], request))

            return mock_instance

    @pytest.mark.asyncio
    async def test_prefers_session_cookie_over_session_hash(self):
        request = make_request(cookies={SESSION_COOKIE: "cookie-sid"}, session_hash="hash-1")
        mock_instance = await self._run_and_capture_history_key(request)
        mock_instance.get_history.assert_called_once_with("cookie-sid")

    @pytest.mark.asyncio
    async def test_falls_back_to_session_hash_without_cookie(self):
        request = make_request(cookies={}, session_hash="hash-1")
        mock_instance = await self._run_and_capture_history_key(request)
        mock_instance.get_history.assert_called_once_with("hash-1")


class TestHistoryReadWiring:
    @pytest.mark.asyncio
    async def test_stored_history_overrides_empty_client_history(self):
        request = make_request(cookies={SESSION_COOKIE: "cookie-sid"})
        stored = [
            {"role": "user", "content": "previous question"},
            {"role": "assistant", "content": "previous answer"},
        ]
        with patch.object(rag_pipeline, "ChatHistoryService") as mock_service_cls, \
             patch.object(rag_pipeline, "format_history", return_value="") as mock_format, \
             patch.object(rag_pipeline, "rewrite_query", new=AsyncMock(return_value="hello")):
            mock_instance = MagicMock()
            mock_instance.get_history.return_value = stored
            mock_service_cls.return_value = mock_instance

            # Second call simulates a page refresh: client sends empty history.
            await drain(rag_pipeline.chat_stream("hello", [], request))

            mock_format.assert_called_once_with(stored)

    @pytest.mark.asyncio
    async def test_empty_stored_history_falls_back_to_client_history(self):
        request = make_request(cookies={SESSION_COOKIE: "cookie-sid"})
        client_history = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}]
        with patch.object(rag_pipeline, "ChatHistoryService") as mock_service_cls, \
             patch.object(rag_pipeline, "format_history", return_value="") as mock_format, \
             patch.object(rag_pipeline, "rewrite_query", new=AsyncMock(return_value="hello")):
            mock_instance = MagicMock()
            mock_instance.get_history.return_value = []
            mock_service_cls.return_value = mock_instance

            await drain(rag_pipeline.chat_stream("hello", client_history, request))

            mock_format.assert_called_once_with(client_history)


class TestHistoryWriteWiring:
    @pytest.mark.asyncio
    async def test_append_turn_called_on_rag_success(self):
        request = make_request(cookies={SESSION_COOKIE: "cookie-sid"})
        with patch.object(rag_pipeline, "ChatHistoryService") as mock_service_cls:
            mock_instance = MagicMock()
            mock_instance.get_history.return_value = []
            mock_service_cls.return_value = mock_instance

            await drain(rag_pipeline.chat_stream("hello", [], request))

            mock_instance.append_turn.assert_called_once_with("cookie-sid", "hello", "answer")

    @pytest.mark.asyncio
    async def test_append_turn_called_on_cache_hit(self, common_mocks):
        request = make_request(cookies={SESSION_COOKIE: "cookie-sid"})
        with patch.object(rag_pipeline, "ChatHistoryService") as mock_service_cls, \
             patch.object(rag_pipeline, "CACHE_ENABLED", True), \
             patch.object(rag_pipeline, "PromptCacheService") as mock_cache_service_cls:
            mock_instance = MagicMock()
            mock_instance.get_history.return_value = []
            mock_service_cls.return_value = mock_instance

            mock_cache_service = MagicMock()
            mock_cache_service.get_cached_response.return_value = {
                "answer": "cached answer",
                "similarity": 0.99,
                "hit_count": 1,
            }
            mock_cache_service_cls.return_value = mock_cache_service

            await drain(rag_pipeline.chat_stream("hello", [], request))

            mock_instance.append_turn.assert_called_once_with(
                "cookie-sid", "hello", "cached answer"
            )

    @pytest.mark.asyncio
    async def test_append_turn_not_called_on_off_topic(self):
        request = make_request(cookies={SESSION_COOKIE: "cookie-sid"})
        mock_intent_classifier = MagicMock()
        mock_intent_classifier.classify = AsyncMock(
            return_value={"relevant": False, "confidence": 0.1}
        )
        mock_intent_classifier.get_off_topic_message.return_value = "off topic"

        with patch.object(rag_pipeline, "ChatHistoryService") as mock_service_cls, \
             patch.object(rag_pipeline, "get_intent_classifier", return_value=mock_intent_classifier):
            mock_instance = MagicMock()
            mock_instance.get_history.return_value = []
            mock_service_cls.return_value = mock_instance

            await drain(rag_pipeline.chat_stream("hello", [], request))

            mock_instance.append_turn.assert_not_called()

    @pytest.mark.asyncio
    async def test_append_turn_not_called_on_guardrail_block(self):
        request = make_request(cookies={SESSION_COOKIE: "cookie-sid"})
        with patch.object(rag_pipeline, "ChatHistoryService") as mock_service_cls, \
             patch.object(rag_pipeline, "detect_prompt_injection", return_value=(True, "pattern")):
            mock_instance = MagicMock()
            mock_instance.get_history.return_value = []
            mock_service_cls.return_value = mock_instance

            await drain(rag_pipeline.chat_stream("hello", [], request))

            mock_instance.append_turn.assert_not_called()


class TestAnswerSources:
    """Tests for appending a "參考資料：" source list to the streamed answer."""

    @pytest.mark.asyncio
    async def test_rag_answer_gets_source_block_appended(self):
        request = make_request(cookies={SESSION_COOKIE: "cookie-sid"})
        with patch.object(rag_pipeline, "ChatHistoryService") as mock_service_cls, \
             patch.object(rag_pipeline, "get_retrieval_chain") as mock_retrieval_factory:
            mock_instance = MagicMock()
            mock_instance.get_history.return_value = []
            mock_service_cls.return_value = mock_instance

            mock_retrieval_chain = MagicMock()
            mock_retrieval_chain.ainvoke = AsyncMock(
                return_value={
                    "context": "some context",
                    "contexts": [],
                    "sources": ["密碼忘記了要怎麼重設啊？"],
                }
            )
            mock_retrieval_factory.return_value = mock_retrieval_chain

            chunks = await drain(rag_pipeline.chat_stream("hello", [], request))

            assert chunks[-1] == "answer\n\n參考資料：\n- 密碼忘記了要怎麼重設啊？"
            # The stored/cached answer text stays the pure generated text.
            mock_instance.append_turn.assert_called_once_with("cookie-sid", "hello", "answer")

    @pytest.mark.asyncio
    async def test_rag_answer_with_empty_sources_has_no_block(self):
        request = make_request(cookies={SESSION_COOKIE: "cookie-sid"})
        with patch.object(rag_pipeline, "ChatHistoryService") as mock_service_cls:
            mock_instance = MagicMock()
            mock_instance.get_history.return_value = []
            mock_service_cls.return_value = mock_instance

            chunks = await drain(rag_pipeline.chat_stream("hello", [], request))

            assert chunks[-1] == "answer"
            assert "參考資料" not in chunks[-1]

    @pytest.mark.asyncio
    async def test_off_topic_response_has_no_source_block(self):
        request = make_request(cookies={SESSION_COOKIE: "cookie-sid"})
        mock_intent_classifier = MagicMock()
        mock_intent_classifier.classify = AsyncMock(
            return_value={"relevant": False, "confidence": 0.1}
        )
        mock_intent_classifier.get_off_topic_message.return_value = "off topic"

        with patch.object(rag_pipeline, "ChatHistoryService") as mock_service_cls, \
             patch.object(rag_pipeline, "get_intent_classifier", return_value=mock_intent_classifier):
            mock_instance = MagicMock()
            mock_instance.get_history.return_value = []
            mock_service_cls.return_value = mock_instance

            chunks = await drain(rag_pipeline.chat_stream("hello", [], request))

            assert all("參考資料" not in chunk for chunk in chunks)

    @pytest.mark.asyncio
    async def test_guardrail_response_has_no_source_block(self):
        request = make_request(cookies={SESSION_COOKIE: "cookie-sid"})
        with patch.object(rag_pipeline, "ChatHistoryService") as mock_service_cls, \
             patch.object(rag_pipeline, "detect_prompt_injection", return_value=(True, "pattern")):
            mock_instance = MagicMock()
            mock_instance.get_history.return_value = []
            mock_service_cls.return_value = mock_instance

            chunks = await drain(rag_pipeline.chat_stream("hello", [], request))

            assert all("參考資料" not in chunk for chunk in chunks)

    @pytest.mark.asyncio
    async def test_cache_hit_answer_gets_source_block_appended(self):
        request = make_request(cookies={SESSION_COOKIE: "cookie-sid"})
        with patch.object(rag_pipeline, "ChatHistoryService") as mock_service_cls, \
             patch.object(rag_pipeline, "CACHE_ENABLED", True), \
             patch.object(rag_pipeline, "PromptCacheService") as mock_cache_service_cls:
            mock_instance = MagicMock()
            mock_instance.get_history.return_value = []
            mock_service_cls.return_value = mock_instance

            mock_cache_service = MagicMock()
            mock_cache_service.get_cached_response.return_value = {
                "answer": "cached answer",
                "sources": ["密碼忘記了要怎麼重設啊？"],
                "similarity": 0.99,
                "hit_count": 1,
            }
            mock_cache_service_cls.return_value = mock_cache_service

            chunks = await drain(rag_pipeline.chat_stream("hello", [], request))

            assert chunks[-1] == "cached answer\n\n參考資料：\n- 密碼忘記了要怎麼重設啊？"
            # The stored history keeps the pure cached answer text.
            mock_instance.append_turn.assert_called_once_with(
                "cookie-sid", "hello", "cached answer"
            )


class TestIntentClassifierHistoryForwarding:
    @pytest.mark.asyncio
    async def test_chat_stream_forwards_history_to_classify(self):
        request = make_request(cookies={SESSION_COOKIE: "cookie-sid"})
        client_history = [
            {"role": "user", "content": "門檻值設定該如何填寫？"},
            {"role": "assistant", "content": "您需要填寫顯著性門檻、實質性門檻和排除門檻。"},
        ]

        mock_intent_classifier = MagicMock()
        mock_intent_classifier.classify = AsyncMock(
            return_value={"relevant": True, "confidence": 0.9}
        )

        with patch.object(rag_pipeline, "ChatHistoryService") as mock_service_cls, \
             patch.object(rag_pipeline, "get_intent_classifier", return_value=mock_intent_classifier), \
             patch.object(rag_pipeline, "rewrite_query", new=AsyncMock(return_value="您確定嗎？")):
            mock_instance = MagicMock()
            mock_instance.get_history.return_value = []
            mock_service_cls.return_value = mock_instance

            await drain(rag_pipeline.chat_stream("你確定嗎", client_history, request))

            mock_intent_classifier.classify.assert_called_once_with("您確定嗎？", client_history)
