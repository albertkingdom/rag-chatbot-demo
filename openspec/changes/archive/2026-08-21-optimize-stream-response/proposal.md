## Summary

將 `chat_stream` 中 7 處逐字元 yield 迴圈替換為共用的逐 chunk async generator，降低 yield 次數約 90%，減少 Stream Response 階段的 sleep 累積時間。

## Motivation

目前 `rag_pipeline.py` 的 `chat_stream` 函式在回傳回應時，使用逐字元 yield 搭配 `asyncio.sleep(0.005)` 產生打字機效果。一個 480 字元的回應需要 yield 480 次，光 sleep 就累積約 2.4 秒。根據 LangSmith trace，Stream Response 佔整體回應時間約 20%。改為每次 yield 10 個字元，yield 次數降為 48 次，sleep 總時間降至約 0.24 秒，用戶視覺體驗幾乎不變。

此外，7 處重複的逐字 yield 迴圈模式違反 DRY 原則，提取為共用函式可提升可維護性。

## Proposed Solution

新增 `_stream_text(text: str)` async generator 函式，以固定 chunk size（預設 10 字元）yield 文字。將 `chat_stream` 中所有 `for i in range(1, len(...) + 1): yield ...[:i]; await asyncio.sleep(0.005)` 迴圈替換為 `async for chunk in _stream_text(...): yield chunk`。chunk size 和 delay 定義為模組常數 `_STREAM_CHUNK_SIZE` 和 `_STREAM_CHUNK_DELAY`，方便後續調整。

## Non-Goals

- 不改變 LLM 生成階段的 streaming 行為（`astream` 的 chunk 是由 LLM API 決定）
- 不改變回應的最終內容（來源、時間標記等）
- 不移除打字機效果，僅降低 yield 頻率

## Capabilities

### New Capabilities

- `stream-response`: 定義 chat_stream 中文字回應的 yield 行為，包含 chunk size、delay、以及逐 chunk 累積輸出的契約

### Modified Capabilities

（無）

## Impact

- Affected specs: `stream-response`（新增）
- Affected code:
  - Modified: `src/rag_pipeline.py`
