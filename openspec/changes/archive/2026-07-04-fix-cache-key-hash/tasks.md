## 1. 實現 Cache key stability 和 Use SHA256 決策（修改 cache key 生成邏輯）

- [x] 1.1 導入必要模組：在 `src/cache_service.py` 頂部加入 `import hashlib` 和 `import struct`，確保 SHA256 和 binary packing 可用。行為：PromptCacheService 能夠使用 hashlib 和 struct。驗證方式：運行 `python3 -c "from src.cache_service import PromptCacheService; import inspect; source = inspect.getsource(PromptCacheService); assert 'hashlib' in source and 'struct' in source"` 確認 imports 存在

- [x] 1.2 修改 set_cached_response() 實現 Cache key stability requirement：在 `set_cached_response()` 方法中，修改 embedding_hash 生成邏輯，實現「Use SHA256 for cache key generation」和「Use full embedding (1536 dims) instead of truncated (10 dims)」設計決策。將第 147 行的 `abs(hash(tuple(question_embedding[:10])))` 改為 `hashlib.sha256(struct.pack(f"{len(question_embedding)}d", *question_embedding)).hexdigest()[:16]`。行為：cache key 由完整 embedding 的 SHA256 雜湊生成，實現「Same embedding produces same cache key」和「Different embeddings produce different cache keys」和「No collision on partial embedding data」scenarios。驗證方式：運行 `pytest tests/test_cache_service.py -k "stability or collision" -v`

## 2. 實現 Cache entry deduplication（存在性檢查）

- [x] 2.1 在 set_cached_response() 中加入重複 cache 檢查邏輯，實現「Implement existence check in set_cached_response()」設計決策。在 `redis.setex()` 之前加入：檢查 cache_key 是否存在；如果存在，讀取舊 cache_data，將 `hit_count` 從舊資料複製到新資料。行為：重複 cache 同一問題時，hit_count 統計數據保持，不被重置為 0；Redis 無聲覆寫的風險被消除。驗證方式：運行 `pytest tests/test_cache_service.py::test_duplicate_cache_preserves_hit_count -v`，驗證 cache 同一問題兩次後，hit_count 保持累增

## 3. 驗證 Cache key stability 和 Different embeddings 的碰撞修復

- [x] 3.1 新增碰撞測試：在 `tests/test_cache_service.py` 中新增測試 `test_cache_key_no_collision_on_partial_match`。測試「Same embedding produces same cache key」和「Different embeddings produce different cache keys」scenarios：構造 1000 對不同 embedding（前 10 維相同但後續不同），驗證所有 key 互不相同；對同一 embedding 計算 10 次 key，驗證完全相同。行為：修復後，碰撞消失；cache key 穩定。驗證方式：`pytest tests/test_cache_service.py::test_cache_key_no_collision_on_partial_match -v` 通過

## 4. 驗證現有測試相容性

- [x] 4.1 驗證現有 cache 測試套件：執行 `pytest tests/test_cache_service.py -v`，確認所有現有測試通過。行為：修改不會破壞既有功能契約。驗證方式：pytest 返回全綠，無 FAILED

## 5. 端到端整合驗證

- [x] 5.1 手動驗證完整 cache 流程：執行「cache Q1 → 查詢 Q1（應 hit）→ cache Q2（前 10 維與 Q1 相同）→ 查詢 Q1（應仍 hit Q1）→ 查詢 Q2（應 hit Q2）」。行為：不同 embedding 互不污染；查詢返回正確答案。驗證方式：手動測試或編寫集成測試驗證查詢返回值正確無誤
