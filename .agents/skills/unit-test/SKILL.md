---
name: unit-test
description: "替這個專案的變更撰寫或修改單元測試：測試放哪裡、怎麼不靠真實 Pinecone／Redis／LLM／網路、既有的假物件與注入方式（FakeRedis、monkeypatch 提供者、AsyncMock）、非同步與 skip 慣例、先看到測試失敗再修、前端 vitest 慣例，以及測試跑法。當使用者說「寫測試」「補測試」「加單元測試」「測試怎麼寫」，或實作新行為、修 bug、處理 review 發現的問題需要測試時使用。"
license: MIT
metadata:
  author: yklin
  version: "1.0"
---

# 單元測試（RAG demo 專案）

CI 不用任何機密、不連網、不呼叫真實 LLM（`docs/development.md` 的 CI 段）。所以**測試必須在沒有 API key 的環境通過**。

## 跑法

```bash
python -m pytest tests -q                       # 全部（約數十秒）
python -m pytest tests/test_cache_service.py -q # 單檔
python -m pytest tests -q -k "threshold"        # 依名稱
cd frontend && pnpm exec vitest run --maxWorkers=1
```

`tests/evaluate_rag.py`、`evaluate_rerank.py`、`generate_rag_eval_data.py` 是評估腳本，不是測試（檔名不以 `test_` 開頭，不會被收集），會呼叫真實服務，**不要放進一般測試流程**。

## 放哪裡、怎麼命名

- 後端：`tests/test_<模組名>.py`，對應 `src/<模組名>.py`。API 路由在 `test_api_routes.py`，對外契約（NDJSON 事件、錯誤格式）在 `test_api_contract.py`。
- 前端：與原始檔並排，`App.test.tsx`、`api.test.ts`、`ui.test.tsx`。
- 檔案開頭用一兩行說明它驗證哪個行為或規格（`openspec/specs/<name>`），測試名稱寫「在什麼情況下，會怎樣」，例如 `test_similarity_equal_to_threshold_is_a_miss`。
- 一個行為一個測試，不把多個斷言塞進一個「大測試」。邊界值（等於門檻、空輸入、上限）要有自己的案例。

## 不靠外部服務

不要用 `MagicMock()` 隨便包住一切再斷言「有被呼叫」，那只驗證了實作細節。依對象選擇：

| 對象 | 做法 | 範例 |
|---|---|---|
| Redis | 用 `test_cache_service.py` 的 `FakeRedis`：行為貼近真實伺服器（回傳 bytes、sorted set 排序、WRONGTYPE、pipeline 交易）。需要新指令時**擴充 FakeRedis 並讓它符合真實行為**，不要在單一測試裡臨時偽造 | `test_cache_service.py` |
| `chat_service` 的各個提供者（歷史、資料庫、快取、路由、檢索、生成、embedding、guardrail） | 用 `test_chat_service.py` 的 `providers` fixture：`monkeypatch.setattr(chat, "<名稱>", ...)` 換掉模組層的工廠函式；非同步的用 `AsyncMock`，串流用 async generator | `test_chat_service.py` |
| LLM／embedding／reranker | 回傳固定結果的假物件；需要真實 key 的測試加 `@pytest.mark.skipif(not os.environ.get("OPENROUTER_API_KEY"), reason="OPENROUTER_API_KEY not set")` | `test_query_router.py` |
| 環境變數 | `monkeypatch.setenv` 或 `patch.dict(os.environ, {...})`，不要讓測試依賴開發機的 `.env` | `test_config.py` |
| 時間、亂數 | 注入或 monkeypatch，不用 `sleep` | |

非同步測試用 `@pytest.mark.asyncio`（已安裝 `pytest-asyncio`）。

## 寫什麼

1. **新行為**：先寫測試，確認它**會失敗**（而且因為對的原因失敗），再實作。
2. **修 bug／處理 review 意見**：先寫能重現的測試並確認失敗，再修；commit 訊息 `fix(review): ...`（見 `review-findings`）。
3. **成對案例**：guardrail 規則要同時有「該擋」與「不該擋」；路由要有走 rag 與走 direct 兩邊；快取要有命中與未命中。
4. **低信心路徑**：固定說明、不顯示來源、不寫快取三件事分別斷言。
5. **契約**：NDJSON 事件型別（`status`、`delta`、`sources`、`metadata`、`done`、`error`）與錯誤格式改動，要更新 `test_api_contract.py`，前端 parser 測試也要同步。

## 不要做

- 不把真實金鑰、真實文件內容放進測試或 fixture；範例資料用虛構手冊（`examples/`）。
- 不為了讓測試通過而放寬斷言或刪掉案例；測試與規格衝突時，改程式或另開 change，不要改測試去配合。
- 不依賴測試執行順序或前一個測試留下的檔案；需要檔案用 `tmp_path`。
- 不 skip 沒有理由的測試。`skipif` 一定要寫明條件和原因。

## 完成前

後端全部測試與（有動前端時）lint、build、vitest 都通過再回報；回報時寫清楚實際執行了哪些、哪些被 skip 與原因。
