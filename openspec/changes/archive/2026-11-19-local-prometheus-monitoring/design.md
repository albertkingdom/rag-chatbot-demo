## Context

`docs/sli-slo.md` 定義了可用性、首字延遲、完整回應延遲與回答品質四個 SLI，但後端沒有輸出任何指標。現況有三個會影響設計的事實：

- `/api/v1/chat/stream` 回傳 `StreamingResponse`，事件由 chat service 的 `chat_event_stream` 產生，再由路由裡的 `ndjson_stream` 序列化。HTTP 狀態碼在串流開始時就送出 200，之後的錯誤以 `error` 事件表達；client 斷線時路由會中斷迴圈。
- chat service 的 direct 與 rag 分支都先把 LLM 的 `astream` 整段收完並通過輸出 guardrail，才由 `_finish_response` 以每 10 字一塊送出 `delta`。因此「第一個 token」的時間在現有架構下約等於完整生成時間。
- 正式環境是 Cloud Run，`min_instance_count = 0`、`max_instance_count = 1`。Prometheus 的 pull 模式會讓閒置實例被喚醒，且 Cloud Run 上沒有地方常駐 Prometheus。

2026-08-21 的 LangSmith 量測顯示 rag 路徑約 12 秒（之後換成託管重排，數字已過時）。`sli-slo.md` 裡的 3 秒、15 秒門檻沒有量測依據。

## Goals / Non-Goals

**Goals:**

- 本機 docker-compose 環境能輸出、抓取並視覺化 SLI 指標。
- 以壓測量出 rag 路徑首段回答與完整回應延遲的 P50／P95 基準，依基準訂 SLO 門檻。
- 可用性 burn rate 告警規則有可重現的自動化測試。
- 修正 `sli-slo.md` 中與實作不符的敘述（TTFT、instrumentator）。

**Non-Goals:**

- 不在 Cloud Run 正式環境輸出或收集指標，不改 terraform。理由：流量稀少加上 scale-to-zero，資料沒有統計意義；Managed Service for Prometheus 的 sidecar 成本與設定不划算。
- 不使用 `prometheus-fastapi-instrumentator`：它是 ASGI middleware，總耗時在 app 結束後的 `finally` 計算，含串流 body；另有 `duration_without_streaming` 只量到回應標頭送出。兩者都量不到「首段回答文字」的時間；串流中途的失敗會沿用已送出的狀態碼 200（本專案的路由還會先接住例外、改送 `error` 事件），所以它判斷不出可用性。請求層級的 SLI 必須依 NDJSON 事件自己量，它只會多出一組重複的 HTTP 指標。
- 不接 Alertmanager、不設通知管道；告警只在 Prometheus UI 顯示狀態。
- 不監控 RQ worker 與知識庫同步工作（批次工作需要 pushgateway，與本次 SLI 無關）。
- 不量依賴服務（OpenAI、Pinecone）各自的延遲；分段延遲查 LangSmith trace。
- 不改變輸出 guardrail 的整段暫存行為；本次只照實量測並在文件說明。
- 不做 fake LLM／fake 檢索模式。
- 回答品質（低信心比例）不寫 burn rate，只在 dashboard 呈現，因為它取決於壓測題庫組成。

## Decisions

### 只在本機 docker-compose 部署 Prometheus 與 Grafana

Prometheus、Grafana 作為 docker-compose 的新服務，Prometheus 經 compose 內部網路抓取 `web:80/metrics`。替代方案是 Cloud Run 上用 Managed Service for Prometheus sidecar，因成本與 scale-to-zero 衝突而不採用。映像檔標籤固定為明確版本（不用 `latest`），CI 的 promtool 使用同一個 Prometheus 標籤。

### 請求層級指標在路由層包裝器量測

新增 `src/observability.py`，提供一個包住事件串流的非同步產生器 `observe_chat_stream(events)`：原樣轉送每個事件，同時觀察事件型別。路由的 `ndjson_stream` 改為迭代 `observe_chat_stream(chat_event_stream(...))`，且兩者都在 `aclosing` 內，確保中斷時包裝器的 `finally` 會執行。替代方案是在 chat service 每個 `_finish_response` 呼叫點與例外處理埋點：呼叫點有四處以上，且讀取對話歷史發生在 chat service 的 `try` 之外，那裡的錯誤只有路由層看得到，因此不採用。

### 以首段回答延遲取代首字延遲

延遲 SLI 量「第一個 `delta` 事件」送出的時間，指標命名為 `chat_first_answer_text_seconds`。不量第一個任何事件，因為 `status` 事件幾毫秒內就會送出，SLI 會永遠達標。文件說明：輸出 guardrail 必須檢查整段回答，所以回答會先暫存，首段回答延遲接近完整生成時間。

### 請求結果的判定規則

每個請求在包裝器的 `finally` 恰好記錄一次 `chat_requests_total`：

| 情況 | outcome |
|---|---|
| 收到 `done` 事件 | success（含 guardrail 擋下的回應，那是刻意給出的正確回答） |
| 收到 `error` 事件，或內層產生器丟出例外（包裝器記錄後原樣拋出） | error |
| 以上皆非就結束（client 斷線導致產生器被關閉、`CancelledError`） | cancelled |

`response_source` 取自 `metadata` 事件；在它之前就結束的請求標為 `unknown`。`_finish_response` 的事件順序是 delta → sources → metadata → done，而錯誤路徑只送 `error`，所以 error 與 cancelled 實務上幾乎都是 `unknown`。因此**可用性 SLI 不依 `response_source` 過濾**：可用性 = 全部 success ÷（全部 success＋全部 error），cancelled 不列入分母；延遲 SLI 才依來源過濾。包裝器只把 `Exception` 判成 error；`CancelledError` 與 `GeneratorExit`（都不是 `Exception` 子類別）走 cancelled。`finally` 內只做同步的指標記錄，不得 `await`。路由裡兩層 `aclosing` 的巢狀順序為：包裝器在內層、先被關閉，再關閉 chat service 的產生器。延遲 histogram 只在 success 時 observe；首段回答延遲另需至少收到一個 `delta`。計時起點為包裝器開始迭代的單調時鐘，不沿用 chat service 的 `elapsed_ms`（它的起點晚於讀取對話歷史）。

### 延遲 bucket 先放寬，SLO 門檻等量完基準再訂

兩個請求延遲 histogram 共用 buckets：0.5、1、2、3、5、8、10、12、15、20、30、60 秒。重排 histogram 用 0.1、0.25、0.5、1、2、3、5、8 秒。SLO 門檻必須是 bucket 邊界之一，SLI 比例才是精確計數而非線性推估；若使用者想要的門檻不在邊界上，就同時調整 buckets。門檻數字只出現在 Prometheus 規則檔，dashboard 讀 recording rule，不在面板重複寫數字。

### 檢索與重排指標在既有計時點記錄

- `rag_retrieval_total{low_confidence}`、`rag_retrieval_retries_total`：在 chat service 決定檢索結果（`_retrieve_best` 產生 outcome、寫出 `[Retrieval]` log）之處記錄，每個 rag 請求一次。低信心比例的分母是進入檢索的 rag 請求，包含之後被輸出 guardrail 擋下的請求。
- `rag_rerank_duration_seconds{outcome}`：在 `_rerank_or_fallback` 的成功與失敗出口記錄，outcome 為 ok、timeout、error，沿用函式既有的計時與失敗分類。候選為空而未呼叫重排時不記錄。降級率由 `outcome!="ok"` 的 `_count` 比例算出，不另開 counter。

### 指標定義在模組層級並使用預設 registry

所有指標在 `src/observability.py` 模組層級建立一次，使用 `prometheus_client` 預設 registry。服務以單一 uvicorn worker 執行，不需要 multiprocess 模式。不可在 `create_app` 或任何會被重複呼叫的函式內建立指標，因為測試會在同一個 process 建立多個 app。測試以 `REGISTRY.get_sample_value` 比較前後差值，不重設 registry。label 只用 `response_source`、`outcome`、`low_confidence` 這類有限集合，禁止 session、query 等高基數值。

### METRICS_ENABLED 控制 /metrics 路由

`src/config.py` 新增 `METRICS_ENABLED`：環境變數值不分大小寫為 `true` 或 `1` 時為真，其餘（含未設定）為假。為真時 `create_app` 在 SPA catch-all 之前以 `add_route` 註冊 `GET /metrics`，回傳 `generate_latest()` 與 `CONTENT_TYPE_LATEST`，不列入 OpenAPI。為假時不註冊，該路徑落入 SPA fallback。`/metrics` 不在 `/api` 底下，驗證中介層不管，因此必須預設關閉。只有 docker-compose 的 web 服務設為 true，web 不需額外對外開 port。替代方案是用 `mount` 掛 ASGI app，會產生 307 轉址，不採用。

### 壓測腳本以 API key 依序送出不重複題目

`scripts/load_baseline.py` 以 `X-API-Key`（取自 `APP_API_KEY`）呼叫 `/api/v1/chat/stream`，不帶 cookie，因此不受單一 session 同時只能一個回答的限制，也不讀寫對話歷史。依序送出，每題之間預設間隔 1 秒，遠低於每分鐘 60 次的限流。安全檢查：腳本在 host 執行，啟動時以 `python-dotenv` 載入 `.env`（路徑可由 `--env-file` 指定，預設為 repo 根目錄的 `.env`；不覆寫已存在的環境變數），再讀 `PINECONE_INDEX_NAME`（未設定時為 compose 預設值 `rag-demo-qa-index`），讓腳本看到的 index 與 compose 一致；屬於 `scripts/eval_sample_manual.py` 的 `PRODUCTION_INDEXES` 時拒絕執行；題庫有重複題目時在送出任何請求前失敗。題庫 `scripts/load_baseline_questions.json` 至少 60 題可由範例手冊回答的不重複問題，另加至少 10 題閒聊或離題問題。

### 可用性 burn rate 規則以 promtool 單元測試並加入 CI

可用性 SLO 目標由使用者確認（`sli-slo.md` 草稿為 99.5%），它不依賴延遲基準，所以可用性規則與 CI 可以在付費壓測之前完成；延遲 recording rules 等門檻確認後才寫。採多視窗 burn rate：快速燒為 1 小時與 5 分鐘兩個視窗的錯誤率都超過 14.4 倍預算，慢速燒為 6 小時與 30 分鐘都超過 6 倍預算。`slo_test.yml` 以合成序列驗證兩組規則在應觸發時觸發、在正常流量時不觸發。CI 新增一個 job，以固定標籤的 Prometheus 映像執行 `promtool check config` 與 `promtool test rules`。另有 pytest 解析規則檔，確認規則引用的每個 `le` 值都在 `src/observability.py` 的延遲 buckets 裡。

## Implementation Contract

**行為**

- `METRICS_ENABLED=true` 時，`GET /metrics` 回應 200、內容型別為 Prometheus 文字格式，包含六個指標的 `# HELP` 行：`chat_requests_total`、`chat_first_answer_text_seconds`、`chat_response_duration_seconds`、`rag_retrieval_total`、`rag_retrieval_retries_total`、`rag_rerank_duration_seconds`。
- 未設定或非真值時，`GET /metrics` 的回應不含 `# HELP`（得到 SPA 的 index.html，或前端未建置時的 503 JSON）。
- 每個進入串流的 `/api/v1/chat/stream` 請求，`chat_requests_total` 恰好增加 1，labels 依上方判定規則。在串流開始前就回應錯誤的請求（例如 409、503）不記錄。
- 加入指標後，NDJSON 事件的內容、順序與 HTTP 行為不變。
- `docker compose up` 後：Prometheus（host port 9090）的 targets 頁面顯示 web 為 UP；Grafana（host port 3000）自動載入 Prometheus 資料來源與 RAG SLO dashboard，無需手動匯入。

**Dashboard 面板**：請求率（依 `response_source`）、可用性、rag 路徑首段回答與完整回應延遲的 P50／P95、SLO 達成比例（讀 recording rule）、28 天 error budget 剩餘、低信心比例、檢索重試率、重排 P95 與降級率。

**壓測腳本介面**：`python scripts/load_baseline.py [--base-url URL] [--questions PATH] [--env-file PATH] [--limit N] [--delay SECONDS]`，預設 `--base-url http://localhost:8000`、`--delay 1.0`。啟動時印出目標 index；完成後印出各 `response_source` × outcome 的請求數。前置檢查失敗（正式 index、缺少 `APP_API_KEY`、題庫重複）時以非零結束碼退出且不送出請求；前置檢查通過後，個別請求失敗只計入摘要，結束碼為 0。HTTP 回應非 200（例如 401、429）時記為 `unknown`／`http_error`。若全部請求都是 `http_error`，腳本印出警告，提醒檢查 URL 與 API key。

**基準與門檻流程**：清空本機 Redis 快取後，以壓測產生至少 50 個 `response_source="rag"` 的成功樣本；以 `histogram_quantile` 在壓測時間區間內算出 P50／P95；將日期、環境（Docker on Mac）、index、樣本數與數值寫入 `docs/sli-slo.md`；延遲門檻在任務 5.3 由使用者確認，任務 5.4 才寫入延遲規則。可用性目標不依賴基準，在任務 4.1 先行確認。

**失敗模式**：指標記錄本身不得讓請求失敗；包裝器觀察到內層例外時記錄 error 並原樣拋出，交給路由既有的例外處理送出 `error` 事件。

**驗收**：`python -m pytest tests -q` 通過（含新增的 observability、/metrics 開關、檢索與重排指標、壓測腳本、規則 `le` 值測試）；CI 的 promtool job 通過；手動確認 Prometheus targets 為 UP、Grafana dashboard 有資料。

**範圍內**：上述六個指標、`/metrics` 開關、compose 服務與 provisioning、壓測腳本與題庫、基準量測與文件、burn rate 規則與 promtool 測試、CI job、`sli-slo.md`／`architecture.md`／README 更新。

**範圍外**：Non-Goals 所列各項；改變任何既有的串流、guardrail、檢索或重排行為。

## Risks / Trade-offs

- [壓測打真實 API 會產生費用] → 題庫約 70 題、依序送出；執行前取得使用者同意；只對 demo index 執行。
- [語意快取會吃掉重複或相近的題目，rag 樣本變少] → 題庫不重複；基準量測前清空本機 Redis（會一併清掉本機 session 與佇列，本機可接受）；腳本印出各來源樣本數，rag 成功樣本不足 50 時補跑。
- [本機 Docker on Mac 的延遲不代表 Cloud Run] → 文件明確標示基準的量測環境，門檻只適用本機示範。
- [只靠真實流量時可用性錯誤率接近 0，告警無從展示] → promtool 合成資料驗證規則；另以只改設定的故障注入（例如換成無效的 LLM API key 跑一輪）讓 Prometheus UI 顯示告警觸發。
- [28 天視窗在本機資料不足] → error budget 面板以 `increase(...[28d])` 計算，資料不足時代表自開始量測以來的累計，面板說明文字註明。
- [client 在 LLM 生成期間斷線，要等下一次 yield 或取消才會被發現] → cancelled 由 `finally` 補記，接受記錄時間延後。
- [在串流開始前就失敗的請求（409、503）看不到] → 文件註明可用性 SLI 只涵蓋已開始串流的請求。
- [包裝器收到 `done` 後，路由才檢查斷線，client 可能沒收到 `done` 卻記為 success] → 接受，文件註明。
- [成功但沒有任何 `delta` 的空回答，只計入完整回應延遲，不計入首段回答延遲，兩者樣本數會不同] → 文件註明。
- [輸出 guardrail 擋下的 rag 請求與輸入防護擋下的快速回應都標為 `guardrail`，延遲分布混在一起] → 延遲 SLI 只看 `rag`；文件註明 `guardrail` 來源的延遲不具比較意義。

## Migration Plan

只影響本機開發環境。`METRICS_ENABLED` 預設關閉，Cloud Run 與 terraform 不變。回滾方式：移除 compose 的兩個服務並把 web 的 `METRICS_ENABLED` 設回 false，後端指標記錄不影響請求行為。

## Open Questions

- 可用性目標：在任務 4.1 由使用者確認。
- 延遲門檻：等基準量完，在任務 5.3 由使用者確認。
