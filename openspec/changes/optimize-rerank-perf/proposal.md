## Why

線上 chat 回應慢（~30s）。LangSmith 瀑布圖確診：總 30.61s 中 `_rerank` 階段佔 **25.32s（83%）**，其餘階段（Query Rewriting 1.04s、Intent Classification 1.18s、`_retrieve` 1.39s、生成 0.96s）都約 1s。根因是 Cloud Run `carbon-assistant-web` 只配置 **1 vCPU**，跑 `BAAI/bge-reranker-v2-m3`（568M, XLM-RoBERTa-large）對 10 個候選做 cross-encoder 推論，每個約 2.5s。已排除冷啟動惰性載入（followup 仍 25s、`Initializing BGE-Reranker` 一小時內僅出現 1 次，singleton 正常）與「兩次 OpenRouter LLM 跳轉」（各約 1s）的嫌疑。

## What Changes

- **Cloud Run web service 提高 CPU**：`terraform/main.tf` 中 `google_cloud_run_v2_service.web` 的 `cpu` 由 `"1"` 提高至 `"4"`，給 cross-encoder 推論更多平行運算資源。
- **設定 torch 執行緒數**：在同一 service 容器加入 plain env `OMP_NUM_THREADS=4`，使 PyTorch/底層 BLAS 實際用滿 4 核。單純加 CPU 而不設執行緒數，torch 預設可能不會吃滿核心，加核無效。
- **減少 reranker 候選數**：`src/config.py` 的 `FUSION_TOP_M` 由 `10` 降為 `5`，餵給 reranker 的候選減半，推論時間約再砍一半。`rerank-stage` 仍回傳 Top 3，功能契約不變。
- 預期效果：`_rerank` 25s → 約 3–5s，整體 chat 回應從 ~30s 降到 ~5–8s。

## Non-Goals

- **不換 reranker 模型**：保留 `bge-reranker-v2-m3`，不降級為 `bge-reranker-base`（278M），避免多語品質下降。
- **不導入 ONNX + int8 量化**：那是 CPU 推論的更徹底解法但工程量最大，留待後續若需 <3s production 級延遲再評估。
- **不調整基礎設施 apply 範圍**：沿用既有 CI scoped apply（只 `-target` Cloud Run service/job），不改 deployer 權限或 terraform backend。
- **不改 rerank 的功能行為**：top-M→top-3 的排序與截斷契約維持不變，本change 只動效能相關參數。

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `rerank-stage`: 既有「top-M→top-3」功能契約不變；新增一條非功能需求，固化「reranker 推論的執行緒平行度（`OMP_NUM_THREADS`）SHALL 與 runtime 配置的 vCPU 數匹配，且餵入候選數 SHALL 受 `FUSION_TOP_M` 約束」，避免未來只加 CPU 卻不設執行緒數導致加核無效的 footgun。

## Impact

- Affected specs: `rerank-stage`（新增 reranker 計算資源配置的非功能需求；既有功能需求不動）
- Affected code:
  - New: （無）
  - Modified:
    - `terraform/main.tf`（`google_cloud_run_v2_service.web`：`cpu` 1→4、新增 `OMP_NUM_THREADS=4` env）
    - `src/config.py`（`FUSION_TOP_M` 10→5）
  - Removed: （無）
- 部署影響：兩處改動都屬 app 層。`main.tf` 的 CPU/env 走既有 CI scoped apply（push `release/**`）或本機 `terraform apply -target=google_cloud_run_v2_service.web`；`FUSION_TOP_M` 改 config 需 rebuild image 由 CI 帶上去。
