# query-routing-coverage Specification

## Purpose

Keeps the hybrid query router aligned with the knowledge base, so questions about CarbonM operations reach retrieval while general questions about other products do not.

## Requirements

### Requirement: Rule keywords cover the sample manual

The rule stage SHALL route at least 42 of the sample manual's questions, every evaluation question it does not leave `uncertain` and every frontend suggestion it does not leave `uncertain` to `rag` and SHALL route none of them to `direct`. It SHALL NOT route any of these general questions to `rag`: 什麼是機器學習？, 推薦一本好看的小說, 明天要不要帶傘？, 幫我想一個生日派對的主題, 遊戲角色怎麼練等, 台灣的首都是哪裡？, 怎麼學好英文？, 如何修改 Gmail 的個人資料？, Excel 報表怎麼做樞紐分析？, iPhone 如何開啟兩步驟驗證？, 數據分析要學什麼？, 相關係數是什麼？, 如何查證網路謠言？, 哲學的研究範疇有哪些？, 幫我寫 Python 計算相關係數, Excel 如何匯入數據？, 倉庫庫存盤查要注意什麼？, GDP 的基準年是什麼意思？, 如何煮咖啡？, 原始數據要怎麼清理？, Excel 如何批次匯入資料？, Google 帳戶沒有收到啟用信怎麼辦？, 哲學的研究範疇一般如何劃分？, 兩步驟驗證和雙因素驗證有什麼差別？, 工廠廠區的消防規定有哪些？, 論文的佐證文件要怎麼準備？、如何在 Excel 製作數據填報表？、Google 表單可以設定填報截止時間嗎？、Google 表單的填報人員怎麼設定？

#### Scenario: Sample manual coverage

- **WHEN** every question of `examples/carbonm_sample_manual.xlsx` is passed to `_rule_route`
- **THEN** at least 42 return `rag` and none return `direct`

#### Scenario: Evaluation questions and suggestions are never direct

- **WHEN** every question of `tests/rag_eval_data.json` and every suggestion in `frontend/src/App.tsx` is passed to `_rule_route`
- **THEN** none returns `direct`

#### Scenario: General questions stay out of rag

- **WHEN** each listed general question is passed to `_rule_route`
- **THEN** none returns `rag`

---
### Requirement: LLM routing prompt describes the system's features

The LLM routing prompt SHALL list the knowledge base's feature areas: account and security (login, password, personal profile, two-step verification, account activation and deactivation), roles and permissions, sites and inventory boundaries, emission sources and factors, data entry, import and review, reports, inventories and dashboards, and third-party verification. It SHALL instruct that a question routes to `rag` when it asks about carbon accounting, emissions or greenhouse-gas concepts, or asks how to operate, configure or what the system does within these feature areas without naming another product or service; and that a question naming another product, a general IT or security concept, a how-to unrelated to these areas, general knowledge or chit-chat routes to `direct`.

#### Scenario: Prompt content

- **WHEN** `_llm_route` builds its prompt
- **THEN** the prompt contains each feature area, the carbon-concept and system-operation conditions for `rag`, and the general IT or security concept exclusion

---
### Requirement: Evaluation runner checks routing

`scripts/eval_sample_manual.py` SHALL, before running RAGAS, route with `route_query` and no history every sample-manual question and every evaluation question, which SHALL be `rag`, and the listed general questions that the rule stage leaves `uncertain`, which SHALL be `direct`. A result whose reasoning marks an LLM routing error SHALL be retried once and, if it fails again, reported as a routing service error separately from misroutes. Any misroute or service error SHALL exit non-zero, printing the question, the route and the reasoning. The runner SHALL report how many sample-manual questions the rule stage decided.

#### Scenario: A question routed to direct stops the run

- **WHEN** `route_query` returns `direct` for any sample-manual or evaluation question
- **THEN** the runner exits non-zero before running RAGAS and prints that question

#### Scenario: A general question routed to rag stops the run

- **WHEN** `route_query` returns `rag` for 如何煮咖啡？
- **THEN** the runner exits non-zero before running RAGAS

#### Scenario: Routing service error is reported separately

- **WHEN** the LLM routing call fails twice for a question
- **THEN** the runner reports it as a routing service error, not a misroute, and exits non-zero
