# Guardrail Context Similarity 改為 BGE Reranker 計劃

## 背景

`check_context_similarity` 原本使用 OpenAI Embedding + Cosine Similarity 比較 LLM response 與檢索回的 context answer，閾值 0.72。

**問題**：使用者問英文、知識庫是中文時，跨語言 embedding 相似度偏低，正確回答也被擋。

## 解法

改用已在專案中載入的 **BGE Reranker v2-m3 (Cross-Encoder)** 取代 OpenAI Embedding + Cosine Similarity。

原因：
- Cross-Encoder 在語意比對上優於 Embedding + Cosine Similarity
- 處理跨語言 pair 的能力遠優於 OpenAI Embedding
- 模型已在 `app.py` 以 singleton 載入，無額外成本與 startup latency

## 修改內容

### 1. `src/guardrails.py`

- 移除 `import math`（不再需要 `_cosine_similarity`）
- 移除 `_cosine_similarity()` 函式
- 將 `check_context_similarity()` 簽章從 `(response, contexts, embeddings, min_similarity=0.72)` 改為 `(response, contexts, reranker, min_similarity=0.3)`
- 內部改為使用 `reranker.score([(response, ctx) for ctx in contexts])`，取最高分

### 2. `src/app.py`

- 在 `chat_stream()` 中將 `check_context_similarity` 的第三個參數從 `embeddings` 改為 `get_reranker_model()`

### 3. `TODO.md`

- 第 3 項標記已完成

## 不影響的檔案

- `src/cache_service.py`
- `src/intent_classifier.py`
- `src/conversation_db.py`
- `src/bom_mapper.py`
- `src/build_vector_store.py`

## 閾值說明

BGE Reranker v2-m3 的 score 輸出是 raw logits 經 sigmoid 轉換，範圍約 0~1。跨語言 pair 的分佈預估在 0.3~0.6 之間，因此初始閾值設為 `0.3`。

### 上線後調校建議

從 MongoDB `conversation_db` 撈出過去被 guardrail 擋掉的對話，重新以 reranker 跑一遍比對，統計分數分佈後決定最終閾值。
