## Summary

將 `requirements.txt` 從近乎全裸的浮動版本,改為可重現的鎖定依賴,消除建置漂移與供應鏈風險。

## Motivation

`requirements.txt` 34 個套件中只有 4 個有版本限制(`sentence-transformers`、`huggingface_hub`、`ragas`、`datasets`),其餘 `fastapi`、`langchain`、`pinecone-client`、`gradio`、`redis`、`pymongo` 等全裸。後果:

- **建置不可重現**:同一 `Dockerfile` 在不同時間 build 會拉到不同版本,行為可能變化(已在 `gcp-deployment` 討論中為 `sentence-transformers` 遇過 layout 不符的版本問題)。
- **供應鏈風險**:任何上游發布 breaking change 或被接管(malicious update)都會直接進入下一次 build。
- **除錯困難**:本機重現 prod 問題時,無法對齊版本。

本 change 不改動任何執行期行為,只鎖定依賴版本,為後續 CI test gate、`app.py` 重構等 change 提供穩定基盤。

## Proposed Solution

1. 以 `pip-compile`(已隨 `pip-tools` 提供)從現有 `requirements.txt` 產生 `requirements.lock`(或直接把 `requirements.txt` 改為鎖定版,另存 `requirements.in` 為來源)。
2. 鎖定方式:
   - 來源檔 `requirements.in`:保留人類可讀的高階依賴(如 `fastapi`、`langchain`、`gradio`),不釘版或只釘大版本。
   - 鎖定檔 `requirements.txt`:由 `pip-compile` 產生,每個套件(含 transitive)釘到 `==<version>`,並附 `--hash` 供 `pip install --require-hashes` 驗證。
3. Dockerfile 改為 `pip install --no-cache-dir -r requirements.txt`(沿用),確保安裝的是鎖定版。
4. 加一個 `scripts/compile_requirements.sh`(或 Makefile target)包裝 `pip-compile --generate-hashes`,讓日後更新依賴有單一入口。
5. 在 `requirements.in` 對少數已知需範圍限制的套件(`sentence-transformers>=2.3,<6`、`huggingface_hub>=0.20,<1`、`ragas>=0.2,<0.3`、`datasets>=2.14`)保留原限制,讓 `pip-compile` 在範圍內挑最新可解析版本。
6. README 補一節「Updating dependencies」說明:改 `requirements.in` → 跑 compile script → commit 兩個檔。

## Non-Goals

- 不升級任何套件的大版本(只在現有可解析範圍內鎖定);如需升級另開 change。
- 不改用 `uv` / `poetry` / `PDM`(工具鏈統一非本次目標;`pip-tools` 已足夠且零新工具)。
- 不改 Docker base image 或 Python 版本。
- 不動 `src/` 任何執行期程式碼。
- 不接 CI 自動 PR(`dependabot` / `renovate` 為後續治理 change)。

## Alternatives Considered

- (a) 直接手動把 `requirements.txt` 每行加 `==<version>`:被否決,不涵蓋 transitive 依賴、無 hash 驗證、易遺漏。
- (b) 改用 `uv lock`:被否決,引入新工具鏈;`pip-tools` 是既有 `pip` 生態、學習成本低。
- (c) 保留全裸依賴、只在 CI 用 `pip freeze > report` 記錄:被否決,無法主動鎖定、事後記錄不等於可重現。

## Impact

- Affected specs:
  - New: `dependency-locking`(建置期契約:鎖定檔格式、hash 驗證、單一重編入口)
- Affected code:
  - New: `requirements.in`, `scripts/compile_requirements.sh`
  - Modified: `requirements.txt`(改為 pip-compile 產出的鎖定版,含 transitive + hash), `Dockerfile`(註解/安裝指令對齊鎖定檔,實質指令不變), `README.md`(新增「Updating dependencies」節)
  - Removed: 無
