## MODIFIED Requirements

### Requirement: Retrieval chain assembles context from answer metadata

The retrieval chain (`get_retrieval_chain`) SHALL, after the rerank stage produces the top-3 `docs`, assemble `context` (the joined answer text) and `contexts` (the list of answer texts) from each document's `answer` metadata field, falling back to empty strings when the field is absent, and SHALL assemble `sources` (a deduplicated list of truncated `"Q: <question> A: <answer>"` strings built from each document's `text`/`page_content` and `answer` fields, falling back to `doc_id` per document, preserving rerank order) from the same `docs`. The final dict returned to `chat_stream` SHALL contain `context`, `contexts`, `sources`, `docs`, `rerank_scores`, and `fusion_metadata`. This preserves the existing generation contract where the LLM receives answer text rather than question text, while giving `chat_stream` a ready-to-use source list for the answer it renders.

#### Scenario: Context built from answer metadata

- **WHEN** the rerank stage returns docs [B, D, C] whose `metadata["answer"]` values are "B ans", "D ans", "C ans"
- **THEN** the retrieval chain produces `context` equal to "B ans\n\nD ans\n\nC ans" and `contexts` equal to ["B ans", "D ans", "C ans"]

#### Scenario: Sources built alongside context from the same docs

- **WHEN** the rerank stage returns docs [B, D, C] whose `metadata["text"]` values are "Q-B", "Q-D", "Q-C" and `metadata["answer"]` values are "A-B", "A-D", "A-C"
- **THEN** the retrieval chain's returned dict contains `sources` equal to `["Q: Q-B A: A-B", "Q: Q-D A: A-D", "Q: Q-C A: A-C"]` in addition to `context` and `contexts`
