## ADDED Requirements

### Requirement: Golden evaluation dataset

The system SHALL provide a curated evaluation dataset at `tests/rag_eval_data.json` consisting of an array of cases, where each case contains a `question` string and a `ground_truth` reference-answer string. The dataset SHALL contain at least 5 cases representative of user-manual questions.

#### Scenario: Dataset is loaded for evaluation

- **WHEN** the evaluation script starts
- **THEN** it reads `tests/rag_eval_data.json` and parses each case into `question` and `ground_truth` fields
- **AND** if the file is missing or empty, the script prints an error and exits non-zero without calling the RAG pipeline

##### Example: dataset case shape

- **GIVEN** one dataset entry
- **WHEN** it is parsed
- **THEN** it has the form: `{"question": "How do I reset the device?", "ground_truth": "Hold the power button for 10 seconds."}`

### Requirement: Golden set draft generation

The system SHALL provide a generator that builds a draft golden dataset from the existing knowledge-base sources. The generator SHALL read Q&A rows (each having a `question` and `answer`) from the source files under the configured data-source directory, sample a configurable number of rows, paraphrase each source `question` into a colloquial user phrasing using the existing LLM, and set `ground_truth` to the source `answer` verbatim. The generator SHALL write the result to a draft file distinct from the active dataset path so an existing reviewed `tests/rag_eval_data.json` is never overwritten without intent.

#### Scenario: Generate a draft dataset from source Q&A

- **WHEN** the generator runs with a requested sample size N
- **THEN** it loads Q&A rows from the source files, samples min(N, available) rows
- **AND** for each sampled row it produces `{question: <paraphrased>, ground_truth: <source answer verbatim>}`
- **AND** writes the array to the draft output path

#### Scenario: Ground truth is never invented

- **WHEN** a draft case is produced
- **THEN** its `ground_truth` equals the source `answer` exactly
- **AND** only the `question` wording differs from the source

#### Scenario: No source Q&A available

- **WHEN** the source files contain no parseable Q&A rows
- **THEN** the generator prints an error and exits non-zero without writing a draft file

#### Scenario: Draft requires human review before use

- **WHEN** the generator writes the draft
- **THEN** it writes to a draft path and prints a notice that the draft MUST be reviewed and copied to `tests/rag_eval_data.json` before evaluation
- **AND** it does not overwrite an existing `tests/rag_eval_data.json`

### Requirement: Pipeline sample collection

The evaluation script SHALL run each dataset question through the existing retrieval chain and generation chain without modifying their behavior, producing one evaluation sample per question containing `question`, generated `answer`, retrieved `contexts` (list of strings), and `ground_truth`.

#### Scenario: Collect a sample per question

- **WHEN** a dataset question is evaluated
- **THEN** the script invokes the existing retrieval chain to obtain the `contexts` list
- **AND** invokes the existing generation chain to obtain the `answer`
- **AND** assembles a sample with `question`, `answer`, `contexts`, and `ground_truth`

#### Scenario: Retrieval returns no context

- **WHEN** the retrieval chain returns an empty context list for a question
- **THEN** the sample is still recorded with `contexts` set to an empty list
- **AND** evaluation continues for the remaining questions

### Requirement: RAGAS metric scoring

The system SHALL score the collected samples using RAGAS with the metrics `faithfulness`, `answer_relevancy`, `context_precision`, and `context_recall`. The RAGAS judge SHALL reuse the project's existing OpenRouter LLM and OpenAI embeddings rather than introducing a new model provider.

#### Scenario: Compute metrics over the dataset

- **WHEN** all samples have been collected
- **THEN** RAGAS computes `faithfulness`, `answer_relevancy`, `context_precision`, and `context_recall` for the dataset
- **AND** the project's existing LLM (`get_llm`) and embeddings (`get_embeddings`) are wrapped and passed to RAGAS as the judge model and embeddings

#### Scenario: Missing API credentials

- **WHEN** the OpenRouter or OpenAI API key required by the judge is not configured
- **THEN** the script prints a clear error naming the missing credential and exits non-zero before scoring

### Requirement: Report output and threshold gate

The system SHALL print a per-metric summary table, write a machine-readable report to `tests/rag_eval_report.json`, and exit non-zero when any computed metric falls below its configured minimum threshold; otherwise it exits zero.

#### Scenario: All metrics pass

- **WHEN** every computed metric is greater than or equal to its threshold
- **THEN** the script writes `tests/rag_eval_report.json` with per-metric scores
- **AND** prints the summary table
- **AND** exits with status code 0

#### Scenario: A metric is below threshold

- **WHEN** at least one computed metric is below its threshold
- **THEN** the script writes the report, prints the summary table marking the failing metric
- **AND** exits with a non-zero status code

##### Example: threshold gate decision

| faithfulness | threshold | result |
| ------------ | --------- | ------ |
| 0.82         | 0.70      | pass   |
| 0.61         | 0.70      | fail (exit non-zero) |
