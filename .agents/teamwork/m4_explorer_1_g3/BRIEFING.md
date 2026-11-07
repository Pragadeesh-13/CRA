# BRIEFING — 2026-11-07T14:55:00Z

## Mission
Investigate replacing Python heuristic/rule-based models for complaint intake and triage with Local LLM-powered categorization and assessment via LM Studio (with deterministic mock fallback when offline), satisfying Requirement R1.

## 🔒 My Identity
- Archetype: explorer
- Roles: investigation, synthesis
- Working directory: D:\CRA
- State folder: D:\CRA\.agents\teamwork\m4_explorer_1_g3
- Original parent: 7cbf8014-b2cf-4355-9fa9-19309b49bb3d
- Milestone: M4

## 🔒 Key Constraints
- Read-only investigation — do NOT implement or modify project code directly.
- Report findings, detailed architecture proposal, prompt designs, and migration plan in handoff.md.
- Send message to caller when finished.

## Current Parent
- Conversation ID: 7cbf8014-b2cf-4355-9fa9-19309b49bb3d
- Updated: 2026-11-07T14:49:45Z

## Investigation State
- **Explored paths**:
  - `app/triage.py` (intake pipeline, `ALL_11_CATEGORIES`, `CATEGORY_TO_TEAM`, `SLA_DEFAULTS`, `CATEGORY_PRECEDENTS`, `CLASSIFICATION_PATTERNS`, `CategoryResult`, `extract_entities`, `predict_category`, `triage_complaint`)
  - `app/providers/openai_driver.py` (`OpenAiDriver`, LM Studio default port 1234, `LMSTUDIO_BASE_URL`, offline fallback mechanism to `MockDriver`)
  - `app/providers/mock_driver.py` (current mock branches: title/summary JSON, 4-stage sections; gaps identified for triage JSON generation)
  - `app/config.py` (`Settings`, `llm_provider`, `lmstudio_base_url`, `llm_timeout_ms`)
  - `app/simulation/troy.py` (`run()`, triage invocation via `asyncio.to_thread(triage_complaint)`, SSE broadcast of `cra_report`)
  - `public/js/app.js` (`cra_report` event listener, `deliverPreliminaryAssessment()` rendering fields: `predicted_category`, `category_confidence`, `recommended_team`, `sla.estimated_hours`, `sla.sla_risk`, `similar_cases`, `extracted_entities`)
  - `app/utils/json_extractor.py` (`parse_json()` stripping thought tags, markdown fences, bracket balancing)
  - `app/utils/sanitizer.py` (`sanitize_model_output()`)
  - `tests/test_category_resolutions.py`, `tests/test_api_endpoints.py`, `tests/test_e2e_troy.py`, `tests/test_challenger_m3.py`
- **Key findings**:
  1. `app/triage.py` currently uses pure Python heuristics (`CLASSIFICATION_PATTERNS` regex dictionary, `extract_entities` regexes) without any LLM invocation.
  2. `OpenAiDriver` connects to `LMSTUDIO_BASE_URL` (`http://localhost:1234/v1`) and transparently falls back to `MockDriver` upon connection failure/timeout (`_offline_detected = True`).
  3. Crucial gap in `MockDriver`: Currently only handles ticket title/summary and 4-stage resolution sections. If a triage prompt is dispatched when LM Studio is offline, `MockDriver` returns non-JSON default agent speech. `MockDriver` MUST be enhanced with a deterministic triage JSON response handler.
  4. Dual-interface requirements for `app/triage.py`: Callers invoke both sync (`triage_complaint`, `predict_category`) and async (`asyncio.to_thread` in Troy). Sync wrappers must handle running event loops safely via thread pool offloading without blocking or crashing.
  5. UI schema compatibility: `public/js/app.js:2070-2158` strictly consumes `predicted_category`, `category_confidence`, `recommended_team`, `sla.estimated_hours`, `sla.sla_risk`, `similar_cases` (similarity, count), `extracted_entities` (`references`, `dates`, `amounts`).
  6. CategoryResult must maintain inheritance from `(str, Mapping)` because existing tests and routes treat it interchangeably as a string (`cat.lower()`) and a dictionary (`pred["predicted_category"]`).
- **Unexplored areas**: None. Full analysis across all 6 prompt requirements completed.

## Key Decisions Made
- Architected replacement `app/triage.py` utilizing `OpenAiDriver` with singleton driver caching, structured JSON prompt schema, robust `parse_json()` extraction, dual sync/async APIs, and 100% deterministic fallback.
- Architected `MockDriver` triage handler covering all 11 CRA categories with exact schemas.
- Prepared comprehensive `handoff.md`.

## Artifact Index
- D:\CRA\.agents\teamwork\m4_explorer_1_g3\DISPATCH.md — incoming dispatch instructions
- D:\CRA\.agents\teamwork\m4_explorer_1_g3\BRIEFING.md — working memory and status
- D:\CRA\.agents\teamwork\m4_explorer_1_g3\progress.md — liveness progress log
- D:\CRA\.agents\teamwork\m4_explorer_1_g3\handoff.md — 5-component handoff report
