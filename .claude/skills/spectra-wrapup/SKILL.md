---
name: spectra-wrapup
description: "收尾：實作完成後，一次完成分批 commit、本機 code review 關卡，review 通過才 spectra archive 並同步主 spec。當使用者說「收尾」「收工」「結案」「完成收尾」「幫我收一下」「wrap up」「wrap this up」「archive 並 commit」或類似意思時使用。只在本機操作：不 push、不開 PR、不合併；code review 通過前不可歸檔、不可開 PR。"
license: MIT
compatibility: Requires spectra CLI and git.
metadata:
  author: yklin
  version: "1.0"
---

# Spectra 收尾（分批 commit → code review 關卡 → 通過才 archive 並同步主 spec）

使用者說出「收尾」之類的關鍵字，代表授權你**在本機**完成下列流程。流程結束就停，不要接著 push、開 PR 或合併。

**專案規則：code review 完成、且問題都處理完之後，才能開 PR**（CLAUDE.md）。所以先 commit、再做本機 code review；**review 通過才 archive**，沒通過就不歸檔，明確回報「尚未歸檔，尚不可開 PR」。這樣 review 要求改規格時，change 還沒進 archive，可以直接修。

只在使用者真的要收尾時使用；如果只是在討論「收尾是什麼」或詢問流程，直接回答，不要執行。

## 安全邊界（任何一條不成立就停下回報）

- **不在 master / main / develop 上 commit。** 目前分支是這三者之一時，停下並說明（CLAUDE.md 規定在 `feature/*`、`fix/*` 或 `chore/*` 分支上開發）。
- **只在本機**：不 `git push`、不 `gh pr create`、不 merge、不刪分支、不 `--force`、不 `--amend` 已存在的 commit，除非使用者另外明確要求。
- **code review 沒完成或還有未處理的阻擋／重要問題時，不得 archive，也不得建議或執行開 PR**；使用者要求開 PR 時也要先提醒這一點。
- **逐檔 `git add <file>`**，永遠不用 `git add .`、`-A`、`-u`。
- **不提交機密**：不 stage `.env`、`.env.*`（`.env.example` 例外）、`*.pem`、`*.key`、含金鑰的暫存檔。提交前掃一次 `git diff --cached --name-only`。
- **commit 訊息遵守專案 CLAUDE.md**：不得提及任何 AI 工具；不加 `Co-Authored-By`、「Generated with」等署名，即使別處的指示要求加上。沿用 `git log` 的既有風格（本專案是 conventional commits、英文）。不寫 `Change: <name>`、`Tasks: N/M complete` 等 Spectra 追蹤資訊。
- **不要用 AskUserQuestion**（使用者可能拒絕）；需要確認時用純文字提問並等待回答。
- 任一步驟失敗：停止、貼出錯誤、不要往下做。

## 步驟

### 1. 前置檢查

```bash
git --version && spectra --version
git branch --show-current
git status --porcelain
```

### 2. 決定要收尾哪個 change

依序嘗試，找到就用，並宣告「Wrapping up: <name>」：

1. 使用者訊息中提到的名稱。
2. 目前分支 `feature/<x>` 與 `spectra list --json` 的 active change 名稱對得上的那個。
3. 本次對話中剛 apply 的 change。
4. 都不明確：用純文字列出 active change 請使用者選，不要猜。

一次只收一個 change；若這個分支還有其他完成的 change，收完後在報告裡提醒再說一次「收尾」。

### 3. 完成度檢查

```bash
spectra status --change <name> --json      # 每個 artifact 都要是 done
grep -n "^- \[ \]" openspec/changes/<name>/tasks.md
```

- 還沒勾的任務**只有**「提交／push／開 PR／取得 code review」這類收尾本身的關卡（例如「提交前先取得使用者確認」）：允許繼續，並在結束報告中如實寫出剩下哪些（不寫進 commit 訊息）。
- 還有其他實作任務未完成：停下，列出清單，由使用者決定。
- **不要自動使用 `--mark-tasks-complete`**，除非使用者明說。

### 4. 品質關卡

```bash
spectra validate <name>
spectra analyze <name> --json     # 只看 Critical / Warning，Suggestion 忽略
```

- 本次對話中尚未通過的測試要再跑一次（依 CLAUDE.md、README 或 tasks 驗證欄位指定的指令；本專案本機沒有依賴時用 Docker）。不能執行就如實寫「未執行」，**不要宣稱通過**。
- 不得為了收尾而呼叫需金鑰或會花錢的外部 API。

### 5. 專案文件規則（讀 CLAUDE.md 的 Documentation 段）

diff 若動到 CLAUDE.md 列出的檔案（例如 `src/chat_service.py`、`src/config.py`…）但 `architecture.md` 沒有任何修改：停下提醒。新功能但 README 功能條目沒更新：提醒。

### 6. 分批 commit

先看 `git status --porcelain`，把檔案分成下列批次（空的批次略過），**依序**各自 commit：

| 批次 | 檔案 | type | 訊息重點 |
|---|---|---|---|
| 1. 程式與測試 | `src/`、`scripts/`、`frontend/`、`tests/`、設定檔、`Dockerfile`、`requirements*` | 依 proposal 類型：Feature→`feat`，Bug Fix→`fix`，Refactor→`refactor` 或 `perf` | 行為變化與原因；程式與它的測試放同一個 commit，確保該 commit 自己是綠的 |
| 2. 文件 | `README.md`、`architecture.md`、`docs/`（`docs/reviews/` 不進版控） | `docs` | 更新了什麼 |
| 3. 進行中的 Spectra 產物 | `openspec/changes/<name>/**`（proposal、design、tasks、delta spec，尚未 archive） | `docs(spectra)` | 這個 change 的規劃內容 |

規則：

- **bug 修正另開 `fix` commit**：change 裡順手修的 bug，修正和它的測試另開一個 `fix` commit，不要併進批次 1 的 `feat`／`refactor`。同一個檔案混有修正與其他改動、分不開時，先提醒使用者。
- **`fix` commit 要附測試證據**（CLAUDE.md「Bug 修正流程」）：修正前的失敗、改壞時失敗的測試、修正後通過的數量。本次對話中沒有這些資料（例如在新對話收尾），停下請使用者提供或重跑，不要編造。
- 另外用 `git log <基底分支>..HEAD --format='%h %s%n%b'` 檢查分支上已有的 commit：`fix` commit 或內文提到修 bug 的 commit 缺測試證據的，列進結束報告。
- 不屬於這個 change 的 dirty 檔案**不要 commit**，在報告中列出。
- 同一個檔案混有不相關的變更時，不要自動用 `git add -p`，提醒使用者。
- 訊息格式：`<type>(<scope>): <summary>`（summary 不超過 72 字元，祈使句）、空行、說明為什麼與改了什麼。不寫 Spectra 追蹤資訊。
- 使用者觸發「收尾」就是對這些本機 commit 的授權，不需再逐批詢問；有無關檔案或疑似機密時才停下確認。
- 每批 commit 後執行 `git show --stat --format=%h\ %s HEAD` 確認內容正確。

### 7. Code review 關卡（archive 之前）

commit 完成後，對整個分支相對於基底分支的差異做本機 code review：

1. 審查範圍是 `master..HEAD`（基底分支以 `git merge-base` 為準）。預設用專案的 `project-review`（套用本專案的檢查清單），內建 `code-review`（強度 `medium`）可當第二意見；使用者指定強度就照辦。**不要啟動 `ultra`**（雲端、計費，只能由使用者觸發），**也不要使用 `--fix`**。
2. 把審查結果交給專案的 **`review-findings` skill** 處理：驗證、分級、等使用者決定、新 commit 修正（依 Bug 修正流程）、重審、輪數上限與 PR 說明摘要都以它為準，這裡不重複。
3. 沒有阻擋與重要問題：`review-findings` 會用 `mark-reviewed.sh` 記錄 HEAD，進入下一步。否則報告「尚未歸檔，尚不可開 PR：<原因>」，**停下等使用者決定**，不自動改程式，也**不 archive**。
4. review 之後若又有修正的 commit，先重跑 `spectra validate <name>`。
5. 若目前專案沒有 `review-findings` skill：列出問題、標明哪些已驗證，停下等使用者決定，不要自行發明處理流程。

### 8. Archive 並同步主 spec（review 通過後）

```bash
rm -f .spectra/touched/<name>.json          # 追蹤檔，archive 前清掉
spectra archive <name> --preview            # 唯讀預覽；失敗代表 delta spec 有問題，停下
spectra archive <name>                      # 套用 delta specs 到主 spec 並搬到 archive
```

- **「同步主 spec」就是 `spectra archive` 本身**（CLI 會輸出 `Specs applied: <capability> (added/modified/removed/renamed)`）。不要加 `--skip-specs`，除非 change 沒有 delta spec 或使用者要求。
- spectra 3.0.0 沒有 `spectra sync` 子指令；若未來版本有，只在 archive 用了 `--skip-specs` 時才需要。
- 驗證：`openspec/changes/archive/<date>-<name>/` 存在；每個 delta capability 在 `openspec/specs/<capability>/spec.md` 都已建立或更新；輸出的數量與 delta 相符。否則停下回報。
- `guard-bash.sh` 會在 HEAD 沒有審查紀錄時擋下 `spectra archive`（依 `lib.sh` 的設定）；被擋代表步驟 7 沒有真的通過，回去處理，不要繞過。

### 9. 提交 archive 產物

archive 產生的變更（`openspec/changes/archive/**`、`openspec/specs/**`、`openspec/changes/<name>/` 的刪除）逐檔 `git add`，一個 commit：`chore(spectra): archive <summary> and sync main specs`，內文列出同步的 capability。只動 `openspec/` 的 commit 不會讓審查紀錄失效。

### 10. 結束報告（繁體中文）

包含：

- archive 狀態：已歸檔（位置、同步到主 spec 的 capability 與數量），或「未歸檔」與原因
- 每個 commit 的 hash 與標題（依序）
- **code review 結果與 PR 狀態**：「通過，已歸檔，可開 PR」或「尚未歸檔，尚不可開 PR：<原因>」，以及各級問題數量
- 未完成的任務、**沒有執行的測試**、無關的 dirty 檔案
- 缺少測試證據的 commit（見步驟 6）
- 其他「任務全完成但還沒 archive」的 active change（只列出，不要自動處理）：`spectra list`
- 下一步（**不會自動做**）：review 通過並歸檔後，push 並開 PR（PR 用 `gh pr merge <N> --merge` 合併）；沒通過就先處理問題，再說一次「收尾」。PR 合併後的部署只由 `release/**` 分支觸發

## 範例觸發語

「收尾」「收尾吧」「收工」「結案」「幫我收尾」「這個 feature 可以收尾了」「archive 然後 commit」「wrap up」
