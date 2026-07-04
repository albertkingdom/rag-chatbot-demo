"""
Tests for clear_chat_history: clearing the visible conversation (Gradio
Chatbot's built-in clear icon) must also clear the session's Redis-stored
chat history.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src import ui
from src.access_control import SESSION_COOKIE


def make_request(cookies=None, session_hash="hash-1"):
    return SimpleNamespace(cookies=cookies or {}, session_hash=session_hash)


class TestClearChatHistory:
    def test_clears_history_for_session_cookie(self):
        request = make_request(cookies={SESSION_COOKIE: "cookie-sid"})
        with patch.object(ui, "get_redis_conn", return_value=MagicMock()), \
             patch.object(ui, "ChatHistoryService") as mock_service_cls:
            mock_instance = MagicMock()
            mock_service_cls.return_value = mock_instance

            ui.clear_chat_history(request)

            mock_instance.clear_history.assert_called_once_with("cookie-sid")

    def test_falls_back_to_session_hash_without_cookie(self):
        request = make_request(cookies={}, session_hash="hash-1")
        with patch.object(ui, "get_redis_conn", return_value=MagicMock()), \
             patch.object(ui, "ChatHistoryService") as mock_service_cls:
            mock_instance = MagicMock()
            mock_service_cls.return_value = mock_instance

            ui.clear_chat_history(request)

            mock_instance.clear_history.assert_called_once_with("hash-1")

    def test_no_request_is_noop(self):
        with patch.object(ui, "get_redis_conn", return_value=MagicMock()), \
             patch.object(ui, "ChatHistoryService") as mock_service_cls:
            mock_instance = MagicMock()
            mock_service_cls.return_value = mock_instance

            ui.clear_chat_history(None)

            mock_instance.clear_history.assert_called_once_with(None)
