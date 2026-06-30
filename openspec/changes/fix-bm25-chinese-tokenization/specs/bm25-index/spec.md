## ADDED Requirements

### Requirement: Tokenizer performs Chinese word segmentation

The `BM25Index` tokenizer SHALL segment Chinese text into word-level tokens using a Chinese word segmentation step (rather than splitting on whitespace), so that Chinese queries and documents that share terms produce overlapping tokens. Whitespace splitting SHALL NOT be used as the tokenizer, because Chinese text contains no inter-word spaces and would collapse each query or document into a single monolithic token, causing `BM25Okapi.get_scores` to return zero for every document. The same tokenizer SHALL be applied at build time and at search time so that corpus tokens and query tokens are comparable. The tokenizer SHALL normalize full-width characters to half-width and lowercase Latin characters before segmentation so that surface variants of the same term match.

#### Scenario: Chinese query produces non-zero scores for matching documents

- **WHEN** the index is built over Chinese Q&A documents and `search` is called with a Chinese query that shares a term with one of those documents
- **THEN** the matching document's `doc_id` appears in the results with a `bm25_score` greater than zero, ranked no later than documents that share no term with the query

##### Example: password query segments into overlapping terms

- **GIVEN** a document with `page_content` "如果我忘記我的密碼，我該怎麼辦？" indexed in the corpus
- **WHEN** `search` is called with query "忘記密碼怎麼辦？"
- **THEN** the tokenizer yields word-level tokens (including terms such as "密碼" and "忘記") that overlap the document's tokens, and that document's `bm25_score` is greater than zero (not the all-zero output produced by whitespace splitting)

#### Scenario: same tokenizer at build and search

- **WHEN** a document is tokenized during `build_from_documents` and the same text is tokenized during `search`
- **THEN** both paths produce the identical token list, so corpus tokens and query tokens are drawn from the same segmentation
