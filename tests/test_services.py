"""Tests for src/services.py — thread-safe singleton providers.

Hermetic: external services (OpenAI/Pinecone/HF) are mocked; no network.
"""
import os
import threading
import time
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# get_embeddings / get_llm: missing-key errors
# ---------------------------------------------------------------------------


class TestProviderErrors:
    def test_get_embeddings_raises_without_openai_key(self):
        from src import services

        # Reset the LazySingleton so the build runs again.
        services._embeddings._value = None
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError, match="OPENAI_API_KEY"):
                services.get_embeddings()
        services._embeddings._value = None  # reset after test

    def test_get_llm_raises_without_openrouter_key(self):
        from src import services

        services._llm._value = None
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
                services.get_llm()
        services._llm._value = None


# ---------------------------------------------------------------------------
# Thread safety: concurrent first call initializes exactly once
# ---------------------------------------------------------------------------


class TestThreadSafety:
    def test_concurrent_first_call_initializes_once(self):
        """Two threads racing on get_reranker_model() get the same instance,
        and the (slow) construction runs exactly once."""
        from src import services

        # Reset the LazySingleton value.
        services._reranker._value = None

        call_count = 0
        count_lock = threading.Lock()
        sentinel_instance = MagicMock(name="fake-reranker")

        def slow_build():
            nonlocal call_count
            with count_lock:
                call_count += 1
            time.sleep(0.1)  # simulate slow model load
            return sentinel_instance

        # Swap the build callable on the existing LazySingleton.
        services._reranker._build = slow_build

        results = []
        results_lock = threading.Lock()

        def worker():
            inst = services.get_reranker_model()
            with results_lock:
                results.append(inst)

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert call_count == 1, "construction must happen exactly once"
        assert len(results) == 2
        assert results[0] is results[1] is sentinel_instance

        # reset for other tests
        services._reranker._value = None

    def test_subsequent_call_returns_cached_instance(self):
        """Second call returns the same instance without re-entering lock."""
        from src import services

        services._llm._value = None
        first = second = None
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "k"}, clear=False):
            with patch("src.services.ChatOpenAI") as mock_chat:
                mock_chat.return_value = MagicMock(name="llm")
                first = services.get_llm()
                second = services.get_llm()
        assert first is second
        assert mock_chat.call_count == 1, "constructor called only once"
        services._llm._value = None


# ---------------------------------------------------------------------------
# get_redis_conn — single shared instance, lazy, no import-time connection
# ---------------------------------------------------------------------------


class TestRedisConn:
    def test_get_redis_conn_returns_same_instance(self):
        from src import services

        services._redis_conn._value = None
        fake = MagicMock(name="redis-conn")
        with patch("src.services.redis.from_url", return_value=fake):
            a = services.get_redis_conn()
            b = services.get_redis_conn()
        assert a is b is fake
        services._redis_conn._value = None

    def test_importing_ui_does_not_call_redis_from_url(self):
        """import src.ui must not establish a Redis connection (no side effect)."""
        import importlib

        with patch("src.services.redis.from_url") as spy:
            importlib.reload(importlib.import_module("src.ui"))
        spy.assert_not_called()

    def test_get_redis_conn_concurrent_single_init(self):
        from src import services

        services._redis_conn._value = None
        call_count = 0
        count_lock = threading.Lock()
        fake = MagicMock(name="redis-conn")

        def slow_from_url(*a, **k):
            nonlocal call_count
            with count_lock:
                call_count += 1
            time.sleep(0.05)
            return fake

        results = []
        results_lock = threading.Lock()

        def worker():
            with patch("src.services.redis.from_url", side_effect=slow_from_url):
                inst = services.get_redis_conn()
            with results_lock:
                results.append(inst)

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        assert call_count == 1
        assert results[0] is results[1] is fake
        services._redis_conn._value = None
