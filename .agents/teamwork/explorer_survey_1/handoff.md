# Handoff Report — Legacy ML Components, Dependencies, and Startup Hooks

**Agent:** Explorer 1 (`explorer_survey_1`)  
**Parent Conversation ID:** `662e7e24-5157-45cc-81f8-2da1031a8e3e`  
**Working Directory:** `D:\CRA\.agents\teamwork\explorer_survey_1\`  
**Target Date:** 2026-11-06T21:10:00Z  

---

## 1. Observation

1. **Repository Layout and ML Directories:**
   - Command: `Get-ChildItem -Path D:\CRA\CRA_Models -Recurse | Select-Object FullName, Length`
   - Observed files in `D:\CRA\CRA_Models`:
     - `D:\CRA\CRA_Models\bert_category_classifier\config.json` (1,291 bytes)
     - `D:\CRA\CRA_Models\bert_category_classifier\tokenizer.json` (742,346 bytes)
     - `D:\CRA\CRA_Models\bert_category_classifier\tokenizer_config.json` (366 bytes)
     - `D:\CRA\CRA_Models\xgb_resolution_time.json` (1,821,978 bytes)
     - `D:\CRA\CRA_Models\xgb_sla_breach.json` (734,152 bytes)
     - `D:\CRA\CRA_Models\tfidf_logreg.pkl` (242,713 bytes)
     - `D:\CRA\CRA_Models\label_map.json` (281 bytes)
     - `D:\CRA\CRA_Models\sample_reports.json` (10,609 bytes)
     - 6 evaluation/EDA images (`baseline_confusion_matrix.png`, `bert_confusion_matrix.png`, `bert_training_curves.png`, `eda_distributions.png`, `text_length_dist.png`, `topic_distribution.png`).
     - Total size: ~3.3 MB.
   - Observed files in `D:\CRA\app\cra`:
     - `D:\CRA\app\cra\engine.py` (16,982 bytes, 386 lines)
     - `D:\CRA\app\cra\__init__.py` (155 bytes, 3 lines)

2. **Gitignored Heavy Model Weights (`D:\CRA\.gitignore` lines 22–25):**
   ```text
   CRA_Models/bert_category_classifier/model.safetensors
   CRA_Models/faiss_index.bin
   CRA_Models/sbert_embeddings.npy
   CRA_Models/resolved_kb.csv
   ```
   Direct observation confirmed that neither `model.safetensors`, `faiss_index.bin`, `sbert_embeddings.npy`, nor `resolved_kb.csv` exist in the repository on disk.

3. **Dependency Specification (`D:\CRA\requirements.txt` lines 1–12):**
   ```text
   1: fastapi
   2: uvicorn[standard]
   3: pydantic
   4: python-dotenv
   5: httpx
   6: cryptography
   7: numpy
   8: pandas
   9: torch
   10: transformers
   11: sentence-transformers
   12: pytest
   ```

4. **Code Imports and Usage Across Repository:**
   - Search: `Get-ChildItem -Path D:\CRA\app, D:\CRA\tests -Recurse -Filter "*.py" | Select-String -Pattern "import numpy|from numpy|import pandas|from pandas|torch|transformers|sentence_transformers"`
   - Result: Heavy ML libraries (`torch`, `transformers`, `sentence_transformers`, `numpy`, `pandas`) are imported **exclusively** inside `D:\CRA\app\cra\engine.py`:
     - `numpy as np`: line 17
     - `from transformers import AutoTokenizer, AutoModelForSequenceClassification`: line 134
     - `import torch`: lines 135, 152, 216
     - `from sentence_transformers import SentenceTransformer`: line 151
     - `import pandas as pd`: line 171
   - Neither `app/database.py`, `app/main.py`, `app/simulation/troy.py`, nor any other module imports `numpy`, `pandas`, `torch`, `transformers`, or `sentence-transformers`.
   - `app/database.py` manages transactions and customer accounts using standard library `csv.DictReader` and `csv.DictWriter`.

5. **Startup Hook and Import Chain (`D:\CRA\app\main.py` lines 58–66):**
   ```python
   58: @asynccontextmanager
   59: async def lifespan(app: FastAPI):
   60:     # Startup: warm up CRA machine learning models asynchronously
   61:     try:
   62:         from app.cra import cra_engine
   63:         asyncio.create_task(asyncio.to_thread(cra_engine.warmup))
   64:     except Exception as e:
   65:         logger.warning(f"Could not launch CRA warmup task: {e}")
   66:     yield
   ```
   Inside `cra_engine.warmup` (`app/cra/engine.py:370-383`), it calls `_ensure_bert()`, `_ensure_sbert()`, and `_ensure_knowledge_base()`.

6. **API Endpoints Referencing `cra_engine`:**
   - `app/main.py:210`: Inside `/api/customer/verify`, calls `cra_engine.predict_category(query)`.
   - `app/main.py:277`: Inside `/api/resolve/action`, calls `cra_engine.predict_category(query)`.
   - `app/main.py:587`: Inside `POST /api/cra/resolve`, calls `cra_engine.generate_intelligence_report(complaint)`.
   - `app/main.py:607`: Inside `POST /api/cra/classify`, calls `cra_engine.predict_category(complaint)`.

7. **Troy Simulation Referencing `cra_engine` (`app/simulation/troy.py` lines 240–270):**
   - Line 245: `from app.cra import cra_engine`
   - Line 249: `report = await asyncio.to_thread(cra_engine.generate_intelligence_report, complaint_text)`
   - Line 270: `self.sse.broadcast("cra_report", report)`
   - Lines 542 & 560: `cra_report: report` embedded in `resolution_end` and `council_end` events.

8. **Frontend UI Streaming Contract (`D:\CRA\public\js\app.js` lines 2070–2090, 2360–2368):**
   - Line 2360: `stream.addEventListener("cra_report", (e) => { const report = JSON.parse(e.data); ... deliverPreliminaryAssessment(report); })`
   - Line 2070: `deliverPreliminaryAssessment(report)` extracts `report.predicted_category`, `report.category_confidence`, `report.recommended_team`, `report.sla.estimated_hours`, `report.sla.sla_risk`, `report.similar_cases`, and `report.extracted_entities`.
   - `public/js` files make zero HTTP calls to `/api/cra/*`. They communicate via `/api/stream` (SSE), `/api/customer/verify`, `/api/resolve/action`, `/api/next`, and `/api/config`.

9. **Test Suite Coupling:**
   - `tests/test_category_resolutions.py`: Imports `from app.cra import cra_engine` (line 2), calls `cra_engine.label_map.values()` (line 26), and `cra_engine.predict_category()` (lines 83, 180).
   - `tests/test_api_endpoints.py`: Lines 147–170 test `/api/cra/classify` and `/api/cra/resolve`.
   - Running `pytest` currently passes all 89 items (time: 16.08s).

---

## 2. Logic Chain

1. **Premise:** The acceptance criteria state that all legacy ML model files, vector indexes, training artifacts in `CRA_Models`, and ML engine code in `app/cra` must be removed, heavy ML dependencies removed from `requirements.txt`, and backend starts cleanly without any ML weights.
2. **From Observation 1 & 2:** `CRA_Models` contains 3.3 MB of tokenizer configs, XGBoost json models, pickle baselines, and evaluation plots. The heavy weights were never committed or needed because regex fallbacks were hardcoded in `engine.py`. Therefore, deleting `CRA_Models/` in its entirety carries zero risk of breaking functionality.
3. **From Observation 3 & 4:** Only `app/cra/engine.py` imports `torch`, `transformers`, `sentence-transformers`, `numpy`, and `pandas`. No other module in `app/` or `tests/` requires these libraries. Deleting `app/cra/` allows `torch`, `transformers`, `sentence-transformers`, `numpy`, and `pandas` to be removed from `requirements.txt` immediately.
4. **From Observation 5:** FastAPI's `lifespan` hook calls `cra_engine.warmup` in an asynchronous background thread. Removing this thread invocation from `app/main.py` guarantees immediate and clean application startup with zero ML baggage.
5. **From Observation 6, 7, & 8:** The frontend UI static files (`public/js/app.js`) are frozen by Requirement R4 and listen to SSE event `cra_report` containing structured fields (`predicted_category`, `category_confidence`, `recommended_team`, `sla`, `similar_cases`, `extracted_entities`). Furthermore, `/api/customer/verify` and `/api/resolve/action` use category predictions when category is not explicitly passed.
6. **Synthesis:** Instead of running PyTorch/Transformers models, the system can fulfill this schema through a lightweight non-ML triage/heuristic utility (already largely mapped in `app/database.py:get_category_complaint_context` for all 11 categories) or directly via Troy's local LLM prompt response. This satisfies R1 (ML removal), R2 (LLM resolution), R3 (database remediation), and R4 (UI preservation) simultaneously.

---

## 3. Caveats

1. **LM Studio Endpoint Default:** `ORIGINAL_REQUEST.md` specifies default `http://localhost:1234/v1` for the OpenAI-compatible local server. In existing code (`app/providers/openai_driver.py:109`), the default was set to `http://localhost:1235/v1`. The implementer must update the default to `http://localhost:1234/v1` and ensure it is configurable via `.env`.
2. **Test File Adjustments:** Removing `app/cra` will immediately cause `tests/test_category_resolutions.py` to fail unless its imports (`from app.cra import cra_engine`) are updated to the replacement non-ML triage module or LLM resolution harness.
3. **`public/` Directory Invariance:** No modifications can be made to any files under `public/`. All adjustments must be strictly backend-side.

---

## 4. Conclusion

1. **Deletions:**
   - Delete entire directory: `D:\CRA\CRA_Models\`
   - Delete entire directory: `D:\CRA\app\cra\`
   - Clean `.gitignore` lines 22–25.
2. **Dependencies (`requirements.txt`):**
   - Remove: `torch`, `transformers`, `sentence-transformers`, `numpy`, `pandas`.
   - Retain: `fastapi`, `uvicorn[standard]`, `pydantic`, `python-dotenv`, `httpx`, `cryptography`, `pytest`.
   - Add: `pytest-asyncio`.
3. **Refactoring:**
   - In `app/main.py`: Remove `cra_engine.warmup` from `lifespan()`.
   - In `app/simulation/troy.py`: Remove `cra_engine` import and replace ML pipeline call with local LLM agent execution, maintaining the `cra_report` SSE event structure.
   - Retain `/api/cra/resolve` and `/api/cra/classify` as lightweight non-ML endpoints returning compatible JSON schemas to keep test suites and API contracts green.
   - Refactor `tests/test_category_resolutions.py` to point to the new non-ML triage helper.

---

## 5. Verification Method

1. **Codebase Inspection:**
   - Verify `CRA_Models/` does not exist:
     ```powershell
     Test-Path D:\CRA\CRA_Models
     # Must return False
     ```
   - Verify `app/cra/` does not exist:
     ```powershell
     Test-Path D:\CRA\app\cra
     # Must return False
     ```
   - Verify zero occurrences of `from app.cra` or `import torch` in `app/`:
     ```powershell
     Get-ChildItem -Path D:\CRA\app -Recurse -Filter "*.py" | Select-String -Pattern "app\.cra|import torch|from transformers"
     # Must return empty
     ```

2. **Clean Startup Verification:**
   - Verify application starts cleanly without ML warmup log messages or errors:
     ```powershell
     python -c "import app.main; print('Clean import successful')"
     ```

3. **Test Suite Execution:**
   - Run pytest across the repository:
     ```powershell
     pytest
     # Must pass without loading any ML model weights
     ```
