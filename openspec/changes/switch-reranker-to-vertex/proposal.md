## Why

服務的重排使用 Pinecone Inference 上的 bge-reranker-v2-m3。Starter 方案每月只有 500 次，本機開發、評估與正式站共用同一個專案與 API key；2026-11-19 用到 527 次後重排被暫停，正式站所有 rag 回答都退回融合排序並走低信心路徑。同時，「手冊有寫的問題被判為低信心」（問題二）的修正在 `fix/rerank-with-answer-text` 分支已完成，但評估因額度用完而無法完成。

2026-11-19 以 demo index 實測 Vertex AI Ranking（`semantic-ranker-default-005`）：繁體中文正常；以「問題」與「問題＋答案」取較高分時，65 題手冊問題在門檻 0.35 到 0.5 之間都只有 3 題低信心（BGE 同做法為 5 題），6 題會走 rag 的手冊外問題誤判 2 題（與 BGE 相同，都是既有的關鍵字誤路由），12 題閒聊沒有誤判；12 題評估題 hit@1 12/12；每次呼叫 p50 168 ms（Pinecone BGE 為 456 ms）。Vertex 依查詢計費（每 1,000 次約 1 美元），沒有每月硬性上限，以 Cloud Run 的 service account 呼叫，資料留在本專案的 Google Cloud。

## What Changes

- 服務的重排改用 Vertex AI Ranking API（`semantic-ranker-default-005`），新的 client 以 Google 應用程式預設憑證（ADC）呼叫，對外介面維持 `score(pairs) -> list[float]`，沿用「取較高分」的多段評分（`rerank-with-answer-text`）。
- 評分門檻 `GRADE_SCORE_THRESHOLD` 的預設值由 0.3 改為 0.4（落在實測的穩定區間中央），仍可用環境變數覆寫。
- Terraform：在專案啟用 Discovery Engine API，並給 Cloud Run 執行期 service account 呼叫 Ranking API 的權限；web 服務已有 `GCP_PROJECT_ID` 環境變數，client 用它決定專案。
- 本機 docker-compose：web 服務掛入使用者的 ADC 憑證檔（唯讀）並傳入 `GCP_PROJECT_ID`，本機重排也走 Vertex。
- 以 demo index 重新評估：65 題手冊問題與手冊外問題的低信心／誤判數、RAGAS 四項指標，寫進 `architecture.md`。

## Non-Goals

- 不刪除 `src/hosted_reranker.py`：`scripts/compare_rerankers.py` 的 `pinecone-bge` provider 仍使用它；服務本身不再使用。
- 不提供在 Pinecone 與 Vertex 之間切換的設定；回復以 git revert 處理。
- 不處理關鍵字誤路由（「忘記 Gmail 密碼」）與「LLM 路由會把追問判為 direct」，另議。
- 不改變重排逾時、退回融合排序與觀測指標的行為。

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `rerank-stage`: 注入的重排 client 改為 Vertex AI Ranking。
- `app-structure`: 重排 provider 缺少設定時的錯誤改為專案 ID。

## Impact

- Affected specs: rerank-stage、app-structure
- Affected code:
  - New: src/vertex_reranker.py, tests/test_vertex_reranker.py
  - Modified: src/services.py, src/config.py, docker-compose.yml, terraform/main.tf, tests/test_services.py, architecture.md, README.md, docs/sli-slo.md
  - Removed: (none)
- 費用：正式站以目前流量估計每月不到 1 美元；超過免費額度只計費、不會中斷。
- 隱私：使用者問題與手冊內容送往 Google Cloud 的 Ranking API（本專案的計費專案），由既有的 Google Cloud 合約與資料治理管理。
- 部署：IAM binding 與 API 啟用不在 CI 的 `-target` 範圍內，合併前要由使用者以 owner 權限在本機 `terraform apply`。
