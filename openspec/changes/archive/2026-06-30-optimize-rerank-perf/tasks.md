## 1. 提高 reranker 計算資源（terraform/main.tf）

- [x] 1.1 在 `terraform/main.tf` 的 `google_cloud_run_v2_service.web` → `template.containers.resources.limits`，將 `cpu` 由 `"1"` 改為 `"4"`（`memory` 維持 `"2Gi"`，4 vCPU 在 2Gi 上限內合法）。
- [x] 1.2 在同一 `containers` 區塊的 plain env 群組（與 `LANGCHAIN_TRACING_V2` 同層）新增一個 `env { name = "OMP_NUM_THREADS" value = "4" }`，數值與 1.1 的 cpu 對齊，使 torch 吃滿 4 核。
- [x] 1.3 本機跑 `terraform plan`（在 `terraform/` 目錄）確認只動 `google_cloud_run_v2_service.web`（預期 1 changed、0 add、0 destroy），未波及 job 或基礎設施。
- [x] 1.4 確認滿足 spec 需求「Rerank stage is resourced for bounded inference latency」：`OMP_NUM_THREADS`（1.2）與 `cpu`（1.1）數值一致，且餵入 reranker 的候選受 `FUSION_TOP_M`（task 2.1）約束，避免只加 CPU 卻不設執行緒數導致加核無效。

## 2. 減少 reranker 候選數（src/config.py）

- [x] 2.1 在 `src/config.py` 將 `FUSION_TOP_M` 由 `10` 改為 `5`，並更新該行註解說明（餵給 reranker 的候選減半以降推論時間）。`BM25_TOP_N`、`VECTOR_TOP_N` 維持 `10` 不變（融合前候選池不縮，只縮融合後餵 reranker 的數量）。
- [x] 2.2 確認 `src/app.py` 與 `src/rerank_stage.py` 皆透過 `config.FUSION_TOP_M` 讀取、無硬編碼 `10`，符合 hybrid-retrieval spec「參數須為 config 常數」要求。

## 3. 部署

- [x] 3.1 在 `feature/optimize-rerank-perf` commit 上述改動 → merge 進 `master`（push 不部署）。
- [x] 3.2 從 master 開/更新 `release/**` 分支並 push，觸發既有 CI：native amd64 build + push（git SHA tag）+ scoped `terraform apply -target=google_cloud_run_v2_service.web -target=google_cloud_run_v2_job.sync`。CI 會同時帶上 image（含 `FUSION_TOP_M=5`）與 main.tf 的 cpu/env 變更。
- [x] 3.3 CI 完成後確認 Cloud Run 產生新 revision，且 revision 的 container 資源顯示 `cpu=4`、env 含 `OMP_NUM_THREADS=4`。

## 4. 驗證效果

- [x] 4.1 等新 revision 接管流量（minScale=0，必要時先 `curl /` 觸發冷啟動）後，在 UI 送一則 chat 查詢觸發完整 RAG pipeline。
- [x] 4.2 到 smith.langchain.com 看該次 trace 的瀑布圖，確認 `_rerank` 階段耗時由 ~25s 降至 ~3–5s、整體回應降至 ~5–8s。
- [x] 4.3 抽查回答品質：同一組代表性問題在 `FUSION_TOP_M=5` 下答案仍正確、引用文件合理（候選減半未顯著傷害召回）。若品質明顯下降，記錄並評估回調 `FUSION_TOP_M` 或改採換小模型/ONNX 量化方案。
