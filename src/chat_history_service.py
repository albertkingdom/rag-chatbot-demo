"""
Short-term chat history service for multi-turn conversation context.
Uses Redis to store recent conversation turns per session, keyed by a
persistent session identifier (see design.md for history_key derivation).
"""
import json
import logging
from typing import List, Optional

import redis

from .config import CHAT_HISTORY_MAX_TURNS, CHAT_HISTORY_TTL_SECONDS

logger = logging.getLogger("chat_history_service")


class ChatHistoryService:
    """Service for storing and retrieving short-term, session-scoped chat history."""

    KEY_PREFIX = "chat_history:"

    def __init__(self, redis_connection: redis.Redis):
        self.redis = redis_connection

    def get_history(self, history_key: Optional[str]) -> List[dict]:
        """
        Retrieve the stored conversation history for a session.

        Args:
            history_key: Session identifier. None returns [].

        Returns:
            List of {"role": "user"|"assistant", "content": str} dicts,
            compatible with format_history/rewrite_query. Returns [] if
            there is no stored history, the key expired, or Redis fails.
        """
        if not history_key:
            return []

        try:
            raw = self.redis.get(f"{self.KEY_PREFIX}{history_key}")
            if not raw:
                return []
            return json.loads(raw)
        except Exception as e:
            logger.warning("[ChatHistoryService] get_history failed: %s", e)
            return []

    def append_turn(
        self,
        history_key: Optional[str],
        user_message: str,
        assistant_message: str,
    ) -> None:
        """
        Append a (user, assistant) turn to the session's stored history,
        trimming to the configured max turns and refreshing the TTL.

        Args:
            history_key: Session identifier. None is a no-op.
            user_message: The user's message text.
            assistant_message: The assistant's response text.
        """
        if not history_key:
            return

        try:
            history = self.get_history(history_key)
            history.append({"role": "user", "content": user_message})
            history.append({"role": "assistant", "content": assistant_message})

            max_messages = CHAT_HISTORY_MAX_TURNS * 2
            if len(history) > max_messages:
                history = history[-max_messages:]

            self.redis.setex(
                f"{self.KEY_PREFIX}{history_key}",
                CHAT_HISTORY_TTL_SECONDS,
                json.dumps(history, ensure_ascii=False),
            )
        except Exception as e:
            logger.warning("[ChatHistoryService] append_turn failed: %s", e)

    def clear_history(self, history_key: Optional[str]) -> None:
        """
        Delete the stored conversation history for a session.

        Args:
            history_key: Session identifier. None is a no-op.
        """
        if not history_key:
            return

        try:
            self.redis.delete(f"{self.KEY_PREFIX}{history_key}")
        except Exception as e:
            logger.warning("[ChatHistoryService] clear_history failed: %s", e)
