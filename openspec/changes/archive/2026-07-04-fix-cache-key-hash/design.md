## Context

`src/cache_service.py` 使用 Python 的內建 `hash()` 函式生成 cache key，存在關鍵缺陷：

1. **部分 embedding 碰撞**: 只取前 10 維 (1536 維中)，導致不同語意的問題產生相同 key
2. **靜默數據損失**: `redis.setex()` 無聲覆寫舊值，`hit_count` 統計歸零，無錯誤訊息
3. **缺乏存在性檢查**: 重複 cache 同一問題時無保護機制

## Goals / Non-Goals

**Goals:**

- Replace `hash(tuple(...[:10]))` with `hashlib.sha256(struct.pack(...))` using full 1536-dim embedding
- Eliminate cache key collisions in realistic workloads (1M+ entries)
- Preserve hit statistics when same question is cached multiple times
- Maintain backward compatibility: existing cache entries remain valid (separate TTL lifecycle)

**Non-Goals:**

- Migrate existing cached entries (old keys auto-expire by TTL)
- Change semantic similarity logic in `get_cached_response()`
- Optimize cache retrieval latency beyond existing scan-all-entries pattern
- Add Prometheus metrics or logging enhancements

## Decisions

### Decision: Use SHA256 for cache key generation

**Choice**: `hashlib.sha256(struct.pack(...))` over `hash()`

**Rationale**:
- `hash()` output space = 2^63 (63 bits). At 1M entries, collision probability ≈ 5.4×10^-8
- SHA256 output space = 2^256. Collision probability at 1M entries < 10^-60 (effectively impossible)
- SHA256 is deterministic across processes, platforms, Python versions
- Performance cost acceptable: 4.4 μs per call vs. Redis network I/O (ms range)

**Alternatives considered**:
- `hashlib.md5()`: Faster but cryptographically broken; not suitable for security-sensitive hash tables
- `hashlib.xxhash()`: Fast and good distribution, but requires external dependency
- Stick with `hash()` but increase dims from 10→50: Still vulnerable to collision, doesn't scale

### Decision: Use full embedding (1536 dims) instead of truncated (10 dims)

**Choice**: `struct.pack(f"{1536}d", *question_embedding)` (full)

**Rationale**:
- Truncation was a micro-optimization that introduced silent data loss
- Full embedding captures complete semantic information
- No performance regression: hashing full embedding still faster than Redis network call

### Decision: Implement existence check in set_cached_response()

**Choice**: Check `redis.exists(cache_key)` before `setex()`, preserve `hit_count` if exists

**Rationale**:
- Prevents silent reset of hit_count statistics
- Ensures cache stats reflect actual usage patterns
- Minimal performance cost (one additional Redis call per write)

## Implementation Contract

### Behavior Change

**Before Fix:**
```python
# User asks Q1 (embedding E1) → cache_key = K1 = hash(E1[:10])
# User asks Q2 (embedding E2) → if E2[:10] == E1[:10]:
#   - cache_key = K1 (collision!)
#   - redis.setex(K1, ..., data_Q2) silently overwrites data_Q1
#   - Q1 permanently evicted, hit_count lost, no error
```

**After Fix:**
```python
# User asks Q1 (embedding E1) → cache_key = K1 = sha256(E1).hex()[:16]
# User asks Q2 (embedding E2) → if E2[:10] == E1[:10]:
#   - cache_key = K2 (collision avoided!)
#   - Both Q1 and Q2 remain in cache with separate entries
#   - Each preserves its own hit_count

# Repeated cache of same Q:
# Before: set_cached_response(Q) → hit_count reset to 0
# After:  set_cached_response(Q) → hit_count preserved (incremented if already exists)
```

### Interface Changes

**Modified function**: `set_cached_response(question_embedding, question_text, answer, ttl)`

- **Input**: Same as before (no signature change)
- **Cache key algorithm**: 
  ```python
  raw_bytes = struct.pack(f"{len(question_embedding)}d", *question_embedding)
  embedding_hash = hashlib.sha256(raw_bytes).hexdigest()[:16]
  cache_key = f"{self.CACHE_PREFIX}{embedding_hash}"
  ```
- **Deduplication logic**:
  ```python
  if self.redis.exists(cache_key):
      # Read existing entry, preserve hit_count, update timestamp/answer
      old_data = json.loads(self.redis.get(cache_key))
      cache_data["hit_count"] = old_data.get("hit_count", 0)  # preserve
  self.redis.setex(cache_key, ttl, json.dumps(cache_data))
  ```
- **Return**: Boolean (unchanged)
- **Error behavior**: Same as before (silently return False on Redis exception)

### Acceptance Criteria

1. **Unit test**: Collision test with 1000 pairs of embeddings where first 10 dims match but full embedding differs → verify all generate unique keys
2. **Unit test**: Stability test with 10 embeddings hashed 10 times each across different processes → verify identical key output
3. **Unit test**: Deduplication test: cache Q twice, verify second cache preserves hit_count
4. **Integration test**: Smoke test existing tests still pass
5. **Performance**: Key generation latency < 10 ms (dominated by Redis round trip)

### Scope Boundaries

**In scope:**
- Modify cache key generation in `set_cached_response()`
- Add existence check before `setex()`
- Update tests in `test_cache_service.py`

**Out of scope:**
- Change TTL/expiration logic
- Modify semantic similarity search in `get_cached_response()`
- Add new monitoring/metrics
- Migrate existing cached data (will auto-expire)

## Risks / Trade-offs

**[Risk] Backward incompatibility**: Existing cache entries use old key format, will not match new keys
- **Mitigation**: Acceptable—cache is ephemeral (24h TTL). Old entries auto-expire; no data loss

**[Risk] SHA256 latency**: Hashing 1536 floats takes ~4.4 μs per call
- **Mitigation**: Negligible compared to Redis I/O (ms scale). Measured trade-off: correctness > micro-optimizations

**[Risk] struct.pack() type assumptions**: Assumes embedding is list of floats, will raise TypeError if not
- **Mitigation**: Same as before—input contract already assumes list[float]. No regression

**[Risk] Existence check adds one Redis call per write**: Doubles write overhead (2 calls instead of 1)
- **Mitigation**: Still sub-millisecond. Correctness and data integrity justify the cost

## Migration Plan

1. Deploy updated code to production (backward compatible—old cache entries simply expire)
2. New cache entries use SHA256 keys immediately
3. No data migration needed (old entries auto-expire within 24h)
4. Monitor cache hit rates post-deployment; expect temporary dip as old entries expire, then normalize

## Open Questions

- Should we add a migration mode that transparently re-keys old entries on-the-fly? (likely overkill for ephemeral cache)
- Should we make embedding dimension configurable (currently hardcoded 1536)? (defer to future optimization)
