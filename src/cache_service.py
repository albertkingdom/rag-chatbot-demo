"""
Prompt Cache Service for caching similar question-answer pairs.
Uses Redis for storage and cosine similarity for semantic matching.
"""
import json
import time
import numpy as np
from typing import Optional, Dict, Any, List
import redis
from .config import CACHE_TTL_SECONDS, CACHE_SIMILARITY_THRESHOLD, CACHE_MAX_SEARCH_RESULTS


class PromptCacheService:
    """Service for caching and retrieving similar question-answer pairs."""
    
    CACHE_PREFIX = "prompt_cache:"
    CACHE_INDEX_KEY = "prompt_cache:index"  # Set of all cache keys
    
    def __init__(self, redis_connection: redis.Redis):
        """
        Initialize the cache service.
        
        Args:
            redis_connection: Active Redis connection instance
        """
        self.redis = redis_connection
    
    def calculate_similarity(self, embedding1: List[float], embedding2: List[float]) -> float:
        """
        Calculate cosine similarity between two embeddings.
        
        Args:
            embedding1: First embedding vector
            embedding2: Second embedding vector
            
        Returns:
            Cosine similarity score (0 to 1)
        """
        vec1 = np.array(embedding1)
        vec2 = np.array(embedding2)
        
        # Cosine similarity = dot product / (norm1 * norm2)
        dot_product = np.dot(vec1, vec2)
        norm1 = np.linalg.norm(vec1)
        norm2 = np.linalg.norm(vec2)
        
        if norm1 == 0 or norm2 == 0:
            return 0.0
        
        return float(dot_product / (norm1 * norm2))
    
    def get_cached_response(
        self, 
        question_embedding: List[float], 
        threshold: float = CACHE_SIMILARITY_THRESHOLD
    ) -> Optional[Dict[str, Any]]:
        """
        Search for a cached response with similar question embedding.
        
        Args:
            question_embedding: Embedding of the incoming question
            threshold: Minimum similarity score for a cache hit
            
        Returns:
            Cached response dict if found, None otherwise
            Dict contains: {"question", "answer", "similarity", "hit_count"}
        """
        try:
            # Get all cache keys from the index
            cache_keys = self.redis.smembers(self.CACHE_INDEX_KEY)
            
            if not cache_keys:
                return None
            
            best_match = None
            best_similarity = threshold
            
            # Search through cached entries for similar questions
            # Limit search to recent entries for performance
            for i, cache_key in enumerate(cache_keys):
                if i >= CACHE_MAX_SEARCH_RESULTS:
                    break
                
                cached_data_json = self.redis.get(cache_key)
                if not cached_data_json:
                    # Key expired, remove from index
                    self.redis.srem(self.CACHE_INDEX_KEY, cache_key)
                    continue
                
                cached_data = json.loads(cached_data_json)
                cached_embedding = cached_data.get("embedding")
                
                if not cached_embedding:
                    continue
                
                # Calculate similarity
                similarity = self.calculate_similarity(question_embedding, cached_embedding)
                
                if similarity > best_similarity:
                    best_similarity = similarity
                    best_match = {
                        "question": cached_data.get("question"),
                        "answer": cached_data.get("answer"),
                        "similarity": similarity,
                        "hit_count": cached_data.get("hit_count", 0)
                    }
                    best_match_key = cache_key
            
            if best_match:
                # Increment hit count
                self._increment_hit_count(best_match_key)
                return best_match
            
            return None
            
        except Exception as e:
            print(f"Error retrieving from cache: {e}")
            return None
    
    def set_cached_response(
        self, 
        question_embedding: List[float], 
        question_text: str, 
        answer: str,
        ttl: int = CACHE_TTL_SECONDS
    ) -> bool:
        """
        Store a question-answer pair in the cache.
        
        Args:
            question_embedding: Embedding vector of the question
            question_text: Original question text
            answer: Generated answer text
            ttl: Time-to-live in seconds
            
        Returns:
            True if successfully cached, False otherwise
        """
        try:
            # Create a hash of the embedding for the cache key
            embedding_hash = abs(hash(tuple(question_embedding[:10])))  # Use first 10 dims for hash
            cache_key = f"{self.CACHE_PREFIX}{embedding_hash}"
            
            cache_data = {
                "question": question_text,
                "answer": answer,
                "embedding": question_embedding,
                "timestamp": int(time.time()),
                "hit_count": 0
            }
            
            # Store with TTL
            self.redis.setex(
                cache_key,
                ttl,
                json.dumps(cache_data, ensure_ascii=False)
            )
            
            # Add to index
            self.redis.sadd(self.CACHE_INDEX_KEY, cache_key)
            
            return True
            
        except Exception as e:
            print(f"Error setting cache: {e}")
            return False
    
    def _increment_hit_count(self, cache_key: str) -> None:
        """
        Increment the hit count for a cache entry.
        
        Args:
            cache_key: Redis key for the cache entry
        """
        try:
            cached_data_json = self.redis.get(cache_key)
            if cached_data_json:
                cached_data = json.loads(cached_data_json)
                cached_data["hit_count"] = cached_data.get("hit_count", 0) + 1
                
                # Get remaining TTL
                ttl = self.redis.ttl(cache_key)
                if ttl > 0:
                    self.redis.setex(cache_key, ttl, json.dumps(cached_data, ensure_ascii=False))
        except Exception as e:
            print(f"Error incrementing hit count: {e}")
    
    def clear_cache(self) -> int:
        """
        Clear all cached entries.
        
        Returns:
            Number of entries cleared
        """
        try:
            cache_keys = self.redis.smembers(self.CACHE_INDEX_KEY)
            count = 0
            
            for cache_key in cache_keys:
                self.redis.delete(cache_key)
                count += 1
            
            self.redis.delete(self.CACHE_INDEX_KEY)
            return count
            
        except Exception as e:
            print(f"Error clearing cache: {e}")
            return 0
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.
        
        Returns:
            Dictionary with cache statistics
        """
        try:
            cache_keys = self.redis.smembers(self.CACHE_INDEX_KEY)
            total_entries = len(cache_keys)
            total_hits = 0
            
            for cache_key in cache_keys:
                cached_data_json = self.redis.get(cache_key)
                if cached_data_json:
                    cached_data = json.loads(cached_data_json)
                    total_hits += cached_data.get("hit_count", 0)
            
            return {
                "total_entries": total_entries,
                "total_hits": total_hits,
                "avg_hits_per_entry": total_hits / total_entries if total_entries > 0 else 0
            }
            
        except Exception as e:
            print(f"Error getting cache stats: {e}")
            return {"error": str(e)}
