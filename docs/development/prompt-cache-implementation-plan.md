# Prompt Cache Mechanism Implementation Plan

## Goal

Implement a caching mechanism to reduce API calls when users ask similar questions. The cache will store previous question-answer pairs and return cached responses when a semantically similar question is detected, significantly reducing costs and improving response time.

## User Review Required

> [!IMPORTANT]
> **Cache Similarity Threshold**: The proposed default similarity threshold is **0.85** (cosine similarity). Questions with similarity ≥ 0.85 will return cached results. This value may need tuning based on real-world usage.

> [!IMPORTANT]
> **Cache Expiration Policy**: Proposed TTL (Time-To-Live) is **24 hours**. This ensures the cache stays relatively fresh while still providing cost savings. Consider if this should be configurable.

> [!WARNING]
> **Embedding API Calls**: Even with caching, we still need to call the OpenAI Embeddings API to generate embeddings for incoming questions to compare similarity. However, we can skip the Pinecone retrieval and Gemini chat generation if we get a cache hit.

---

## Proposed Changes

### Core Components

#### [NEW] [cache_service.py](file:///Users/yklin/Documents/cedars-digital-assignment/src/cache_service.py)

A new module to handle all cache-related operations:

**Key Features:**
- **Redis-based storage**: Leverage existing Redis infrastructure
- **Semantic similarity matching**: Use cosine similarity on question embeddings
- **Configurable parameters**: TTL, similarity threshold, max cache size
- **Cache key structure**: `prompt_cache:{embedding_hash}` → stores Q&A pairs with metadata

**Main Functions:**
```python
class PromptCacheService:
    - get_cached_response(question_embedding, threshold=0.85)
      → Returns cached answer if similar question found
    
    - set_cached_response(question_embedding, question_text, answer)
      → Stores Q&A pair with TTL
    
    - calculate_similarity(embedding1, embedding2)
      → Computes cosine similarity between embeddings
    
    - find_similar_cached_questions(question_embedding, threshold)
      → Searches for cached questions above similarity threshold
```

**Data Structure:**
```json
{
  "question": "原始問題文本",
  "answer": "快取的答案",
  "embedding": [0.1, 0.2, ...],  // 1536-dim array
  "timestamp": 1735275600,
  "hit_count": 3
}
```

---

#### [MODIFY] [config.py](file:///Users/yklin/Documents/cedars-digital-assignment/src/config.py)

Add cache-related configuration constants:

```python
# Cache settings
CACHE_ENABLED = True
CACHE_TTL_SECONDS = 86400  # 24 hours
CACHE_SIMILARITY_THRESHOLD = 0.85
CACHE_MAX_SEARCH_RESULTS = 5  # Max number of cache entries to check
```

---

#### [MODIFY] [app.py](file:///Users/yklin/Documents/cedars-digital-assignment/src/app.py)

Integrate cache into the `chat_stream` function:

**Changes to `chat_stream`:**
1. Generate embedding for incoming question
2. Check cache for similar questions using `PromptCacheService`
3. If cache hit (similarity ≥ threshold):
   - Return cached answer immediately
   - Update cache hit metrics
4. If cache miss:
   - Proceed with normal RAG pipeline
   - Store the generated response in cache

**Pseudo-code:**
```python
async def chat_stream(message: str, history: list):
    # Initialize cache service
    cache_service = PromptCacheService(conn)
    
    # Generate embedding for the question
    embeddings = OpenAIEmbeddings()
    question_embedding = embeddings.embed_query(message)
    
    # Try to get cached response
    cached_response = cache_service.get_cached_response(
        question_embedding, 
        threshold=CACHE_SIMILARITY_THRESHOLD
    )
    
    if cached_response:
        # Cache hit - return cached answer
        yield cached_response["answer"]
        return
    
    # Cache miss - proceed with RAG pipeline
    # ... existing RAG logic ...
    
    # Store response in cache
    cache_service.set_cached_response(
        question_embedding,
        message,
        full_response
    )
```

---

#### [MODIFY] [requirements.txt](file:///Users/yklin/Documents/cedars-digital-assignment/src/requirements.txt)

Add numpy for vector operations:

```
numpy
```

---

### Testing

#### [NEW] [test_cache_service.py](file:///Users/yklin/Documents/cedars-digital-assignment/tests/test_cache_service.py)

Unit tests for cache functionality:
- Test similarity calculation accuracy
- Test cache storage and retrieval
- Test TTL expiration
- Test cache hit/miss scenarios
- Test edge cases (empty cache, identical questions, etc.)

---

## Verification Plan

### Automated Tests

```bash
# Run cache service tests
pytest tests/test_cache_service.py -v

# Run integration tests
pytest tests/test_rag_stream.py -v
```

### Manual Verification

1. **Test Cache Hit**:
   - Ask: "什麼是碳管理系統？"
   - Ask similar: "碳管理系統是什麼？"
   - Verify second response is instant (from cache)

2. **Test Cache Miss**:
   - Ask completely different question
   - Verify it goes through normal RAG pipeline

3. **Monitor Metrics**:
   - Check Redis for cache entries
   - Monitor API call reduction in logs

4. **Performance Comparison**:
   - Measure response time with/without cache
   - Track API cost savings

### Success Metrics

- **Cache hit rate**: Target ≥ 30% for typical user sessions
- **Response time improvement**: Cache hits should be < 500ms
- **API cost reduction**: Reduce Gemini API calls by 30-40%
- **Accuracy**: Cached responses should be relevant (similarity threshold tuning)

---

## Implementation Notes

### Why This Approach?

1. **Leverages Existing Infrastructure**: Uses the already-running Redis instance
2. **Semantic Matching**: More sophisticated than simple string matching
3. **Configurable**: Easy to tune threshold and TTL based on usage patterns
4. **Minimal Performance Impact**: Similarity search is fast with limited cache size

### Trade-offs

| Aspect | Benefit | Cost |
|--------|---------|------|
| **Embedding Generation** | More accurate matching | Still calls OpenAI API once per question |
| **Redis Storage** | Fast, ephemeral storage | Need to monitor memory usage |
| **Similarity Threshold** | Flexibility in matching | Requires tuning for optimal balance |

### Future Enhancements

- Add cache warming for common questions
- Implement LRU eviction for cache size limits
- Add admin UI to view/manage cache entries
- Track and display cache hit rate metrics in UI
