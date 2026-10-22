## Why

PR 與 master 的變更目前只在本機跑測試，GitHub 上唯一的 workflow 是 `release/**` 的部署，所以 PR 頁面看不到測試結果，合併前也沒有自動關卡。公開 repo 作為作品展示時，缺少 CI 也不利於他人確認專案狀態。

## What Changes

- 新增 `.github/workflows/ci.yml`，在 pull request 與 push 到 master 時執行三個 job：
  - Python：Python 3.13，以 `pip install --require-hashes -r requirements.txt` 安裝（與 Dockerfile 相同的鎖定檔），執行 `python -m pytest tests`。測試不需要重排模型、jieba 大字典或網路（已在無網路、無模型的容器中驗證 671 passed、2 skipped），因此不下載模型；需要 `OPENROUTER_API_KEY` 的測試維持 skip。
  - 前端：Node 22、pnpm 11.19.0（與 Dockerfile 相同），`pnpm install --frozen-lockfile` 後執行 lint、typecheck、`vitest run`、`vite build`。
  - Terraform：`terraform fmt -check -recursive`，以 `terraform init -backend=false` 後 `terraform validate`。
- workflow 只需要 `contents: read`，不使用任何 secret；同一分支的新 push 取消舊的執行。
- README 加上 CI badge，`docs/development.md` 說明 CI 內容。

## Non-Goals

- 不改部署 workflow，不在 CI 執行 RAGAS、真實 LLM 或 Docker 映像建置。
- 不設定 branch protection（需要在 GitHub 設定，由使用者決定）。

## Capabilities

### New Capabilities

- `continuous-integration`: PR 與 master 自動執行後端測試、前端檢查與 Terraform 驗證。

### Modified Capabilities

(none)

## Impact

- Affected specs: `continuous-integration`（新增）
- Affected code:
  - New: .github/workflows/ci.yml
  - Modified: README.md, docs/development.md
