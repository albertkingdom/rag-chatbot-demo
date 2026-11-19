# SLI／SLO 設計筆記

> 狀態：指標、可用性 SLO 與 burn rate 告警已在**本機 docker-compose** 實作（Prometheus＋Grafana）；Cloud Run 正式環境不輸出指標。延遲 SLO 的門檻要等基準量測完成後才訂（見第二節）。指標名稱與量測位置的正式說明在 [architecture.md](../architecture.md#監控指標srcobservabilitypy)。

## 一、SLI 定義

SLI 的寫法是「好事件數 ÷ 有效事件數」，而且要從使用者角度量測。本專案最核心的使用者路徑是 `POST /api/v1/chat/stream`（[`src/api/routes.py`](../src/api/routes.py)），SLI 圍繞這條路徑定義。

| SLI | 定義（好事件 ÷ 有效事件） | 指標 | 為什麼選它 |
|---|---|---|---|
| 可用性 | 以 `done` 結束的請求 ÷（以 `done` 結束＋以 `error` 結束的請求），跨所有 `response_source` | `chat_requests_total` | 使用者最在意有沒有回應 |
| 首段回答延遲 | 第一個 `delta` 事件在門檻內送出的 rag 請求 ÷ 所有成功的 rag 請求 | `chat_first_answer_text_seconds` | 串流回應下，使用者感受的是多久開始看到回答 |
| 完整回應延遲 | `done` 事件在門檻內送出的 rag 請求 ÷ 所有成功的 rag 請求 | `chat_response_duration_seconds` | 避免開頭很快、後面卡住的情況 |
| 回答品質（RAG 專屬） | 非低信心的檢索 ÷ 所有進入檢索的 rag 請求 | `rag_retrieval_total` | 服務沒掛但檢索不到東西，對使用者而言也是失敗 |

### 為什麼不量「首字延遲（TTFT）」

原本的草案量「第一個 token」。實際上 direct 與 rag 路徑都會先把模型輸出整段收完、通過輸出 guardrail（檢查 PII 與注入內容），才分段送出 `delta`。所以第一個 token 的時間在現有架構下約等於完整生成時間，量它沒有意義；改量「第一個回答片段送出的時間」，指標也照實命名為 `chat_first_answer_text_seconds`。

也不能量「第一個任何事件」：`status` 事件幾毫秒內就會送出，SLI 會永遠達標。

### 請求結果怎麼判定

每個開始串流的請求，在路由層的包裝器（[`src/observability.py`](../src/observability.py) 的 `observe_chat_stream`）結束時恰好記錄一次：

| 情況 | outcome |
|---|---|
| 收到 `done` 事件（含 guardrail 擋下的回應，那是刻意給出的正確回答） | success |
| 收到 `error` 事件，或 chat service 丟出例外 | error |
| 以上皆非就結束（client 斷線、取消） | cancelled |

`response_source` 取自 `metadata` 事件，在它之前就結束的請求記為 `unknown`。

### 設計判斷與已知限制

- **可用性跨所有來源計算。** `metadata` 事件在回答之後才送出，錯誤路徑只送 `error`，所以出錯的請求幾乎都是 `unknown`。只算 `response_source="rag"` 會把錯誤全部漏掉，可用性永遠 100%。
- **cancelled 不列入分母。** 使用者自己關掉頁面不算服務失敗。
- **延遲 SLI 只算 rag。** `response_source` 有 `cache`、`direct`、`guardrail`、`rag`、`unknown`。快取命中只要幾十毫秒，混在一起算會讓延遲數字很好看，卻掩蓋 RAG 路徑的真實延遲。`guardrail` 混合了輸入防護（極快）與輸出防護（生成完才擋）兩種請求，它的延遲分布不具比較意義。
- **降級不等於失敗。** Reranker 逾時或失敗會退回融合排序（[`src/rag_pipeline.py`](../src/rag_pipeline.py) 的 `_rerank_or_fallback`），請求仍然成功，不計入可用性失敗，改由「重排降級率」監控。
- **串流開始前就被拒絕的請求不在量測範圍。** 例如 409（同一個 session 已有回答進行中）、503，以及驗證失敗的 401、限流的 429，都在包裝器開始之前就回應了。
- **收到 `done` 後才斷線的請求會記為 success。** 路由在送出每個事件前才檢查斷線，`done` 已被包裝器看到，client 可能沒收到。
- **空回答只計入完整回應延遲。** 成功但沒有任何 `delta` 的請求不會產生首段回答延遲，所以兩個延遲 histogram 的樣本數可能不同。

## 二、SLO

| SLI | SLO（28 天滾動視窗） | Error budget | 狀態 |
|---|---|---|---|
| 可用性 | 99.5% | 每 1 萬次請求最多 50 次失敗 | 已訂，寫在 `monitoring/prometheus/rules/slo.yml` |
| 首段回答延遲 | 95% 的 rag 請求在門檻內 | 5% 可超過門檻 | 門檻待基準量測後訂定 |
| 完整回應延遲 | 95% 的 rag 請求在門檻內 | 5% 可超過門檻 | 門檻待基準量測後訂定 |
| 回答品質 | 低信心比例 ≤ 10% | 超過時優先改善檢索 | 只在 dashboard 呈現，不寫告警（取決於題庫組成） |

### 可用性目標怎麼訂

可用性目標不是靠壓測量出來的。少量壓測（約 70 題）就算全部成功，統計上也只能說錯誤率大概低於 3 ÷ 70 ≈ 4%（rule of three），分辨不出 99.5% 與 99.9%。目標的上限受依賴服務限制：每個請求都經過 OpenRouter、OpenAI、Pinecone、Redis，假設各約 99.9%，串起來約 0.999⁴ ≈ 99.6%，所以訂略低於上限的 99.5%。目標訂太高，error budget 會一直燒完，最後沒有人在意這個 SLO。

### 延遲門檻怎麼訂

延遲門檻相反，少量樣本就看得出分布，所以先量基準再訂：

1. 清空本機 Redis 快取後，以 `scripts/load_baseline.py` 打 demo index，取得至少 50 個成功的 rag 樣本。
2. 在 Prometheus 以 `histogram_quantile` 算出壓測期間的 P50／P95，記錄在下方「基準」。
3. 門檻訂在略高於 P95 的 **bucket 邊界**（延遲 histogram 的 buckets：0.5、1、2、3、5、8、10、12、15、20、30、60 秒），SLI 比例才是精確計數而不是推估值。

原草案的 3 秒、15 秒沒有量測依據。2026-08-21 的 LangSmith 量測顯示 rag 路徑約 12 秒（之後換成託管重排，數字已過時），3 秒的門檻會一開始就違反。

### 基準

尚未量測。

### 告警（burn rate）

可用性採多視窗 burn rate，規則在 `monitoring/prometheus/rules/slo.yml`，以 `promtool test rules` 測試（`slo_test.yml`，CI 也會執行）：

| 告警 | 條件 | 嚴重度 |
|---|---|---|
| `ChatAvailabilityFastBurn` | 1 小時與 5 分鐘的錯誤率都超過預算的 14.4 倍（約 1 小時燒掉 28 天預算的 2%） | critical |
| `ChatAvailabilitySlowBurn` | 6 小時與 30 分鐘的錯誤率都超過預算的 6 倍（約 6 小時燒掉 5%） | warning |

長視窗確認問題持續存在，短視窗讓問題恢復後告警能很快解除。沒有流量的視窗不產生數值，所以不會誤觸發。本機沒有接 Alertmanager，告警只在 Prometheus 的 Alerts 頁面顯示狀態。

## 三、本機查看方式

```bash
docker compose up --build
```

| 網址 | 用途 |
|---|---|
| <http://localhost:9090> | Prometheus：Query 頁面下 PromQL；Status → Target health 確認 `rag-web` 為 UP；Alerts 看告警狀態 |
| <http://localhost:3000> | Grafana（預設 `admin`／`admin`）：Dashboards → RAG SLO |

- 只有 compose 的 `web` 服務設 `METRICS_ENABLED=true`，Prometheus 經 compose 內部網路每 15 秒抓取 `web:80/metrics`；web 沒有對外開放 `/metrics`。
- Prometheus 資料存在 Docker volume `prometheus_data`，保留 30 天；`docker compose down -v` 會一併刪除。
- 改了後端程式碼要加 `--build` 重建映像，否則 web 跑的是舊程式碼，`/metrics` 會落到前端頁面而抓取失敗。
- 刻意不使用 `prometheus-fastapi-instrumentator`：它是 ASGI middleware，總耗時含串流，但量不到首段回答的時間；串流中途失敗時狀態碼已經是 200，它判斷不出可用性。

## 四、面試常見問題與回答重點

### 基本概念

1. **SLI、SLO、SLA 有什麼差別？**
   SLI 是量測值，SLO 是內部目標，SLA 是對外承諾、違反要賠償。SLA 通常比 SLO 寬鬆，中間留緩衝。
2. **為什麼用 P95／P99，不用平均值？**
   平均值會被大量快速請求稀釋，看不到尾端少數很慢、體驗最差的使用者。
3. **Error budget 是什麼、怎麼用？**
   Error budget = 1 − SLO。預算有剩可以放心發版、做實驗；快燒完就凍結新功能、優先處理穩定性。開發與維運用同一個數字溝通。

### 應用到本專案

4. **你的系統 SLI 怎麼定？**
   用第一節的表格回答，強調三個設計判斷：因為輸出 guardrail 會先暫存整段回答，延遲量首段回答而不是首字；延遲依 `response_source` 只算 rag；可用性跨所有來源，因為錯誤請求拿不到來源標籤。
5. **外部 LLM API 變慢，算不算你的 SLO 違反？**
   算，使用者不在乎是誰慢。因應方式：timeout、fallback（reranker 已實作）、快取；另外記錄依賴服務延遲以利定位。
6. **快取命中會怎麼扭曲指標？**
   命中的回應極快，混算會拉低 P95。需分開量測，或延遲 SLI 只算 RAG 路徑。
7. **SLO 門檻怎麼訂？**
   延遲先量基準，訂在略高於 P95 的 bucket 邊界；可用性看依賴服務的上限與能接受的失敗量，少量壓測分辨不出 99.5% 和 99.9%。

### 告警

8. **延遲超過門檻就告警，有什麼問題？**
   容易誤報。應改用 burn rate：error budget 燒得太快才告警。本專案用 1h/5m 超過 14.4 倍、6h/30m 超過 6 倍兩組。
9. **SLO 違反了怎麼處理？**
   先止血（回滾、切換 fallback）→ 查根因 → 寫事後檢討（postmortem）→ 必要時調整 SLO 或架構。

### 故障排查

10. **P95 突然從 10 秒變 30 秒，怎麼查？**
    先確認範圍：全部請求都慢，還是只有某種來源？最近有沒有部署或變更？接著分層看：重排延遲與降級率、檢索重試率、LangSmith 的各階段 trace。
    本專案特別要注意 retry 機制：檢索品質變差會觸發 query 改寫重試（`chat_service.py` 的 `_retrieve_best`），一個請求可能多呼叫好幾次 LLM，延遲自然暴增；`rag_retrieval_retries_total` 可以直接看出來。

### 實務經驗

11. **你實際導入過 SLO 嗎？**
    誠實回答：正式環境沒有導入過；在個人專案中設計 SLI，以 Prometheus 實作指標、可用性 SLO 與 burn rate 告警，並用 promtool 單元測試告警規則（本機環境）。
