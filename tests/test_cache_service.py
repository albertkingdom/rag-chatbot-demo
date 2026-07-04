"""
Unit tests for the Prompt Cache Service.
"""
import pytest
import json
import time
from unittest.mock import MagicMock, patch
from src.cache_service import PromptCacheService


@pytest.fixture
def mock_redis():
    """Fixture to create a mock Redis connection."""
    redis_mock = MagicMock()
    redis_mock.smembers.return_value = set()
    redis_mock.get.return_value = None
    return redis_mock


@pytest.fixture
def cache_service(mock_redis):
    """Fixture to create a PromptCacheService instance with mock Redis."""
    return PromptCacheService(mock_redis)


class TestCacheSimilarity:
    """Tests for similarity calculation."""
    
    def test_identical_embeddings(self, cache_service):
        """Test that identical embeddings have similarity of 1.0."""
        embedding = [0.1, 0.2, 0.3, 0.4, 0.5]
        similarity = cache_service.calculate_similarity(embedding, embedding)
        assert abs(similarity - 1.0) < 0.0001
    
    def test_orthogonal_embeddings(self, cache_service):
        """Test that orthogonal embeddings have similarity of 0.0."""
        embedding1 = [1.0, 0.0, 0.0]
        embedding2 = [0.0, 1.0, 0.0]
        similarity = cache_service.calculate_similarity(embedding1, embedding2)
        assert abs(similarity - 0.0) < 0.0001
    
    def test_similar_embeddings(self, cache_service):
        """Test that similar embeddings have high similarity."""
        embedding1 = [0.9, 0.1, 0.0]
        embedding2 = [0.85, 0.15, 0.0]
        similarity = cache_service.calculate_similarity(embedding1, embedding2)
        assert similarity > 0.9
    
    def test_zero_vector_handling(self, cache_service):
        """Test handling of zero vectors."""
        embedding1 = [0.0, 0.0, 0.0]
        embedding2 = [1.0, 2.0, 3.0]
        similarity = cache_service.calculate_similarity(embedding1, embedding2)
        assert similarity == 0.0


class TestCacheRetrieval:
    """Tests for cache retrieval operations."""
    
    def test_empty_cache_returns_none(self, cache_service, mock_redis):
        """Test that querying an empty cache returns None."""
        mock_redis.smembers.return_value = set()
        embedding = [0.1, 0.2, 0.3]
        result = cache_service.get_cached_response(embedding)
        assert result is None
    
    def test_cache_hit_with_high_similarity(self, cache_service, mock_redis):
        """Test successful cache hit with similar question."""
        # Setup mock cached data
        test_embedding = [0.9, 0.1, 0.0]
        cached_data = {
            "question": "What is carbon management?",
            "answer": "Carbon management is...",
            "embedding": [0.85, 0.15, 0.0],  # Very similar
            "timestamp": int(time.time()),
            "hit_count": 2
        }
        
        cache_key = "prompt_cache:12345"
        mock_redis.smembers.return_value = {cache_key.encode()}
        pipe_mock = MagicMock()
        pipe_mock.execute.return_value = [json.dumps(cached_data)]
        mock_redis.pipeline.return_value = pipe_mock
        mock_redis.get.return_value = json.dumps(cached_data)  # for _increment_hit_count
        mock_redis.ttl.return_value = 3600

        result = cache_service.get_cached_response(test_embedding, threshold=0.85)

        assert result is not None
        assert result["question"] == "What is carbon management?"
        assert result["answer"] == "Carbon management is..."
        assert result["similarity"] > 0.85
    
    def test_cache_miss_with_low_similarity(self, cache_service, mock_redis):
        """Test cache miss when similarity is below threshold."""
        test_embedding = [1.0, 0.0, 0.0]
        cached_data = {
            "question": "Different question",
            "answer": "Different answer",
            "embedding": [0.0, 1.0, 0.0],  # Orthogonal - very different
            "timestamp": int(time.time()),
            "hit_count": 0
        }
        
        cache_key = "prompt_cache:12345"
        mock_redis.smembers.return_value = {cache_key.encode()}
        mock_redis.get.return_value = json.dumps(cached_data)
        
        result = cache_service.get_cached_response(test_embedding, threshold=0.85)
        
        assert result is None


class TestCacheStorage:
    """Tests for cache storage operations."""
    
    def test_set_cached_response(self, cache_service, mock_redis):
        """Test storing a new cached response."""
        embedding = [0.1, 0.2, 0.3, 0.4, 0.5]
        question = "What is the system?"
        answer = "The system is a carbon management tool."
        
        result = cache_service.set_cached_response(embedding, question, answer, ttl=3600)
        
        assert result is True
        mock_redis.setex.assert_called_once()
        mock_redis.sadd.assert_called_once()
        
        # Verify the data structure
        call_args = mock_redis.setex.call_args
        assert call_args[0][1] == 3600  # TTL
        stored_data = json.loads(call_args[0][2])
        assert stored_data["question"] == question
        assert stored_data["answer"] == answer
        assert stored_data["embedding"] == embedding
        assert stored_data["hit_count"] == 0
    
    def test_set_cached_response_with_error(self, cache_service, mock_redis):
        """Test error handling when storing fails."""
        mock_redis.setex.side_effect = Exception("Redis error")
        
        embedding = [0.1, 0.2, 0.3]
        result = cache_service.set_cached_response(embedding, "Question", "Answer")
        
        assert result is False


class TestCacheManagement:
    """Tests for cache management operations."""
    
    def test_clear_cache(self, cache_service, mock_redis):
        """Test clearing all cache entries."""
        cache_keys = {
            b"prompt_cache:123",
            b"prompt_cache:456",
            b"prompt_cache:789"
        }
        mock_redis.smembers.return_value = cache_keys
        
        count = cache_service.clear_cache()
        
        assert count == 3
        assert mock_redis.delete.call_count == 4  # 3 entries + 1 index
    
    def test_get_cache_stats(self, cache_service, mock_redis):
        """Test retrieving cache statistics."""
        cached_data1 = {
            "question": "Q1",
            "answer": "A1",
            "embedding": [0.1, 0.2],
            "timestamp": int(time.time()),
            "hit_count": 5
        }
        cached_data2 = {
            "question": "Q2",
            "answer": "A2",
            "embedding": [0.3, 0.4],
            "timestamp": int(time.time()),
            "hit_count": 3
        }
        
        cache_keys = {b"prompt_cache:123", b"prompt_cache:456"}
        mock_redis.smembers.return_value = cache_keys
        mock_redis.get.side_effect = [
            json.dumps(cached_data1),
            json.dumps(cached_data2)
        ]
        
        stats = cache_service.get_cache_stats()
        
        assert stats["total_entries"] == 2
        assert stats["total_hits"] == 8
        assert stats["avg_hits_per_entry"] == 4.0


class TestCacheKeyStability:
    """Tests for cache key stability and collision resistance."""

    def _compute_key(self, cache_service, embedding):
        """Helper: invoke set_cached_response and capture the key passed to setex."""
        cache_service.redis.get.return_value = None
        cache_service.set_cached_response(embedding, "q", "a", ttl=3600)
        call_args = cache_service.redis.setex.call_args
        return call_args[0][0]

    def test_cache_key_no_collision_on_partial_match(self, cache_service):
        """Same first 10 dims but different later dims must produce different keys."""
        import hashlib, struct, random
        random.seed(42)
        shared_prefix = [round(random.uniform(0, 1), 4) for _ in range(10)]

        keys = set()
        for _ in range(1000):
            suffix = [round(random.uniform(0, 1), 4) for _ in range(1526)]
            embedding = shared_prefix + suffix
            raw = struct.pack(f"{len(embedding)}d", *embedding)
            key = hashlib.sha256(raw).hexdigest()[:16]
            keys.add(key)

        assert len(keys) == 1000, f"Expected 1000 unique keys, got {len(keys)}"

    def test_cache_key_stability_same_embedding(self, cache_service):
        """Same embedding produces identical key every time."""
        import hashlib, struct
        embedding = [0.1 * i for i in range(1536)]
        raw = struct.pack(f"{len(embedding)}d", *embedding)
        expected = hashlib.sha256(raw).hexdigest()[:16]

        for _ in range(10):
            computed = hashlib.sha256(raw).hexdigest()[:16]
            assert computed == expected


class TestCacheDeduplication:
    """Tests for cache entry deduplication / hit_count preservation."""

    def test_duplicate_cache_preserves_hit_count(self, cache_service, mock_redis):
        """Caching the same question twice must keep the existing hit_count."""
        embedding = [0.1, 0.2, 0.3, 0.4, 0.5]
        existing_data = {
            "question": "Same question",
            "answer": "Old answer",
            "embedding": embedding,
            "timestamp": 1000,
            "hit_count": 5,
        }
        mock_redis.get.return_value = json.dumps(existing_data)

        result = cache_service.set_cached_response(embedding, "Same question", "New answer", ttl=3600)

        assert result is True
        call_args = mock_redis.setex.call_args
        stored = json.loads(call_args[0][2])
        assert stored["hit_count"] == 5, "hit_count must be preserved from existing entry"


class TestEdgeCases:
    """Tests for edge cases and error handling."""
    
    def test_expired_cache_entries_removed_from_index(self, cache_service, mock_redis):
        """Test that expired entries are removed from the index during lookup."""
        cache_key = b"prompt_cache:expired"
        mock_redis.smembers.return_value = {cache_key}
        pipe_mock = MagicMock()
        pipe_mock.execute.return_value = [None]  # Simulate expired key
        mock_redis.pipeline.return_value = pipe_mock

        embedding = [0.1, 0.2, 0.3]
        result = cache_service.get_cached_response(embedding)

        assert result is None
        mock_redis.srem.assert_called_once_with(cache_service.CACHE_INDEX_KEY, cache_key)
    
    def test_cache_with_missing_embedding_field(self, cache_service, mock_redis):
        """Test handling of corrupted cache data without embedding field."""
        cached_data = {
            "question": "Q",
            "answer": "A",
            # Missing "embedding" field
            "timestamp": int(time.time()),
            "hit_count": 0
        }
        
        cache_key = "prompt_cache:123"
        mock_redis.smembers.return_value = {cache_key.encode()}
        mock_redis.get.return_value = json.dumps(cached_data)
        
        embedding = [0.1, 0.2, 0.3]
        result = cache_service.get_cached_response(embedding, threshold=0.85)
        
        # Should handle gracefully and return None
        assert result is None
