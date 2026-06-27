## 1. 依賴與資料集

- [x] 1.1 在 `requirements.txt` 加入 `ragas` 與 `datasets`（`ragas` 釘選已知可用的版本範圍）。驗證：於 venv 執行 `pip install -r requirements.txt` 成功，且 `python -c "import ragas, datasets"` 無錯誤。
- [x] 1.2 （對應需求：Golden set draft generation）建立 `tests/generate_rag_eval_data.py`，可用 `python -m tests.generate_rag_eval_data [--n N] [--out PATH]` 執行；複用 `src/build_vector_store.py` 的 Q&A 抽取邏輯讀取 `DATA_SOURCE_DIR` 下來源，抽樣 min(N, 可用筆數) 列，對每列以 `get_llm()` 將 `question` 改寫成口語問法、`ground_truth` 取來源 `answer` 原文，寫出至草稿路徑（預設 `tests/rag_eval_data.draft.json`）。來源無可解析 Q&A 時印錯誤並以非零碼結束、不寫檔；已存在 `tests/rag_eval_data.json` 時不覆寫並印出需人工審核後再 promote 的提示。驗證：在有來源檔的環境執行，產生草稿檔且每筆 `ground_truth` 與來源 `answer` 完全相同；於無來源時執行確認結束碼為非零且未產生草稿。
- [x] 1.3 （對應需求：Golden evaluation dataset）由 1.2 草稿經人工審核後，整理出 `tests/rag_eval_data.json`，內容為陣列，至少 5 筆 `{"question": str, "ground_truth": str}`，題目為具代表性的使用手冊口語問法。驗證：`python -c "import json; d=json.load(open('tests/rag_eval_data.json')); assert len(d)>=5 and all('question' in c and 'ground_truth' in c for c in d)"` 通過。

## 2. 評估腳本骨架與樣本收集

- [x] 2.1 建立 `tests/evaluate_rag.py`，可用 `python -m tests.evaluate_rag` 執行；載入 `tests/rag_eval_data.json`，若檔案不存在或為空則印出錯誤並以非零碼結束（不呼叫 pipeline）。驗證：暫時改名資料集後執行，程式印出錯誤訊息且 `echo $?` 為非零。
- [x] 2.2 在腳本啟動時檢查 `OPENROUTER_API_KEY` 與 `OPENAI_API_KEY`，缺少時印出指名缺少哪個憑證的錯誤並以非零碼結束（早於任何評分）。驗證：暫時 unset 其中一個變數執行，錯誤訊息點名該憑證且結束碼為非零。
- [x] 2.3 （對應需求：Pipeline sample collection）對每筆 question，呼叫 `src.app` 既有的 `get_retrieval_chain()` 取得 `contexts`（list[str]），呼叫 `get_generation_chain()` 以 `{"context", "question", "history": ""}` 取得 `answer`，組成樣本 `{question, answer, contexts, ground_truth}`。當檢索回傳空結果時，`contexts` 記為 `[]` 並繼續處理其餘題目。驗證：以 1 筆資料試跑，印出收集到的樣本可見非空 `answer` 與 `contexts` 欄位。

## 3. RAGAS 評分與門檻閘門

- [x] 3.1 （對應需求：RAGAS metric scoring）將收集到的樣本轉為 RAGAS 可用的 dataset，使用 `LangchainLLMWrapper(get_llm())` 與 `LangchainEmbeddingsWrapper(get_embeddings())` 作為 judge，呼叫 `ragas.evaluate(...)` 計算 `faithfulness`、`answer_relevancy`、`context_precision`、`context_recall`。驗證：完整跑一次資料集，stdout 出現四個指標皆有數值。
- [x] 3.2 （對應需求：Report output and threshold gate）定義 `THRESHOLDS` 常數（每指標預設 0.70），印出對齊的逐指標摘要表並標示未達標者；寫出 `tests/rag_eval_report.json`（含 `metrics`、`thresholds`、`passed`、`num_cases`）。所有指標達標時結束碼為 0，任一指標低於門檻時為非零。驗證：以正常資料跑一次確認結束碼 0 且報告檔產生；將某指標門檻臨時設為 1.0 重跑，確認摘要表標示失敗且結束碼為非零。

## 4. 文件與收尾

- [x] 4.1 在 `README.md`（或 `TODO.md` 第 7 項）補上一段如何執行 `python -m tests.evaluate_rag` 與指標意義的說明，並說明 evaluation timing（RAGAS 為離線、事後評估：先 build 知識庫 → 生成 golden set 草稿 → 人工審核 → 跑 pipeline 收樣本 → RAGAS 評分 → 門檻閘門，且不在 live 請求路徑執行），並標記 TODO 第 7 項為 done。同時補上「以 baseline 對照判讀分數」的指引，至少涵蓋：(a) 改 pipeline 前先跑一次記下 baseline 分數，改後再跑做比較；(b) golden set（`tests/rag_eval_data.json`）在比較期間必須維持同一份，否則分數變化無法歸因；(c) RAGAS 以 LLM 為 judge 屬非確定性，分數有抖動，應看趨勢而非追兩三個百分點，必要時同版本跑 2–3 次取平均；(d) 若更新了知識庫來源（`uploaded_files/`）內容，須重新生成並審核 golden set，不可沿用舊的。驗證：文件含執行指令、四個指標名稱、評估順序說明，以及上述 (a)–(d) baseline 對照判讀指引；`TODO.md` 第 7 項標示為 done。
