---
name: create-pr
description: "依這個專案的流程開 Pull Request：開 PR 前的檢查（分支、code review 紀錄、測試、README 與 architecture.md 同步、Spectra 歸檔）、PR 標題與英文說明格式、推送與等 CI。當使用者說「開 PR」「建立 PR」「create PR」「推上去開 PR」「準備合併」時使用。需要使用者明確要求才會 push 與開 PR。"
---

# 開 Pull Request（RAG demo 專案）

只在使用者**明確要求**時 push 與開 PR。預設分支是 `master`；功能都從它開 `feature/<name>`，不直接 commit 到 master（hook 會擋）。

## 1. 開 PR 前檢查（有一項沒過就停下回報）

```bash
git status -sb                                  # 工作區乾淨、在 feature/* 分支
git log --oneline origin/master..HEAD           # commit 都是 Conventional Commits、沒有 AI 署名
git diff --stat origin/master...HEAD
```

| 項目 | 標準 |
|---|---|
| Spectra | 有對應的 change 且任務完成；實作完成後已 `spectra-archive`（或 `spectra-wrapup`），主規格已同步。純文件或設定的小變更可以沒有 change，要在 PR 說明寫明 |
| Code review | 已跑 `project-review`，阻擋與重要問題都處理完（`review-findings`）。`.claude/state/review-passed` 要對應目前 HEAD（歸檔產生的、只動 `openspec/` 的 commit 不會讓它失效），否則 `gh pr create` 會被 hook 擋下；只改文件與規格的分支不需要。**不要手動寫這個檔案繞過**，補審查。順序是：實作 → 歸檔 → review → 開 PR；`git commit` 與 `gh pr create` 不要串在同一個指令（hook 會擋） |
| 測試 | `python -m pytest tests -q`；動到前端再跑 `pnpm lint`、`pnpm build`、`pnpm exec vitest run --maxWorkers=1`；動到 `terraform/` 跑 `terraform fmt -check -recursive` 與 `terraform validate` |
| 文件 | 新功能：README 功能條目加一行；改到 `architecture.md` 列出的檔案：該檔已同步 |
| 機密 | `git diff origin/master...HEAD` 沒有金鑰、`.env`、內部網址 |

## 2. 推送

```bash
git push -u origin <branch>
```

推送失敗只在網路錯誤時重試。**不 force push**。

## 3. 建立 PR

標題：Conventional Commits 格式、英文、70 字內，例如 `ci: run backend tests, frontend checks and Terraform validation on PRs`。說明用英文（與既有 PR 一致），結構：

```markdown
## Summary
<1-3 bullets: what changed and why>

## Changes
<by area: backend / frontend / terraform / docs / specs>

## Testing
<commands actually run and their result; what was NOT run and why>

## Code review
<the summary block produced by review-findings, or "no blocking or important findings">

## Notes
<known issues recorded, follow-ups, deployment or env-var implications>
```

`Testing` 只寫實際執行過的。CI 沒涵蓋的（RAGAS、Docker smoke、真實 LLM）若沒跑，要明講。

```bash
gh pr create --base master --title "<title>" --body-file <file>
```

說明很長時先寫進暫存檔再用 `--body-file`，不要把大段文字塞進單行指令。

## 4. 之後

- 開完 PR 回報連結，並看 CI 結果（`gh pr checks`）；CI 紅燈時先讀失敗輸出再修，不盲目重跑。
- **不自動合併。** 合併由使用者決定。
- 合併後的歸檔紀錄與 release 筆記見 `docs/deployment/releases.md`。
