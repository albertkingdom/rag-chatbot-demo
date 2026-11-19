## 1. Vertex 重排 client

- [x] 1.1 依「新增 VertexReranker，介面與 PineconeReranker 相同」與「以 ADC 取得憑證，並以 AuthorizedSession 自動更新 token」新增 `src/vertex_reranker.py`，滿足 Rerank scorer is injected as a callable。驗證：先寫 `tests/test_vertex_reranker.py`（以假的 session 與憑證，不連網路），涵蓋 session 以 `max_refresh_attempts=0` 建立、使用者憑證補上配額專案而 service account 憑證不補（不送 `X-Goog-User-Project`）、兩個 thread 同時第一次呼叫只取得一次憑證、請求 URL、model、records、分數依輸入順序、缺 `score` 視為 0、缺 record 為 `RerankError`、不同 query 與段數超過上限不發請求、空輸入不發請求、HTTP 錯誤只發一次且訊息只含狀態碼、逾時傳遞、一個 session 重用、建立 client 時不取得憑證；確認測試在 client 不存在時失敗，實作後通過。
- [x] 1.2 依「provider 以 GCP_PROJECT_ID 決定專案」修改 `src/services.py` 的 `_build_reranker`，滿足 Thread-safe singleton providers 的缺專案情境。驗證：`tests/test_services.py` 改為斷言建立 `VertexReranker`、缺 `GCP_PROJECT_ID` 時拋出含 `GCP_PROJECT_ID` 的 `ValueError` 且不建立 client；`tests/test_app_pipeline.py` 的逾時與錯誤分類測試仍通過。

## 2. 門檻與部署設定

- [x] 2.1 依「評分門檻預設改為 0.4」修改 `src/config.py` 的 `GRADE_SCORE_THRESHOLD` 預設值。驗證：`tests/test_retrieval_grader.py` 中斷言預設門檻的測試改為 0.4；`python -m pytest tests -q` 全部通過（測試中以固定門檻 0.30 的 fixture 不受影響）。
- [x] 2.2 依「Terraform 啟用 API 並授權執行期 service account」修改 `terraform/main.tf`（`disable_on_destroy = false`；Cloud Run 資源不對它加 `depends_on`）。驗證：`cd terraform && terraform fmt -check -recursive && terraform init -backend=false -input=false && terraform validate` 通過。
- [x] 2.3 依「本機 docker-compose 掛入 ADC 憑證」修改 `docker-compose.yml` 的 web 服務（volume 長語法、`create_host_path: false`）。驗證：`docker compose config -q` 通過；`tests/test_monitoring_config.py` 等既有 compose 測試通過。

## 3. 評估與文件

- [ ] 3.1 以本分支重建本機 web（掛入 ADC），在 demo index 評估（**呼叫付費 API 前先取得使用者同意**）：(a) 在 web 容器內對 `scripts/load_baseline_questions.json` 的 `manual` 鍵 65 題執行 `get_retrieval_chain().ainvoke`，以 `get_top_score` 與新門檻計算低信心題數，並對 18 題手冊外問題（`offtopic` 12 題加 `rerank-with-answer-text` proposal 列出的 6 題）計算誤判通過數，同時確認沒有 `rerank_error`；(b) 依 eval-rag skill 執行 `scripts/eval_sample_manual.py --audit-index carbon-assistant-sample-index`：在 web 容器內執行時覆寫 `DATA_SOURCE_DIR`、`BM25_INDEX_DIR` 到 `/tmp` 下的目錄（避免同步改寫 web 的 volume），並明確設定 `PINECONE_INDEX_NAME=rag-demo-qa-index` 與 `GCP_PROJECT_ID`；與 `tests/rag_eval_report.json`（2026-10-21）比較四項指標。Pinecone 額度用完，無法在不含本次變更的版本重跑基準，因此比較的是「取較高分＋改用 Vertex＋新門檻」以及其間其他變更的總和，結論註明。驗證：(a) 低信心題數不高於 5（BGE 取較高分的結果）、誤判通過數不高於 2；(b) 四項指標都不低於 0.7；結果寫進 `architecture.md` 的 Evaluation 段落，依 eval-rag 決定是否更新報告檔。
- [ ] 3.2 更新文件：`architecture.md`（System Overview 的 Reranker、第 5 節重排、Key Parameters 的重排模型與門檻、Performance Optimizations、已知問題表移除「本機與正式站共用 Pinecone 重排的每月額度」與「手冊有寫的問題被判為低信心」）、README（Hybrid Search 描述與本機啟動需要的 ADC 與 `GCP_PROJECT_ID`）、`docs/sli-slo.md`（提到 Pinecone 重排與門檻 0.3 之處）。另外：`architecture.md` 的 mermaid 流程圖與 sequence diagram 中的 `0.3`、路由段落與已知問題中「Gmail 0.645」（BGE 分數，已不適用）、已知問題「評分門檻 0.3 缺乏標註資料驗證」改為新門檻依小樣本訂定、第 8 節註明切換前後的 `top1_score`／`threshold` 與 Grafana 低信心比例不能直接比較；`CLAUDE.md` 的專案地圖與「要同步 architecture.md 的檔案」清單加入 `src/vertex_reranker.py`；eval-rag skill 的費用說明加入 Vertex AI Ranking。驗證：內容審查，`python -m pytest tests -q` 全部通過。
- [ ] 3.3 上線前置（**由使用者操作**）：以 owner 權限在本機只套用這兩個資源：`cd terraform && terraform apply -target=google_project_service.discoveryengine -target=google_project_iam_member.runtime_discoveryengine_user`（不套用 Cloud Run，避免 `image_tag` 不對時把正式站換成錯的 image；apply 前先看 plan 確認只有這兩項；沒有預設值的變數照平常的 `terraform.tfvars` 傳入），並以 `gcloud projects get-iam-policy` 確認；在 GCP Billing 設預算警示。驗證：使用者確認兩項已完成；CI 部署後正式站一題 rag 問題的 `rag_rerank_duration_seconds{outcome="ok"}` 增加。

## 4. 歸檔順序

- [ ] 4.1 先歸檔 `rerank-with-answer-text`（它的 3.x 依賴本 change 的 3.1、3.2 完成），再歸檔本 change，兩者在同一個 PR。驗證：`spectra archive` 依序成功，`openspec/specs/rerank-stage/spec.md` 同時含兩個 change 的修改。
