## ADDED Requirements

### Requirement: Intent classification incorporates recent conversation history

The system SHALL accept an optional `history` parameter on `IntentClassifier.classify()` and `IntentClassifier.is_relevant()`, and SHALL include the most recent conversation turns in the classification prompt when `history` is non-empty, so that a follow-up question is classified in the context of the topic it continues rather than in isolation. When `history` is empty or not provided, the classification prompt SHALL be unchanged from the question-only prompt.

#### Scenario: Follow-up question inherits topic relevance from prior turn

- **WHEN** `history` contains a prior turn where the user asked "門檻值設定該如何填寫？" and the assistant answered with threshold-value guidance, and the rewritten follow-up question is "您確定顯著性門檻、實質性門檻和排除門檻的建議值分別為3、5、0.5嗎？"
- **THEN** `classify()` includes that prior turn in the prompt sent to the LLM, so the classification is made with the knowledge that this question continues a CarbonM system-configuration topic

#### Scenario: No history falls back to question-only classification

- **WHEN** `history` is an empty list or omitted
- **THEN** the prompt sent to the LLM contains only the question text, identical in shape to the classifier's behavior before this change

##### Example: prompt construction with and without history

| history | Prompt contains history block |
| ------- | ------------------------------ |
| `[]` | No |
| `None` | No |
| `[{"role": "user", "content": "門檻值設定該如何填寫？"}, {"role": "assistant", "content": "..."}]` | Yes |

### Requirement: chat_stream passes conversation history to intent classification

The system SHALL pass the same `history` list that `chat_stream` uses for query rewriting and prompt formatting into `intent_classifier.classify()`, so the intent classifier and the query rewriter share the same view of the conversation.

#### Scenario: chat_stream forwards history to classify

- **WHEN** `chat_stream` calls `intent_classifier.classify(rewritten_query)` after rewriting the user's message
- **THEN** the call SHALL be `intent_classifier.classify(rewritten_query, history)`, passing the same `history` value used earlier in `rewrite_query(message, history)`
