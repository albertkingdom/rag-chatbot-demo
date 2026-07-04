"""
Unit tests for the Chat History Service.
"""
import json
from unittest.mock import MagicMock

import pytest

from src.chat_history_service import ChatHistoryService
from src.config import CHAT_HISTORY_MAX_TURNS, CHAT_HISTORY_TTL_SECONDS


@pytest.fixture
def mock_redis():
    """Fixture to create a mock Redis connection backed by an in-memory dict."""
    store = {}
    redis_mock = MagicMock()
    redis_mock.get.side_effect = lambda key: store.get(key)
    redis_mock.setex.side_effect = lambda key, ttl, value: store.__setitem__(key, value)
    redis_mock.delete.side_effect = lambda key: store.pop(key, None)
    redis_mock._store = store
    return redis_mock


@pytest.fixture
def chat_history_service(mock_redis):
    """Fixture to create a ChatHistoryService instance with mock Redis."""
    return ChatHistoryService(mock_redis)


class TestGetHistory:
    """Tests for get_history."""

    def test_no_data_returns_empty_list(self, chat_history_service):
        assert chat_history_service.get_history("session-1") == []

    def test_none_history_key_returns_empty_list(self, chat_history_service):
        assert chat_history_service.get_history(None) == []

    def test_returns_stored_history(self, chat_history_service, mock_redis):
        history = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        mock_redis._store["chat_history:session-1"] = json.dumps(history)
        assert chat_history_service.get_history("session-1") == history

    def test_redis_exception_returns_empty_list(self, chat_history_service, mock_redis):
        mock_redis.get.side_effect = Exception("connection error")
        assert chat_history_service.get_history("session-1") == []


class TestAppendTurn:
    """Tests for append_turn."""

    def test_written_turn_can_be_read_back(self, chat_history_service):
        chat_history_service.append_turn("session-1", "q1", "a1")
        assert chat_history_service.get_history("session-1") == [
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
        ]

    def test_trims_to_max_turns(self, chat_history_service):
        for i in range(CHAT_HISTORY_MAX_TURNS + 2):
            chat_history_service.append_turn("session-1", f"q{i}", f"a{i}")

        history = chat_history_service.get_history("session-1")
        assert len(history) == CHAT_HISTORY_MAX_TURNS * 2
        # Oldest turns discarded first: last stored turn's user message is the
        # most recent one appended.
        assert history[-2] == {
            "role": "user",
            "content": f"q{CHAT_HISTORY_MAX_TURNS + 1}",
        }

    def test_writes_with_configured_ttl(self, chat_history_service, mock_redis):
        chat_history_service.append_turn("session-1", "q1", "a1")
        mock_redis.setex.assert_called_with(
            "chat_history:session-1",
            CHAT_HISTORY_TTL_SECONDS,
            mock_redis.setex.call_args[0][2],
        )

    def test_none_history_key_is_noop(self, chat_history_service, mock_redis):
        chat_history_service.append_turn(None, "q1", "a1")
        mock_redis.setex.assert_not_called()


class TestClearHistory:
    """Tests for clear_history."""

    def test_deletes_stored_history(self, chat_history_service):
        chat_history_service.append_turn("session-1", "q1", "a1")
        chat_history_service.clear_history("session-1")
        assert chat_history_service.get_history("session-1") == []

    def test_none_history_key_is_noop(self, chat_history_service, mock_redis):
        chat_history_service.clear_history(None)
        mock_redis.delete.assert_not_called()

    def test_clearing_empty_history_does_not_raise(self, chat_history_service):
        chat_history_service.clear_history("session-with-no-history")


class TestRedisFailure:
    """Tests for fail-open behavior on Redis exceptions."""

    def test_get_history_does_not_raise(self, chat_history_service, mock_redis):
        mock_redis.get.side_effect = Exception("boom")
        assert chat_history_service.get_history("session-1") == []

    def test_append_turn_does_not_raise(self, chat_history_service, mock_redis):
        mock_redis.setex.side_effect = Exception("boom")
        chat_history_service.append_turn("session-1", "q1", "a1")

    def test_clear_history_does_not_raise(self, chat_history_service, mock_redis):
        mock_redis.delete.side_effect = Exception("boom")
        chat_history_service.clear_history("session-1")
