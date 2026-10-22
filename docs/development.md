# 開發與驗證

## 前端開發

正式 Docker image 會在 Node build stage 執行前端建置，runtime 只保留 Python 與 `frontend/dist`。需要單獨開發介面時：

```bash
cd frontend
corepack enable
pnpm install --frozen-lockfile
pnpm dev
```

Vite 會把 `/api` 與 `/health` proxy 到 `http://localhost:8000`。前端檢查：

```bash
pnpm lint
pnpm typecheck
pnpm exec vitest run --maxWorkers=1
pnpm build
```

## Python 開發

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

Dev override 只掛載 `src`，不覆蓋 image 內的 `frontend/dist`；Python 修改後重啟 web/worker。前端修改需 Vite dev server 或重新 build image。web/worker 共用 `manual_data` 與 `bm25_data` volumes，讓背景 worker 能讀取已上傳文件、web 會在約 30 秒內自動載入 worker 重建的 BM25 index（`BM25_RELOAD_INTERVAL_SECONDS`）。移除 volumes 會清除這些本機資料。

## CI

`.github/workflows/ci.yml` 在每個 pull request 與 push 到 master 時執行三個 job，只需要讀取權限、不使用任何 secret：

- **Backend tests**：Python 3.13，以 `pip install --require-hashes -r requirements.txt` 安裝（與 Dockerfile 相同的鎖定檔），執行 `python -m pytest tests`。測試不需要重排模型、jieba 大字典或網路；需要 `OPENROUTER_API_KEY` 的測試會 skip。
- **Frontend checks**：Node 22、pnpm 11.19.0，`pnpm install --frozen-lockfile` 後執行 lint、`pnpm build`（含 typecheck）與 vitest。
- **Terraform validation**：`terraform fmt -check -recursive`，以 `terraform init -backend=false` 後 `terraform validate`。

CI 不建置 Docker 映像、不呼叫真實 LLM、不跑 RAGAS；這些仍以本機或 `scripts/eval_sample_manual.py` 執行。部署是另一個 workflow（`deploy.yml`，只在 `release/**` 觸發）。

## 前端驗收

元件與 parser 測試：`cd frontend && pnpm exec vitest run --maxWorkers=1`。完整驗收證據與尚未完成項目見 `openspec/changes/archive/2026-10-03-replace-gradio-frontend/validation.md`。

瀏覽器驗收腳本 `frontend/e2e/acceptance.mjs` 使用 Playwright 與本機 Chrome，mock API，不會呼叫真實 LLM 或提交真實上傳。先建置前端，再從 `frontend/dist` 啟動本機靜態 server（預設 `http://127.0.0.1:18173`）。在有 Playwright 的環境執行 `node frontend/e2e/acceptance.mjs`；也可用 `PLAYWRIGHT_MODULE_PATH` 指向現有 Playwright 的 `index.mjs`。`ACCEPTANCE_URL` 可指定測試 URL，`ACCEPTANCE_OUTPUT` 可指定截圖目錄。

Docker smoke test 使用 image 內的 Python 與 fake chat/job provider：

```bash
docker build -t rag-react-acceptance .
docker run --rm --mount type=bind,source="$PWD/scripts/frontend_docker_smoke.py",target=/tmp/frontend_docker_smoke.py,readonly rag-react-acceptance python /tmp/frontend_docker_smoke.py
```

`frontend/e2e/socket-acceptance.mjs` 可對 `scripts/phone_acceptance.py` 的隔離同源測試站執行真實 HTTP cookie/history/abort/upload 流程（fake provider/job、記憶體資料）。指定 `ACCEPTANCE_URL` 與相同 Playwright 路徑即可重跑；測試站登入 key 僅供測試使用，不能部署為正式服務。

`scripts/real_rag_smoke.py` 提供需明確授權的真實 provider 單人／兩人測試。授權前不執行；caller 私下提供 credentials，設 `ALLOW_LIVE_RAG_SMOKE=true`，不把 key 寫入命令、log 或 commit。腳本停用 cache、tracing 與 history/Mongo 寫入，僅輸出耗時、回答長度與來源數。

這些自動化測試不取代實際手機虛擬鍵盤、真實 provider 容量或正式 Cloud Run revision 的切換與 rollback 驗收。

## 使用範圍細節

- 同一底層 key 共用預設 60 RPM。
- Redis 對話脈絡閒置 30 分鐘後過期；清除目前對話不會刪除 MongoDB 稽核紀錄、prompt cache 或 LangSmith trace。
- 「停止」會立即停止瀏覽器接收並保留目前顯示的文字、標示未完成；已在後端完成的回答仍可能完成保存，已送出的 provider 請求不保證立即停止計費，未保存的內容不保證重新整理後存在。
- 預設 `MAX_UPLOAD_BYTES=26214400`（25 MB）。上傳包含副檔名/MIME/大小/路徑基本驗證，但不含惡意檔案掃描或完整 PII/DLP 治理。
- 單程序會以 session 防止重複聊天／清除衝突（409）；不提供跨 instance 鎖。
- 手機中文鍵盤開啟時收起底部分頁與建議問題，輸入區跟隨 VisualViewport；關閉後恢復。主題跟隨系統深淺模式。
