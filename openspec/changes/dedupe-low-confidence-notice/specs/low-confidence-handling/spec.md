## MODIFIED Requirements

### Requirement: Low-confidence answers are labelled and carry no sources

The system SHALL treat the final kept retrieval as low confidence when grading of that result fails, meaning its top-1 rerank score is below the grading threshold or it has no top-1 score because it contains no documents or its rerank failed; a score equal to the threshold passes. For a low-confidence retrieval the system SHALL still run generation and SHALL pass a non-empty low-confidence note into the generation input. The note SHALL instruct the model to quote the retrieved material only where it explicitly answers the question; for a question about the CarbonM product itself (features, versions, prices, whether something is supported) that the material does not answer, to state that it cannot confirm rather than speculate or assert that something exists or does not exist; to use general knowledge only for general questions and then briefly; not to invent CarbonM-specific operation steps, screens or menu names; and not to repeat that the knowledge base has no answer. After generation and before the output guardrail runs, the system SHALL first remove copies of the fixed notice from the generated answer as required by "The low-confidence notice is not duplicated", and then, when the remaining answer is not empty or whitespace only, SHALL prepend a fixed notice followed by one blank line to the answer; an empty or whitespace-only answer SHALL NOT receive the notice. The notice SHALL state that the knowledge base has no directly relevant information and that the following text is general reference only. Unless the output guardrail replaces the answer, the delivered answer, the conversation history and the saved conversation record SHALL all contain the same text, the system SHALL NOT emit a sources event, and the response source reported to the client SHALL remain rag. When the output guardrail replaces a low-confidence answer, the existing guardrail behavior SHALL apply unchanged. The fixed notice SHALL NOT match any prompt-injection or personal-information detection rule.

#### Scenario: Score below threshold

- **GIVEN** a grading threshold of 0.30
- **WHEN** the final kept retrieval has top-1 score 0.29
- **THEN** the generation input contains a non-empty low-confidence note, the delivered answer starts with the fixed notice, and the event stream contains no sources event

#### Scenario: Score equal to threshold

- **GIVEN** a grading threshold of 0.30
- **WHEN** the final kept retrieval has top-1 score 0.30
- **THEN** the generation input contains an empty low-confidence note, the answer does not start with the fixed notice, and the sources event is emitted

#### Scenario: No documents retrieved

- **WHEN** both the original and the retry retrieval return no documents
- **THEN** the retrieval is treated as low confidence and generation still runs with an empty context, a non-empty low-confidence note and the fixed notice prepended

#### Scenario: Rerank failed

- **WHEN** the kept retrieval has fallback documents but no top-1 score because its rerank failed
- **THEN** the retrieval is treated as low confidence and generation runs with an empty context, a non-empty low-confidence note and the fixed notice prepended, and no sources event is emitted

#### Scenario: Empty low-confidence generation

- **WHEN** the retrieval is low confidence and the generated answer is empty, whitespace only, or becomes empty after copies of the fixed notice are removed
- **THEN** the answer does not receive the fixed notice

#### Scenario: Notice is stored with the turn

- **WHEN** a low-confidence answer is delivered
- **THEN** the history turn and the saved conversation record contain the same text, including the fixed notice, that was streamed to the client

#### Scenario: Guardrail replaces a low-confidence answer

- **WHEN** the output guardrail replaces an answer that was produced from a low-confidence retrieval
- **THEN** the guardrail message is delivered, no history turn is appended, no cache entry is written, the saved record's response source is guardrail, and its metadata contains the retrieval object with low_confidence true

#### Scenario: Callers that omit the note keep working

- **WHEN** the generation chain is invoked without a low-confidence note, as the direct-answer path and the offline evaluation script do
- **THEN** the prompt renders successfully, contains no low-confidence instruction, and is textually identical to the prompt rendered before this change

## ADDED Requirements

### Requirement: The low-confidence notice is not duplicated

Before the fixed low-confidence notice is prepended and before the output guardrail runs, the system SHALL remove from the answer text every occurrence of text identical to the fixed notice, together with the whitespace that directly follows each occurrence, and SHALL then remove leading and trailing whitespace from the result. This SHALL apply to answers generated on the rag path and on the direct path, whatever their retrieval confidence, and to answers served from the semantic cache. Text that contains no occurrence of the fixed notice SHALL be left unchanged, including its leading and trailing whitespace. Unless the output guardrail replaces the answer, a low-confidence rag answer whose remaining text is not empty SHALL contain the fixed notice exactly once, at its start, and a high-confidence rag answer, a direct answer and a cached answer SHALL NOT contain the fixed notice. A direct answer that becomes empty after the removal SHALL be handled as an empty direct answer is handled today.

#### Scenario: Model copies the notice from the conversation history

- **GIVEN** the conversation history contains an earlier low-confidence answer that starts with the fixed notice
- **WHEN** the current retrieval is low confidence and the model output starts with the fixed notice followed by an answer body
- **THEN** the delivered answer, the history turn and the saved conversation record each contain the fixed notice exactly once, followed by one blank line and the answer body

#### Scenario: Cached answer carries a copied notice

- **WHEN** a semantic cache hit returns an answer that starts with the fixed notice followed by an answer body
- **THEN** the delivered answer and the history turn contain the answer body without the fixed notice

#### Scenario: Notice removal by case

- **WHEN** an answer is processed before labelling
- **THEN** the result follows the cases below

##### Example: notice handling

| Path and confidence | Answer text before processing | Delivered answer |
| --- | --- | --- |
| rag, low confidence | NOTICE + blank line + "Body" | NOTICE + blank line + "Body" |
| rag, low confidence | "Body" | NOTICE + blank line + "Body" |
| rag, low confidence | NOTICE only | empty answer, no notice |
| rag, low confidence | NOTICE + blank line + NOTICE + blank line + "Body" | NOTICE + blank line + "Body" |
| rag, low confidence | "Body" + blank line + NOTICE | NOTICE + blank line + "Body" |
| rag, high confidence | NOTICE + blank line + "Body" | "Body" |
| rag, high confidence | "Part 1" + blank line + NOTICE + blank line + "Part 2" | "Part 1" + blank line + "Part 2" |
| direct | NOTICE + blank line + "Body" | "Body" |
| cache hit | NOTICE + blank line + "Body" | "Body" |
| rag, high confidence | "  Body  " (no notice) | "  Body  " (unchanged) |

NOTICE is the fixed low-confidence notice text.
