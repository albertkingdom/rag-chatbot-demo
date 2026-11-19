## Purpose

Defines when the router rewrites a question using the conversation history, so that self-contained questions keep their original wording for semantic-cache lookup and retrieval, and only real follow-ups are rewritten.

## ADDED Requirements

### Requirement: Self-contained questions keep their original wording

When the conversation history contains at least one user turn followed by an assistant turn, the router SHALL determine, on both the rule-routed path and the LLM-routed path, whether the question needs that history to be understood (a follow-up). A history without such a pair SHALL be treated as empty. When the question is judged not to be a follow-up, the rewritten query returned by the router SHALL be exactly the user's original question, regardless of the text the model produced. When the question is judged a follow-up, the rewritten query SHALL be the model's rewritten question with leading and trailing whitespace removed, or the original question when that text is empty. When the conversation history is empty, the router SHALL NOT call the language model to rewrite on the rule-routed path, and on both paths SHALL return the original question as the rewritten query. When the follow-up judgement cannot be obtained (the call fails, returns nothing, or omits the judgement), the rule-routed path SHALL return the original question, and the LLM-routed path SHALL keep its existing fallback of the direct route with the original question.

#### Scenario: Complete question with history

- **GIVEN** a non-empty conversation history about passwords
- **WHEN** the user asks "忘記密碼怎麼辦？" and the model judges it not a follow-up while producing "忘記CarbonM的密碼怎麼辦？"
- **THEN** the rewritten query is "忘記密碼怎麼辦？"

#### Scenario: Follow-up question with history

- **GIVEN** a non-empty conversation history about emission factors
- **WHEN** the user asks "那適用年度呢？" and the model judges it a follow-up producing "CarbonM 係數庫的排放係數適用哪一個年度？"
- **THEN** the rewritten query is "CarbonM 係數庫的排放係數適用哪一個年度？"

#### Scenario: LLM-routed question without history

- **WHEN** a question the rule stage cannot route is asked with an empty conversation history and the model returns a route with a different rewritten text
- **THEN** the route is the model's route and the rewritten query is the original question

#### Scenario: Rewrite outcomes by case

- **WHEN** the router returns a rewritten query
- **THEN** the result follows the cases below

##### Example: rewrite outcomes

| History | Model judgement | Model text | Rewritten query |
| --- | --- | --- | --- |
| empty, rule-routed | (not called) | — | original question |
| non-empty | not a follow-up | different text | original question |
| non-empty | follow-up | "  Standalone question  " | "Standalone question" |
| non-empty | follow-up | "" | original question |
| non-empty, rule-routed | call fails, returns nothing, or omits the judgement | — | original question |
| non-empty, LLM-routed | call fails, returns nothing, or omits the judgement | — | original question, route direct |

### Requirement: Rule-routed questions are not routed again by the language model

When the rule stage has decided the route, the router SHALL use the language model only for the follow-up judgement and rewrite, and SHALL NOT ask it to choose a route.

#### Scenario: Rule-routed question with history

- **WHEN** a question that the rule stage routes to rag is asked with a non-empty conversation history
- **THEN** the route is rag and the language model is not asked for a routing decision
