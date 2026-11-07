# BRIEFING — 2026-11-06T21:11:00Z

## Mission
Investigate legacy ML components, models, pipelines, heavy dependencies, and import/startup chains across the repository to enable clean ML removal and headless LLM agent transition.

## 🔒 My Identity
- Archetype: explorer
- Roles: survey, legacy ML analysis, dependency analysis, startup chain analysis
- Working directory: D:\CRA\.agents\teamwork\explorer_survey_1
- Original parent: 662e7e24-5157-45cc-81f8-2da1031a8e3e
- Milestone: Survey & Analysis

## 🔒 Key Constraints
- Read-only investigation — do NOT implement
- Investigate legacy ML components, models, pipelines, and artifacts in CRA_Models, app/cra, and elsewhere in D:\CRA
- Identify heavy ML dependencies in requirements.txt vs essential backend dependencies
- Trace code references, imports, and startup initialization hooks where ML models are loaded
- Identify what needs to be deleted or refactored so the backend starts cleanly without any ML models or weights
- Deliver analysis.md and handoff.md; notify parent when finished

## Current Parent
- Conversation ID: 662e7e24-5157-45cc-81f8-2da1031a8e3e
- Updated: not yet

## Investigation State
- **Explored paths**:
  - `D:\CRA\CRA_Models` (complete file audit)
  - `D:\CRA\app\cra` (`engine.py`, `__init__.py`)
  - `D:\CRA\requirements.txt`
  - `D:\CRA\app\main.py` (startup lifespan hook, endpoints)
  - `D:\CRA\app\simulation\troy.py` (troy orchestration & SSE broadcast)
  - `D:\CRA\public\js\app.js` (SSE `cra_report` contract)
  - `D:\CRA\app\database.py` (11 category definitions, CSV ledger)
  - `D:\CRA\tests\test_category_resolutions.py` and `tests\test_api_endpoints.py`
- **Key findings**:
  - `CRA_Models/` (3.3 MB) and `app/cra/` can be completely deleted.
  - Heavy ML packages (`torch`, `transformers`, `sentence-transformers`, `numpy`, `pandas`) are ONLY imported in `app/cra/engine.py`.
  - Backend startup is burdened by `cra_engine.warmup` in `app/main.py:lifespan()`. Removing this hook yields instant, clean startup.
  - `public/js/app.js` relies on SSE event `cra_report` with specific JSON fields. A non-ML lightweight triage helper can provide these to keep `public/` untouched.
  - `LM Studio` default URL is `http://localhost:1235/v1` in `openai_driver.py`, but requirements request `http://localhost:1234/v1`.
- **Unexplored areas**: None within Explorer 1 scope.

## Key Decisions Made
- Fully documented all ML removal requirements in `analysis.md`.
- Completed 5-component handoff report in `handoff.md`.

## Artifact Index
- `D:\CRA\.agents\teamwork\explorer_survey_1\analysis.md` — Detailed ML analysis and removal recommendations
- `D:\CRA\.agents\teamwork\explorer_survey_1\handoff.md` — 5-component handoff report
- `D:\CRA\.agents\teamwork\explorer_survey_1\progress.md` — Task progress log
