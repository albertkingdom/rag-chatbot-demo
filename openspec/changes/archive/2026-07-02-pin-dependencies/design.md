## Context

`requirements.txt` 目前 34 行、幾乎全裸,只有 4 個套件有版本限制。`venv` 內已安裝 159 個套件(含 transitive)。本 change 把浮動依賴改為可重現的鎖定檔,為後續 CI test gate 與重構提供穩定基盤。不改任何執行期程式碼。

## Goals / Non-Goals

**Goals:**
- `requirements.txt` 鎖定所有 direct + transitive 套件到 `==<version>` + `--hash`。
- 保留 `requirements.in` 為人類維護的來源檔,含既有 4 個範圍限制。
- 單一腳本 `scripts/compile_requirements.sh` 重編鎖定檔。
- Dockerfile 安裝鎖定版;`pip install --require-hashes` 驗證。

**Non-Goals:**
- 不升級大版本(只在現有可解析範圍鎖定)。
- 不換工具鏈(`uv`/`poetry`);用 `pip-tools`。
- 不接 CI 自動 PR(`dependabot`/`renovate`)。
- 不改 Docker base image / Python 版本。
- 不動 `src/` 執行期程式碼。

## Decisions

### in/lock 雙檔而非單檔釘版

保留 `requirements.in`(人類編輯,高階依賴,可 unpinned 或範圍限制)+ `requirements.txt`(`pip-compile` 產出,全釘 `==` + `--hash`)。

替代方案:
- (a) 單一 `requirements.txt` 直接手動加 `==`:不涵蓋 transitive、無 hash、易遺漏,被否決。
- (b) 改 `uv lock`:引入新工具鏈,Non-Goal,被否決。

### hash 驗證啟用

鎖定檔每行附 `--hash`,Dockerfile/本機用 `pip install --require-hashes -r requirements.txt`。供應鏈被替換會 fail fast。

### 既有範圍限制保留於 in 檔

`requirements.in` 對 `sentence-transformers>=2.3,<6`、`huggingface_hub>=0.20,<1`、`ragas>=0.2,<0.3`、`datasets>=2.14` 保留原範圍;其餘 entry 不釘版,讓 `pip-compile` 挑最新可解析。這避免「鎖定 = 凍結在舊版」的誤解:鎖定的是「這次編譯的結果」,更新時重編即可在範圍內升。

### 重編腳本單一入口

`scripts/compile_requirements.sh` 包裝 `pip-compile --generate-hashes -r requirements.in --output-file requirements.txt`。文件要求貢獻者改 `requirements.in` + 跑腳本,禁止手改 `requirements.txt`。

## Implementation Contract

**行為(對外可觀察):**

- `scripts/compile_requirements.sh` 執行後,`requirements.txt` 為 `pip-compile` 產出,每行 `==<version>` + `--hash`。
- `pip install --require-hashes -r requirements.txt` 在乾淨環境成功,版本與鎖定一致;hash 不符時 non-zero 退出。
- `requirements.in` 含 4 個範圍限制;其餘 entry 可不釘版。
- Dockerfile 仍用 `pip install --no-cache-dir -r requirements.txt`(鎖定檔格式自帶 hash,`--require-hashes` 由檔案格式觸發)。

**介面 / 資料形狀:**

- New: `requirements.in`(高階來源)、`scripts/compile_requirements.sh`(重編入口)。
- Modified: `requirements.txt`(改為 pip-compile 產出,含 transitive + hash)、`Dockerfile`(安裝指令不變,必要時補註解)、`README.md`(「Updating dependencies」節)。

**失敗模式:**

- `pip-compile` 解析失敗(範圍衝突)→ 腳本 non-zero 退出,不產出半成品。
- `pip install --require-hashes` hash 不符 → 安裝中止。
- 貢獻者手改 `requirements.txt` → 下次重編會被覆蓋(文件警告)。

**驗收條件:**

- `scripts/compile_requirements.sh` 產出的 `requirements.txt` 含所有 transitive 套件、每行有 `--hash`。
- `pip install --require-hashes -r requirements.txt` 在乾淨 venv 成功。
- `requirements.in` 4 個範圍限制保留。
- `docker build .` 成功且安裝鎖定版。
- README 含「Updating dependencies」節說明 in→compile→commit 流程。

**範圍邊界:**

- In scope:`requirements.in`、`requirements.txt`、`scripts/compile_requirements.sh`、`Dockerfile` 註解/指令、`README.md` 文件。
- Out of scope:`src/` 執行期程式碼、套件大版本升級、工具鏈替換、CI 自動 PR。

## Risks / Trade-offs

- [鎖定後更新依賴需多一步重編] → 文件 + 腳本降低摩擦;範圍限制保留讓重編可在範圍內自動升小版。
- [transitive 套件數從 34 暴增到 ~159 行] → 這是正確的(本來就裝了,只是沒記錄);lock 檔就是該完整。
- [`pip-compile` 冪等性受 index 狀態影響] → 同一 index 快照下 byte-identical(modulo header 時間戳);實務上夠用。
- [`--require-hashes` 在某些离线/私镜像環境可能行為不同] → 目前用 PyPI 公网,無影響;未來私镜像另議。
