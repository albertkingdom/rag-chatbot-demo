## Context

`src/services.py` 的 `get_reranker_model()` 以 `PINECONE_API_KEY` 建立 `PineconeReranker`（`src/hosted_reranker.py`），檢索 chain 注入它的 `score(pairs) -> list[float]`。`_rerank_or_fallback` 以 `RERANK_TIMEOUT_SECONDS`（5 秒）限制呼叫，任何例外或逾時都退回融合排序並記錄 `rag_rerank_duration_seconds`。`fix/rerank-with-answer-text` 分支（本 change 所在分支）已讓每個候選以「問題」與「問題＋答案」兩段評分、取較高分。Cloud Run 的執行期 service account 由 Terraform 管理，web 服務已有 `GCP_PROJECT_ID` 環境變數（背景同步用）。

## Goals / Non-Goals

**Goals:**

- 重排改用 Vertex AI Ranking，消除每月 500 次的上限，並保留現有的逾時、退回與觀測行為。
- 以實測的分數分布重新設定評分門檻。
- 正式站與本機都能以 Google 憑證呼叫，不新增需要保管的 API key。

**Non-Goals:**

- 刪除 Pinecone 重排 client、提供切換設定、處理誤路由（見 proposal）。

## Decisions

### 新增 VertexReranker，介面與 PineconeReranker 相同

`src/vertex_reranker.py` 提供 `VertexReranker(project_id, timeout)`，`score(pairs)` 的行為與現有 client 一致：所有配對必須同一個 query、空輸入不發請求、段數上限檢查在發請求前完成、只發一次請求不重試、回傳依輸入順序的分數、回應缺分數時拋 `RerankError`、錯誤訊息只含 HTTP 狀態碼。請求送到 `https://discoveryengine.googleapis.com/v1/projects/{project}/locations/global/rankingConfigs/default_ranking_config:rank`，body 為 `model`（`semantic-ranker-default-005`）、`query`、`records`（以輸入位置為 `id`，文字放在 `content`）；回應依 `id` 對回分數，Vertex 可能省略分數極低的 record 的 `score` 欄位，視為 0。段數上限沿用 100（Vertex 每次最多 1,000 筆，100 已足夠且與既有上限測試一致）。

替代方案是改用 `google-cloud-discoveryengine` SDK：多一個依賴，且逾時與重試行為較難明確控制，不採用。

### 以 ADC 取得憑證，並以 AuthorizedSession 自動更新 token

client 以 `google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])` 取得憑證，建立 `google.auth.transport.requests.AuthorizedSession`（`google-auth[requests]` 已在鎖定的依賴中），一個 client 共用一個 session。`AuthorizedSession` 預設 `refresh_status_codes=(401,)`、`max_refresh_attempts=2`，收到 401 會更新 token 並重送同一個請求，這違反「不重試」，因此建立時傳入 `max_refresh_attempts=0`；token 仍會在送出前依到期時間主動更新。

配額專案只用於使用者憑證：本機 ADC 是使用者憑證（`google.oauth2.credentials.Credentials`），沒有設定配額專案時以 `with_quota_project(project)` 補上，讓呼叫計入本專案；Cloud Run 的 service account 憑證不設配額專案、不送 `X-Goog-User-Project`，因為送了會需要 `serviceusage.services.use` 權限（`roles/discoveryengine.user` 不含）。

憑證在第一次呼叫 `score` 時才取得，`import` 與建立 client 都不連網路，符合「No module-level side effects on import」；多個 worker thread 同時第一次呼叫時，以 lock 保護懶初始化，只建立一個 session。

### provider 以 GCP_PROJECT_ID 決定專案

`_build_reranker()` 讀 `GCP_PROJECT_ID`，沒有設定時拋 `ValueError` 並提到 `GCP_PROJECT_ID`，不建立 client（取代原本對 `PINECONE_API_KEY` 的檢查）。

### 評分門檻預設改為 0.4

`GRADE_SCORE_THRESHOLD` 的預設值由 0.3 改為 0.4：實測 Vertex 取較高分時，0.35 到 0.5 的低信心與誤判數相同，0.4 位於中央。這個「穩定」只代表樣本中沒有分數落在這個區間（手冊外只有 6 題），因此在 `architecture.md` 已知問題保留「門檻依小樣本訂定」並於上線後追蹤低信心比例。環境變數覆寫與無效值處理不變；`tests/test_retrieval_grader.py` 斷言預設值為 0.3 的測試要改為 0.4。Mongo 紀錄的 `top1_score`／`threshold` 與 Grafana 的低信心比例，切換前後（BGE 與 Vertex）不能直接比較，文件註明。

### Terraform 啟用 API 並授權執行期 service account

新增 `google_project_service.discoveryengine` 啟用 `discoveryengine.googleapis.com`，設 `disable_on_destroy = false`（避免 destroy 或移除 resource 時停用 API；2026-11-19 已手動啟用，接管是冪等的），並以 `google_project_iam_member.runtime_discoveryengine_user` 給執行期 service account `roles/discoveryengine.user`（含 `discoveryengine.rankingConfigs.rank`）。Cloud Run 資源不對 `google_project_service` 加 `depends_on`：CI 部署以 `-target` 只 apply Cloud Run service 與 job，加了依賴會讓 deployer service account 需要 serviceusage 權限而失敗。web 服務已有 `GCP_PROJECT_ID`，不需新增環境變數。

### 本機 docker-compose 掛入 ADC 憑證

web 服務以 volume 長語法新增唯讀 bind mount，並設 `create_host_path: false`（短語法在主機檔案不存在時會建立同名目錄，之後 `gcloud auth application-default login` 就寫不進去），把主機的 ADC 憑證檔（預設 `${HOME}/.config/gcloud/application_default_credentials.json`，可用 `GOOGLE_ADC_FILE` 覆寫）掛到容器內，設定 `GOOGLE_APPLICATION_CREDENTIALS` 指向它，並傳入 `GCP_PROJECT_ID`。README 說明需先執行 `gcloud auth application-default login` 並在 `.env` 設定 `GCP_PROJECT_ID`。沒有憑證時重排會失敗並退回融合排序，服務仍可運作。

## Implementation Contract

**行為**

- 檢索 chain 的重排呼叫 Vertex AI Ranking；成功時回傳依「取較高分」排序的前 3 名與分數，失敗或逾時時退回融合排序（行為與現在相同）。
- `get_reranker_model()` 在 `GCP_PROJECT_ID` 未設定時拋 `ValueError`（訊息含 `GCP_PROJECT_ID`），且不建立 client。
- 未設定 `GRADE_SCORE_THRESHOLD` 時門檻為 0.4。

**介面**：`VertexReranker.score(pairs: list[tuple[str, str]]) -> list[float]`；`RerankError` 沿用 `src/hosted_reranker.py` 的定義，讓既有的錯誤分類不變。

**失敗模式**：HTTP 非 200（含 401、403）→ `RerankError`（訊息只有狀態碼），不重送；回應缺 record → `RerankError`；不同 query 或段數超過上限 → `ValueError`（不發請求）；HTTP 逾時 → `requests.Timeout`（`_rerank_or_fallback` 歸類為 timeout）；取得或更新憑證失敗（含 `google.auth.exceptions.TransportError`）→ 例外（歸類為 error）。

**驗收**

- `tests/test_vertex_reranker.py`：session 以 `max_refresh_attempts=0` 建立、使用者憑證補上配額專案而 service account 憑證不補、並發第一次呼叫只初始化一次、請求格式（URL、model、records）、分數依輸入順序、缺 `score` 視為 0、缺 record 為錯誤、不同 query 拒絕、段數上限、空輸入不發請求、HTTP 錯誤只發一次且訊息不含內容、逾時傳遞、一個 session 重用；以假的 session 與憑證測試，不連網路。
- `tests/test_services.py`：provider 建立 `VertexReranker`、缺 `GCP_PROJECT_ID` 時拋錯。
- `tests/test_retrieval_grader.py`：門檻預設 0.4。
- `cd terraform && terraform fmt -check -recursive && terraform validate` 通過；`docker compose config -q` 通過。
- demo index 評估：65 題手冊問題的低信心題數與手冊外誤判數（第一次檢索）、RAGAS 四項指標都不低於 0.7；結果寫進 `architecture.md`。

**範圍內**：上述檔案、Terraform IAM 與 API、compose、README、architecture.md、`docs/sli-slo.md` 的重排說明。

**範圍外**：Pinecone 向量檢索（不變）、`scripts/compare_rerankers.py`、路由。

## Risks / Trade-offs

- [本機沒有 ADC 憑證時重排失敗] → 退回融合排序、服務可運作；README 說明設定步驟。
- [Vertex 的分數分布與 BGE 不同，門檻依小樣本訂定] → 以 demo index 重新量測；手冊外 6 題樣本小，結論註明。
- [Cloud Run 上的延遲與本機不同] → 上線後以 `rag_rerank_duration_seconds` 觀察；本機實測 p50 168 ms，遠低於 5 秒逾時。
- [資料送往 Google Cloud Ranking API] → 屬於本專案的 Google Cloud 合約與資料治理範圍；proposal 已註明。
- [費用沒有硬性上限] → 依查詢計費，目前流量估計每月不到 1 美元；在 GCP Billing 設預算警示（任務 3.3，由使用者操作）。

## Migration Plan

1. **合併前由人以 owner 權限在本機只套用 `google_project_service.discoveryengine` 與 `google_project_iam_member.runtime_discoveryengine_user`**（`-target` 這兩項，不套用 Cloud Run，避免 `image_tag` 不對時換成錯的 image；apply 前看 plan 確認）；以 `gcloud projects get-iam-policy` 確認執行期 service account 有 `roles/discoveryengine.user`。CI 的部署只 `-target` Cloud Run service 與 job，不會套用這兩項；沒先套用就發 release，正式站會收到 403、全部退回融合排序。
2. 合併後經 `release/**` 部署 Cloud Run。
3. 部署後在正式站送一題 rag 問題，確認 `rag_rerank_duration_seconds{outcome="ok"}` 增加、`outcome="error"` 沒有持續出現（403 代表權限或配額專案設定有誤）。
4. 回復：git revert 此 change 並重新部署；Pinecone 額度重置前，回復只會回到退回融合排序的狀態。IAM binding 與 API 可保留。
