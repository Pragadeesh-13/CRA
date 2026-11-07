# Dispatch for Explorer 2 (Backend, Troy LLM Engine, & UI Contracts)

Working directory: D:\CRA\.agents\teamwork\explorer_survey_2\
Original request file: D:\CRA\.agents\teamwork\ORIGINAL_REQUEST.md

## Task
Investigate the backend architecture, Troy resolution engine, API endpoints, streaming protocols, and UI contracts.
- Map backend framework, entrypoints, routes, API contracts, streaming logs/reasoning, and ledger endpoints.
- Inspect `public/` files (read-only) to see exactly what endpoints, JSON structures, SSE/events, and data schemas the frontend expects.
- Design architecture for the local LLM engine (Troy) connecting to OpenAI-compatible endpoint (LM Studio, `http://localhost:1234/v1`, configurable via env vars) and mock/stub fallback mode when offline.
- Determine how Troy autonomously decides between direct customer response vs database tool invocation.
- Write your findings to `analysis.md` and `handoff.md` in your working directory.


## 2026-11-06T21:02:55Z
From: 662e7e24-5157-45cc-81f8-2da1031a8e3e
You are Explorer 2 (Survey Explorer Backend).
Your working directory is D:\CRA\.agents\teamwork\explorer_survey_2\
Read your task in D:\CRA\.agents\teamwork\explorer_survey_2\DISPATCH.md and D:\CRA\.agents\teamwork\ORIGINAL_REQUEST.md.

Investigate:
1. Backend architecture, web framework, routes, API endpoints, complaint handling flow.
2. UI contracts: read public/ static files (without modifying them) to document exact API endpoints, request/response formats, SSE/WebSocket streaming formats, customer verification/ledger cards expected by the frontend.
3. Architecture of Troy resolution engine: connecting to an OpenAI-compatible endpoint (LM Studio, default http://localhost:1234/v1, env-configurable) and automated mock/stub fallback when server is offline.
4. Decision flow for Troy: analyzing complaint to choose between direct response vs invoking database tools.

Write your detailed findings to:
- D:\CRA\.agents\teamwork\explorer_survey_2\analysis.md
- D:\CRA\.agents\teamwork\explorer_survey_2\handoff.md
Update progress.md in your directory as you work.
When finished, send a completion message to parent with summary and file paths.
