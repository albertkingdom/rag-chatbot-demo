## 1. 來源檔與鎖定檔

- [x] 1.1 建立 `requirements.in`,內容為目前人類維護的高階依賴(對應現 `requirements.txt` 的 34 行 direct deps),其中 `sentence-transformers>=2.3,<6`、`huggingface_hub>=0.20,<1`、`ragas>=0.2,<0.3`、`datasets>=2.14` 保留範圍限制,其餘 entry 不釘版。驗收:`requirements.in` 存在、含 34 個 direct entry、4 個範圍限制保留、可被 `pip-compile` 解析(對應設計決策「in/lock 雙檔而非單檔釘版」「既有範圍限制保留於 in 檔」)。
- [x] 1.2 安裝 `pip-tools`(若 venv 未裝),以 `pip-compile --generate-hashes -r requirements.in --output-file requirements.txt` 產出鎖定檔。鎖定檔 SHALL 含所有 direct + transitive 套件、每行 `==<version>` + 至少一個 `--hash`。驗收:`grep -c "==" requirements.txt` ≥ 159(含 transitive);`grep -c "\-\-hash" requirements.txt` ≥ 159;`grep -E "sentence-transformers|huggingface_hub|ragas|datasets" requirements.txt` 顯示落在指定範圍內(實作 Locked dependency file with transitive pins and hashes)。

## 2. 重編腳本

- [x] 2.1 建立 `scripts/compile_requirements.sh`,內容為 `#!/usr/bin/env bash` + `set -euo pipefail` + `pip-compile --generate-hashes -r requirements.in --output-file requirements.txt`,並 `chmod +x`。執行後 SHALL 覆蓋 `requirements.txt` 為當前 `pip-compile` 產出。驗收:`bash scripts/compile_requirements.sh` 成功且 `requirements.txt` 被更新;腳本在 `pip-compile` 失敗時 non-zero 退出(實作 Single recompile entry point;對應設計決策「重編腳本單一入口」)。

## 3. Dockerfile 對齊

- [x] 3.1 確認 `Dockerfile` 的 `pip install --no-cache-dir -r requirements.txt` 能搭配帶 hash 的鎖定檔(檔案格式自帶 `--hash` 時 pip 自動啟用 hash 驗證);必要時在 `COPY requirements.txt .` 旁補一行註解說明鎖定檔由 `scripts/compile_requirements.sh` 產生、勿手改。驗收:`docker build .` 成功且安裝鎖定版(實作 Reproducible install with hash verification;對應設計決策「hash 驗證啟用」)。

## 4. 驗證

- [x] 4.1 在乾淨 venv 驗證 `pip install --require-hashes -r requirements.txt` 成功且版本與鎖定一致;故意改一個 hash 後確認 `pip install` non-zero 退出。驗收:`python -m venv /tmp/locktest && /tmp/locktest/bin/pip install --require-hashes -r requirements.txt` 成功;改 hash 後同指令 fail(手動驗證 Reproducible install with hash verification 的 tamper 情境)。

## 5. 文件

- [x] 5.1 在 `README.md` 補一節「Updating dependencies」:說明改 `requirements.in` → 跑 `scripts/compile_requirements.sh` → commit 兩個檔;禁止手改 `requirements.txt`;說明 4 個範圍限制的保留原因。驗收:content review 確認流程與禁令已記載。
