---
name: review-findings
description: "處理 code review 發現的問題：整理與驗證意見、分級（阻擋／重要／建議）、先補測試再修、用新 commit、重審、輪數上限、產出 PR 說明的 review 摘要。當 code review（內建 code-review、PR 上的人工或機器人意見、或自己發現的問題）產生了需要處理的意見，或使用者說「處理 review 意見」「修 review」「review 有問題」「review findings」時使用。不負責找問題本身。"
license: MIT
metadata:
  author: yklin
  version: "1.0"
---

# Review 發現問題的處理

專案規則（CLAUDE.md）：**code review 完成、且阻擋與重要問題都處理完之後，才能開 PR。** 這個 skill 定義「找到問題之後怎麼辦」；「怎麼找問題」不在範圍內（可用內建 `code-review`、人工審查或 PR 意見）。

## 邊界

- **不自動修。** 先整理、驗證、分級並報告，使用者同意後才改程式。需要確認時用純文字提問，不用 AskUserQuestion。**不要使用 `/code-review --fix`**（它會直接改工作樹）。
- **不 amend、不 rebase 已存在的 commit**；不 push、不開 PR、不合併，除非使用者另外明確要求。
- 逐檔 `git add <file>`，永遠不用 `git add .`、`-A`、`-u`。
- commit 訊息遵守 CLAUDE.md：不得提及任何 AI 工具，不加 `Co-Authored-By` 或「Generated with」等署名。
- **審查意見不是事實，先驗證。** 意見可能是錯的或過度推論（模型與人都會）。每一筆都標明「已驗證（讀碼、重現、執行）」或「推測」，推測的先去驗證，再決定要不要修。

## 步驟

### 1. 整理並驗證意見

把每一筆整理成：位置（`file:line`）、問題、證據、建議修法、狀態。重複或互相矛盾的合併。

- 驗證成立：進入分級。
- 驗證不成立：標「不成立」並寫明原因，**不修**，但保留在最後的摘要裡。
- 無法驗證：標「無法驗證」，由使用者決定。
- 屬於**專案既有問題、不是這次變更造成**：不混進這個 PR；記入 `architecture.md` 的已知問題，在摘要中註明。

### 2. 分級

| 等級 | 判斷標準 |
|---|---|
| 阻擋 | 正確性錯誤、資料遺失或損毀、安全問題（機密外洩、注入、權限）、違反 `openspec/specs/` 的規格、讓既有測試失敗、破壞對外介面 |
| 重要 | 實際使用中會出錯或明顯退化但有範圍限制、缺少必要測試、文件與行為不符（CLAUDE.md 的文件規則）、明顯的效能退化 |
| 建議 | 命名、風格、重構、可讀性、非必要的最佳化 |

分級有爭議時從嚴。

### 3. 報告並等使用者決定

- 阻擋與重要：必須修；或在 PR 說明寫明不修的理由並經使用者同意。
- 建議：可以延後，但**不可悄悄丟掉**：記在 PR 說明或 `architecture.md` 的已知問題。
- 結果先報告，等使用者同意再動手。

### 4. 修正（使用者同意之後）

1. **先補或改測試重現問題**，確認它先失敗。
2. 修到通過；最小改動，不夾帶無關的重構。
3. 用**新的 commit**：`fix(review): <what>`；一個問題或一組相關問題一個 commit，內文寫明對應的意見。
4. 若修正改變 `openspec/specs/` 涵蓋的行為：**不要手改已 archive 的產物**，用 `/spectra-propose` 另開 change。
5. 動到 CLAUDE.md 文件規則所列檔案時，同步更新 `architecture.md`；新功能同步 README 功能條目。

### 5. 重審

重跑完整測試；對新增的 commit 範圍（修正前的 HEAD 到現在）重新 review。重複步驟 1 到 4，直到**沒有阻擋與重要問題**。

### 6. 輪數上限

連續 3 輪仍有阻擋問題：停下，說明卡在哪裡與可能需要重新設計，交給使用者決定。

### 7. 結案摘要

通過時產出一段給 PR 說明用的摘要（PR 說明用英文，與既有 PR 一致），並回報「通過，可開 PR」；否則回報「尚不可開 PR：<原因>」。

```
Code review
- Scope: <base>..HEAD (<N> commits)
- Rounds: <n>
- Blocking: <a> found, <a> fixed
- Important: <b> found, <x> fixed, <y> deferred (reason: ...)
- Suggestions: <c> found, deferred (recorded in: ...)
- Not reproduced / rejected: <items and reasons>
```

測試狀態也要如實寫：哪些實際執行過、哪些沒有。
