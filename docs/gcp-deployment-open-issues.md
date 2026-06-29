# GCP 部署 — 待討論議題（gcp-deployment change）

這些是審視 `gcp-deployment` change 時發現、**尚未納入該 change 結論**的部署議題。
架構主軸（GCS 共享儲存 + 保留 Cloud Run Job + max-instances=1 + BM25 指標檔惰性重載 +
Redis 不可移除 + secret/命名修正）已於 2026-06-28 討論定案並寫回 change 的 design/spec/tasks。

下列項目留待後續逐一討論後再決定是否補進 change：

## 1. Cloud Run Job 的 container command/entrypoint ✅ 已解決（2026-06-28，已寫回 change）
- 問題：Job 與 Service 共用同一 image，但 Dockerfile 預設 `CMD` 是 `uvicorn src.app:app`（啟動 web server，永不結束）。
- 結論（採同一 image + Job 層 command override，對應 Google 建議／12-factor admin process）：
  - 不動 Dockerfile `CMD`（Service 沿用）；在 Terraform `google_cloud_run_v2_job` 設 container `command = ["python", "-m", "src.build_vector_store"]`，Service 不設 `command`。
  - 進入點重用 `src/build_vector_store.py` 既有的 `__main__`（已呼叫 `sync_vector_store()`），**不另開 `run_sync.py`**。
  - 修正 `__main__` 的 `except`：失敗時 `sys.exit(1)`，否則 sync 失敗會被 Cloud Run 誤判為 SUCCEEDED。
- 寫回位置：design.md「Cloud Run Job 的進入點」決定、spec `gcp-cloud-run-job`（新 requirement「runs the sync entrypoint via container command override」）、tasks 3.4 與新增 6.6。

## 2. Container port：80 vs $PORT ✅ 已解決（2026-06-28，已寫回 change）
- 問題：Dockerfile `EXPOSE 80` + `uvicorn --port 80`；Cloud Run 預設把流量送往 `$PORT`（8080）並注入該環境變數。未對齊則 Service 起得來但收不到流量 / 健康檢查失敗。
- 結論（採方案 b：uvicorn 讀 `$PORT`）：
  - `Dockerfile` `CMD` 改 shell form `CMD ["sh", "-c", "uvicorn src.app:app --host 0.0.0.0 --port ${PORT:-80}"]`，`EXPOSE` 改 `8080`。
  - Cloud Run：讀 `PORT=8080` → 聽 8080；本機 docker-compose：未設 `PORT` → 退回 80，行為不變。
  - Terraform `google_cloud_run_v2_service` 不設 `container_port`，沿用 Cloud Run 預設 8080。
  - 不採方案 (a)（Terraform 設 container port=80）：Dockerfile 與 Terraform 隱性耦合、不符 Cloud Run 慣例。
- 寫回位置：design.md「Container port」決定、spec `gcp-cloud-run-service`（新 requirement「Service listens on the Cloud Run injected $PORT」）、tasks 新增 5.2。

## 3. BGE 模型預下載的 cache layout 是否相符 ✅ 已解決（2026-06-28，已寫回 change）
- 問題：Dockerfile 用 `snapshot_download(cache_dir=...)` → 產生 HF hub 格式 `models--BAAI--bge-reranker-v2-m3/...`；`get_reranker_model()` 用 `HuggingFaceCrossEncoder(cache_folder=...)`。layout 不符 → 冷啟動重新下載、預下載落空。
- 釐清：現代 sentence-transformers（2.3+）的 `CrossEncoder` 下載委派 `huggingface_hub`，與 `snapshot_download` 用同一套 HF hub 格式，故**相符**；舊版（<2.3）才用扁平資料夾而不符。
- 結論（基本相符 + 三項加固）：
  - **鎖版本**：`requirements.txt` 釘 `sentence-transformers`/`huggingface_hub`（如 `sentence-transformers>=2.3,<4`），避免拉到 layout 不同的版本。
  - **路徑單一來源**：Dockerfile 預下載與 `get_reranker_model()` 共用 `MODEL_CACHE_DIR`（預設 `/app/models`），`app.py` 不再用 `os.getcwd()` 推導，消除漂移。
  - **離線旗標**：設 `HF_HUB_OFFLINE=1`/`TRANSFORMERS_OFFLINE=1`，快取有就用、絕不連網（連 revision HEAD 都不打），達成冷啟動零網路。
  - **驗證強化**：8.2 改為「離線狀態仍能載入且無任何 HF 連線訊息」，離線能載入即證明 layout 相符。
- 寫回位置：design.md「在 Dockerfile Build 階段預下載 BGE 模型」決定、spec `gcp-cloud-run-service`（BGE requirement 強化）、tasks 5.1/5.3/6.7/8.2。

## 4. MongoDB Atlas M0 的 IP allowlist ✅ 已解決（2026-06-28，已寫回 change）
- 問題：Cloud Run 出口 IP 為動態；Atlas M0 預設需 IP 白名單，且 M0 不支援 VPC peering/PrivateLink（M10+ 才有）。
- 結論（採方案 a：白名單開 `0.0.0.0/0` + 帳密/TLS 把關）：
  - **白名單 `0.0.0.0/0`**，安全由帳密 + TLS 承擔；不採方案 (b)（Cloud NAT 靜態 IP），對 demo/低流量過度投資。
  - 加固：**強隨機密碼只存 Secret Manager**、**最小權限 DB user**（僅 `chatbot` 的 `readWrite`）、**強制 `mongodb+srv` TLS**。
  - 升級路徑：需更嚴格隔離時升級 M10+ 走 PrivateLink/VPC peering，或加 Serverless VPC Access + Cloud NAT 取靜態出口 IP 收斂白名單。
  - Atlas 不歸 Terraform 管，為手動 Console 步驟（task 2.2）。
- Upstash 走 `rediss://` + token，較無此問題。
- 寫回位置：design.md「MongoDB Atlas M0 的 IP allowlist」決定 + Risks 一條、tasks 2.2。

---
建立日期：2026-06-28
