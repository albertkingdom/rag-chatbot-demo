"""
Prompt Cache Service for caching similar question-answer pairs.
Uses Redis for storage and cosine similarity for semantic matching.
"""
import hashlib
import json
import struct
import time
import numpy as np
from typing import Optional, Dict, Any, List, Callable
import redis
from . import config as app_config
from .config import CACHE_TTL_SECONDS, CACHE_SIMILARITY_THRESHOLD


class PromptCacheService:
    """Service for caching and retrieving similar question-answer pairs."""
    
    # Unscoped layout. An instance with a namespace overrides both on itself:
    # prompt_cache:<namespace>:<hash> and prompt_cache:<namespace>:index:zset.
    CACHE_PREFIX = "prompt_cache:"
    # Sorted Set of data keys, scored by write time (seconds). Capped at
    # CACHE_MAX_ENTRIES; the oldest entries are evicted on write.
    CACHE_INDEX_KEY = "prompt_cache:index:zset"
    # Set index used by earlier releases. Never read or written by lookups and
    # writes (a Set under the new key would make ZADD fail with WRONGTYPE);
    # only clear_cache() removes it.
    LEGACY_INDEX_KEY = "prompt_cache:index"
    DELETE_BATCH_SIZE = 500

    def __init__(
        self,
        redis_connection: redis.Redis,
        clock: Optional[Callable[[], float]] = None,
        max_entries: Optional[int] = None,
        namespace: Optional[str] = None,
    ):
        """
        Initialize the cache service.

        Args:
            redis_connection: Active Redis connection instance
            clock: Returns the current time in seconds; scores index entries.
                Defaults to time.time. Injected by tests for a deterministic order.
            max_entries: Index size cap. Defaults to the CACHE_MAX_ENTRIES setting,
                read at call time. Must be an integer >= 1.
            namespace: Scopes keys and the index so caches built from different
                knowledge bases never answer for each other. Defaults to the
                PINECONE_INDEX_NAME setting; an empty string keeps the unscoped
                layout.
        """
        if max_entries is not None and (
            isinstance(max_entries, bool)
            or not isinstance(max_entries, int)
            or max_entries < 1
        ):
            raise ValueError(f"max_entries must be an integer >= 1, got {max_entries!r}")
        self.redis = redis_connection
        self._clock = clock if clock is not None else time.time
        self._max_entries = max_entries
        if namespace is None:
            namespace = app_config.PINECONE_INDEX_NAME
        if namespace:
            self.CACHE_PREFIX = f"prompt_cache:{namespace}:"
            self.CACHE_INDEX_KEY = f"{self.CACHE_PREFIX}index:zset"

    def _limit(self) -> int:
        """Current index cap; the setting is read per call, not bound at import."""
        if self._max_entries is not None:
            return self._max_entries
        return app_config.CACHE_MAX_ENTRIES

    @staticmethod
    def _decode_entry(raw: Any) -> Optional[Dict[str, Any]]:
        """
        Parse a stored value into an entry that can be served, or None.

        Servable means a JSON object with a non-empty string answer and a list
        of string sources (a missing sources field is not defaulted to []).
        The embedding is checked separately: a bad one only makes the entry
        unmatchable, while an entry that fails here is dropped from the index.
        """
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        answer = data.get("answer")
        if not isinstance(answer, str) or not answer:
            return None
        sources = data.get("sources")
        if not isinstance(sources, list) or not all(isinstance(s, str) for s in sources):
            return None
        return data

    @staticmethod
    def _existing_hit_count(raw: Any) -> int:
        """hit_count of an already stored value; 0 if it cannot be read."""
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return 0
        if not isinstance(data, dict):
            return 0
        hit_count = data.get("hit_count", 0)
        if isinstance(hit_count, bool) or not isinstance(hit_count, int) or hit_count < 0:
            return 0
        return hit_count
    
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
            Dict contains: {"question", "answer", "sources", "similarity", "hit_count"}
        """
        try:
            # Newest first, capped at the same limit the index is trimmed to, so
            # this is the whole index. One round-trip reads every value.
            members = self.redis.zrevrange(self.CACHE_INDEX_KEY, 0, self._limit() - 1)
            if not members:
                return None

            cached_values = self.redis.mget(members)

            best_match = None
            best_similarity = threshold
            best_match_key = None
            stale_keys = []

            for cache_key, cached_data_json in zip(members, cached_values):
                if cached_data_json is None:
                    # Data key expired; drop it from the index
                    stale_keys.append(cache_key)
                    continue

                cached_data = self._decode_entry(cached_data_json)
                if cached_data is None:
                    # Unusable for good (not an entry, or no answer/sources to serve)
                    stale_keys.append(cache_key)
                    continue

                cached_embedding = cached_data.get("embedding")
                if not isinstance(cached_embedding, list) or not cached_embedding:
                    continue

                try:
                    similarity = self.calculate_similarity(question_embedding, cached_embedding)
                except (TypeError, ValueError):
                    # Non-numeric values or a different dimension: skip this entry only
                    continue

                if similarity > best_similarity:
                    best_similarity = similarity
                    best_match = {
                        "question": cached_data.get("question"),
                        "answer": cached_data["answer"],
                        "sources": cached_data["sources"],
                        "similarity": similarity,
                        "hit_count": cached_data.get("hit_count", 0)
                    }
                    best_match_key = cache_key

            # Remove expired and unusable members in one command (never empty)
            if stale_keys:
                self.redis.zrem(self.CACHE_INDEX_KEY, *stale_keys)

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
        sources: List[str],
        ttl: int = CACHE_TTL_SECONDS
    ) -> bool:
        """
        Store a question-answer pair in the cache.

        Args:
            question_embedding: Embedding vector of the question
            question_text: Original question text
            answer: Generated answer text
            sources: Deduplicated list of source FAQ questions/doc_ids for this answer
            ttl: Time-to-live in seconds

        Returns:
            True if successfully cached, False otherwise
        """
        try:
            raw_bytes = struct.pack(f"{len(question_embedding)}d", *question_embedding)
            embedding_hash = hashlib.sha256(raw_bytes).hexdigest()[:16]
            cache_key = f"{self.CACHE_PREFIX}{embedding_hash}"

            cache_data = {
                "question": question_text,
                "answer": answer,
                "sources": sources,
                "embedding": question_embedding,
                "timestamp": int(time.time()),
                "hit_count": 0
            }

            # Preserve hit_count if entry already exists. An unreadable existing
            # value counts as 0 and is overwritten, so one corrupted value can
            # not keep a question out of the cache until its TTL runs out.
            existing = self.redis.get(cache_key)
            if existing:
                cache_data["hit_count"] = self._existing_hit_count(existing)

            # Store with TTL
            self.redis.setex(
                cache_key,
                ttl,
                json.dumps(cache_data, ensure_ascii=False)
            )
            
            self._register_and_evict(cache_key)
            
            return True
            
        except Exception as e:
            print(f"Error setting cache: {e}")
            return False
    
    def _register_and_evict(self, cache_key: str) -> None:
        """
        Register a data key in the index and evict everything but the newest N.

        Registering, selecting the overflow members (all but the newest N, i.e.
        ranks 0 to -(N+1); empty while the index holds N or fewer) and removing
        them run as one transaction, so the list of members read is exactly the
        list removed. Only then are their data keys deleted, so the deleted
        keys can never include an entry another writer registered meanwhile.
        A key that is already indexed just gets a newer score.
        """
        overflow_end = -(self._limit() + 1)
        with self.redis.pipeline(transaction=True) as pipe:
            pipe.zadd(self.CACHE_INDEX_KEY, {cache_key: self._clock()})
            pipe.zrange(self.CACHE_INDEX_KEY, 0, overflow_end)
            pipe.zremrangebyrank(self.CACHE_INDEX_KEY, 0, overflow_end)
            _, evicted, _ = pipe.execute()
        if evicted:
            self.redis.delete(*evicted)

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
        Clear this namespace's cached entries, plus those registered in the
        legacy Set index from earlier releases.

        Returns:
            Number of distinct data keys cleared (a key registered in both
            indexes counts once)

        Raises:
            Any Redis error, so a failure is not mistaken for an empty cache.
        """
        data_keys = set(self.redis.zrange(self.CACHE_INDEX_KEY, 0, -1))
        data_keys |= set(self.redis.smembers(self.LEGACY_INDEX_KEY))

        # DEL needs at least one key; batch so a large legacy index stays sane
        ordered = sorted(data_keys)
        for start in range(0, len(ordered), self.DELETE_BATCH_SIZE):
            self.redis.delete(*ordered[start:start + self.DELETE_BATCH_SIZE])

        self.redis.delete(self.CACHE_INDEX_KEY, self.LEGACY_INDEX_KEY)
        return len(data_keys)

    def get_cache_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics for the current (Sorted Set) index.

        Returns:
            Dictionary with cache statistics
        """
        try:
            members = self.redis.zrange(self.CACHE_INDEX_KEY, 0, -1)
            total_entries = len(members)
            total_hits = 0

            if members:
                for cached_data_json in self.redis.mget(members):
                    if cached_data_json is not None:
                        total_hits += self._existing_hit_count(cached_data_json)

            return {
                "total_entries": total_entries,
                "total_hits": total_hits,
                "avg_hits_per_entry": total_hits / total_entries if total_entries > 0 else 0
            }

        except Exception as e:
            print(f"Error getting cache stats: {e}")
            return {"error": str(e)}
