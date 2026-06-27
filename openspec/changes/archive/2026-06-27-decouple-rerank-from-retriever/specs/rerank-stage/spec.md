## ADDED Requirements

### Requirement: Rerank stage transforms top-M candidates into top-3

The system SHALL provide a rerank stage that receives a query string and a list of candidate `Document` objects (the top-M fused output of `HybridRetriever`), invokes a BGE Reranker `score` callable on `(query, page_content)` pairs, sorts the candidates by descending score, and returns exactly the Top 3 documents. The rerank stage SHALL NOT belong to `HybridRetriever` and SHALL NOT hold a reference to the BM25 index or the vector store.

#### Scenario: Top-M candidates are reranked to top-3

- **WHEN** the rerank stage receives 4 candidates [A, B, C, D] and the scorer returns scores [0.60, 0.95, 0.70, 0.80] for query "test"
- **THEN** the rerank stage returns docs ordered [B, D, C] (top 3 by score) and omits A

##### Example: four candidates, top-3 truncation

- **GIVEN** candidates: A(content="Doc A", score=0.60), B(content="Doc B", score=0.95), C(content="Doc C", score=0.70), D(content="Doc D", score=0.80)
- **WHEN** the rerank stage runs with query "test"
- **THEN** the returned `docs` list is [B, D, C] and `rerank_scores` contains three entries with ranks 1, 2, 3

### Requirement: Rerank stage output contract

The rerank stage SHALL return a dict containing `docs` (list of the top-3 `Document` objects sorted by descending reranker score) and `rerank_scores` (a list of objects each with `rank` as a 1-indexed integer, `score` as a string formatted to 4 decimal places, and `content` as the first 100 characters of the document's `page_content` followed by `...`). The rerank stage SHALL NOT assemble `context` or `contexts`; those are assembled by the retrieval chain from document metadata so that application-specific formatting (e.g. using the `answer` metadata field) stays in the chain, keeping the rerank stage a generic scoring/ranking component.

#### Scenario: Rerank stage returns docs and scores only

- **WHEN** the rerank stage selects docs [B, D, C] as the top 3
- **THEN** the returned dict contains exactly the keys `docs` and `rerank_scores`, and does NOT contain `context` or `contexts`

### Requirement: Retrieval chain assembles context from answer metadata

The retrieval chain (`get_retrieval_chain`) SHALL, after the rerank stage produces the top-3 `docs`, assemble `context` (the joined answer text) and `contexts` (the list of answer texts) from each document's `answer` metadata field, falling back to empty strings when the field is absent. The final dict returned to `chat_stream` SHALL contain `context`, `contexts`, `docs`, `rerank_scores`, and `fusion_metadata`. This preserves the existing generation contract where the LLM receives answer text rather than question text.

#### Scenario: Context built from answer metadata

- **WHEN** the rerank stage returns docs [B, D, C] whose `metadata["answer"]` values are "B ans", "D ans", "C ans"
- **THEN** the retrieval chain produces `context` equal to "B ans\n\nD ans\n\nC ans" and `contexts` equal to ["B ans", "D ans", "C ans"]

### Requirement: Empty candidates short-circuit without invoking scorer

When the rerank stage receives an empty `candidates` list, it SHALL return `{"docs": [], "rerank_scores": []}` immediately and SHALL NOT invoke the scorer callable. This prevents undefined behavior on empty input and preserves the downstream generation "I don't know" path.

#### Scenario: No candidates returned by retriever

- **WHEN** the retriever returns an empty `candidates` list (both BM25 and vector returned nothing usable)
- **THEN** the rerank stage returns empty `docs` and `rerank_scores` without calling the scorer

### Requirement: Rerank scorer is injected as a callable

The rerank stage SHALL accept the reranker as a `scorer` parameter of type `Callable[[list[tuple[str, str]]], list[float]]`. The retrieval chain SHALL inject `get_reranker_model().score` (the bound `score` method of the BGE Reranker singleton) when assembling the stage. The rerank stage SHALL NOT instantiate the reranker model itself, ensuring the singleton is shared with other consumers (e.g. guardrails).

#### Scenario: Scorer is injected by the retrieval chain

- **WHEN** `get_retrieval_chain` assembles the pipeline
- **THEN** the rerank stage is constructed with `scorer=get_reranker_model().score` and the rerank stage does not call `get_reranker_model()` internally
