## Why

`docs/sli-slo.md` 已經定義了 SLI／SLO，但專案目前沒有輸出任何指標，那些數字既沒量過也無法驗證；文件裡的門檻（例如首字 3 秒）也不是依實際耗時訂的。這次在本機建立 Prometheus＋Grafana，先量出 rag 路徑的實際基準，再依基準訂 SLO 門檻與 burn rate 規則，讓 SLI／SLO 從草稿變成可以量測、可以展示的東西。

## What Changes

- 後端以 `prometheus_client` 輸出六個自訂指標：
  - 請求結果計數（依 `response_source` 與 `outcome`：success／error／cancelled），用於可用性 SLI。
  - 首段回答文字延遲與完整回應延遲兩個 histogram（依 `response_source`），bucket 範圍刻意放寬，足以容納任何候選門檻。
  - rag 檢索結果計數（依是否低信心）、檢索重試次數計數。
  - 重排耗時 histogram（依 ok／timeout／error），可算出重排降級率。
- 請求層級的三個指標在 HTTP 串流路由外層以包裝器量測，不在 chat service 的各個回應分支埋點；檢索與重排指標在各自的既有計時點記錄。
- 新增 `GET /metrics`，由 `METRICS_ENABLED` 環境變數控制，預設關閉；關閉時該路徑不輸出 Prometheus 格式。只有本機 docker-compose 的 web 服務開啟。
- docker-compose 新增 Prometheus 與 Grafana 服務；Prometheus 抓取設定、recording／alert 規則、Grafana 資料來源與 dashboard 以檔案 provisioning 進版控。
- 新增壓測腳本：以不重複的題庫依序打本機 `/api/v1/chat/stream`，拒絕在正式 index 上執行，結束時印出各 `response_source` 的樣本數。
- 以壓測結果量出 rag 路徑首段回答與完整回應的 P50／P95 基準，記錄在 `docs/sli-slo.md`，再由使用者依基準確認 SLO 門檻；門檻只寫在 Prometheus 規則與 dashboard，不寫進規格。
- 可用性目標先由使用者確認（不依賴基準），據此撰寫可用性 burn rate 告警規則（快速燒、慢速燒兩組）；延遲 SLI 規則等基準量完、門檻確認後才寫，以 `promtool test rules` 的合成資料單元測試驗證，並加進 CI。
- 更新 `docs/sli-slo.md`：「首字延遲（TTFT）」改為「首段回答延遲」並說明原因（輸出 guardrail 需要整段回答暫存後才送出），移除 instrumentator 為主的寫法；更新 `architecture.md` 描述指標與量測位置。

## Non-Goals

（寫在 design.md 的 Goals／Non-Goals。）

## Capabilities

### New Capabilities

- `service-level-metrics`: 後端輸出的請求結果、延遲、檢索與重排指標，以及每個請求恰好記錄一次結果的語意。
- `metrics-endpoint-exposure`: `/metrics` 端點的開關行為與預設關閉。
- `slo-burn-rate-rules`: Prometheus 告警規則必須通過 promtool 單元測試，且延遲 bucket 邊界涵蓋 SLO 門檻。
- `load-baseline-script`: 產生基準流量的壓測腳本的安全檢查與輸出。

### Modified Capabilities

- `continuous-integration`: 新增驗證 Prometheus 規則的 CI job。

## Impact

- Affected specs: `service-level-metrics`、`metrics-endpoint-exposure`、`slo-burn-rate-rules`、`load-baseline-script`（新增）；`continuous-integration`（修改）
- Affected code:
  - New:
    - src/observability.py
    - scripts/load_baseline.py
    - scripts/load_baseline_questions.json
    - monitoring/prometheus/prometheus.yml
    - monitoring/prometheus/rules/slo.yml
    - monitoring/prometheus/rules/slo_test.yml
    - monitoring/grafana/provisioning/datasources/prometheus.yml
    - monitoring/grafana/provisioning/dashboards/dashboards.yml
    - monitoring/grafana/dashboards/rag-slo.json
    - tests/test_observability.py
    - tests/test_load_baseline.py
  - Modified:
    - src/api/routes.py
    - src/app.py
    - src/chat_service.py
    - src/rag_pipeline.py
    - src/config.py
    - requirements.in
    - requirements.txt
    - docker-compose.yml
    - .github/workflows/ci.yml
    - docs/sli-slo.md
    - architecture.md
    - README.md
  - Removed: (none)
