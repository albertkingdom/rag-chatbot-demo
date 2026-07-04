## Why

`src/cache_service.py:147` 使用 `abs(hash(tuple(question_embedding[:10])))` 生成 cache key，導致嚴重的缺陷：
- 只取前 10 維，忽略後 1526 維，導致不同問題的 embedding 碰撞
- 碰撞導致靜默覆寫舊答案和 cache 污染——用戶查詢 Q1 卻得到 Q2 的答案
- 碰撞發生時無任何錯誤訊息，問題難以察覺

此 bug 已由 Claude Sonnet 驗證，在 1M 條目時碰撞概率為 5.4×10^-8。

## What Changes

1. **修改 `set_cached_response()`**：
   - 將 `embedding_hash` 從 `abs(hash(tuple(...[:10])))` 改為 `hashlib.sha256(struct.pack(...))` 
   - 使用完整 1536 維 embedding，而非只取前 10 維
   - 輸出 16 字元 hex，256-bit 雜湊空間，碰撞概率 < 10^-14

2. **加入 existence check**：
   - 避免重複 cache 同一問題時，`setex` 無聲覆寫舊值並重置 `hit_count`
   - 實現方式：檢查 key 是否存在，若存在則更新 hit_count 而非覆寫

3. **強化測試**：
   - 新增碰撞測試驗證修復後碰撞消失
   - 新增穩定性測試驗證跨執行 embedding_hash 一致

## Non-Goals

- 改進 cache 的語意相似度檢索邏輯（已在 `get_cached_response()` 中實現）
- 改變 Redis 連接或 TTL 策略
- 遷移至其他 cache 後端

## Capabilities

### New Capabilities

- `cache-key-stability`: 使用加密雜湊確保 embedding cache key 跨行程/跨平台穩定，並防止碰撞

### Modified Capabilities

(none)

## Impact

- Affected code:
  - Modified: `src/cache_service.py` (第 147 行、`set_cached_response` 方法)
  - Modified: `tests/test_cache_service.py` (新增測試)
  - New: `openspec/specs/cache-key-stability/spec.md`
