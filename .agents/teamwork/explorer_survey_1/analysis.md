# Technical Analysis: Legacy Machine Learning Components, Dependencies, and Startup Hooks

**Author:** Explorer 1 (Survey Explorer ML)  
**Date:** 2026-11-06T21:08:00Z  
**Target Repository:** `D:\CRA`  
**Purpose:** Comprehensive survey and decoupling plan to remove all legacy ML models, pipelines, and heavy dependencies, ensuring clean backend startup and headless local LLM operation while preserving UI contracts.

---

## Executive Summary

The CRA repository currently contains a legacy machine learning layer housed in `CRA_Models/` and `app/cra/engine.py`. This layer originally incorporated a fine-tuned DistilBERT classifier, SBERT/MiniLM vector search embeddings, and XGBoost models. 
However, an empirical inspection reveals that the actual model weights (`model.safetensors`, `faiss_index.bin`, `sbert_embeddings.npy`) are already gitignored or absent, causing the engine to operate almost exclusively via fallback regexes and keyword heuristics while incurring heavy imports (`torch`, `transformers`, `sentence-transformers`, `numpy`, `pandas`) and blocking asynchronous warmup tasks during FastAPI startup.

Removing `CRA_Models/`, deleting `app/cra/`, and pruning heavy ML packages from `requirements.txt` will reduce the footprint by hundreds of megabytes of Python binaries, eliminate startup delays and memory overhead, and cleanly pave the way for Troy's local LLM agent architecture. Because the frontend UI (`public/js/app.js`) listens to SSE events (`cra_report`) and specific API endpoints (`/api/cra/resolve`, `/api/cra/classify`, `/api/customer/verify`), we must maintain an exact, lightweight, non-ML API compatibility facade so that static files in `public/` remain 100% untouched.

---

## 1. Inventory of Legacy ML Artifacts and Models

### 1.1 Files in `CRA_Models/`
All files in `D:\CRA\CRA_Models` are legacy ML artifacts, training plots, and serializations:

| File / Directory | Size | Purpose / Contents | Recommendation |
|---|---|---|---|
| `CRA_Models/bert_category_classifier/` | Directory | DistilBERT tokenizer configs (`config.json`, `tokenizer.json`, `tokenizer_config.json`) | **Delete entirely** |
| `CRA_Models/xgb_resolution_time.json` | 1,821,978 B | Exported XGBoost model for resolution time estimation | **Delete** |
| `CRA_Models/xgb_sla_breach.json` | 734,152 B | Exported XGBoost model for SLA breach prediction | **Delete** |
| `CRA_Models/tfidf_logreg.pkl` | 242,713 B | Scikit-learn TF-IDF + LogisticRegression baseline model | **Delete** |
| `CRA_Models/label_map.json` | 281 B | Mapping of 11 integer labels (0–10) to category names | **Delete** (or embed category list in constants) |
| `CRA_Models/sample_reports.json` | 10,609 B | Sample output fixtures from training runs | **Delete** |
| `CRA_Models/baseline_confusion_matrix.png` | 166,700 B | Evaluation visual artifact | **Delete** |
| `CRA_Models/bert_confusion_matrix.png` | 165,954 B | Evaluation visual artifact | **Delete** |
| `CRA_Models/bert_training_curves.png` | 89,144 B | Training loss and accuracy plot | **Delete** |
| `CRA_Models/eda_distributions.png` | 99,989 B | Exploratory data analysis plot | **Delete** |
| `CRA_Models/text_length_dist.png` | 31,875 B | Text length distribution plot | **Delete** |
| `CRA_Models/topic_distribution.png` | 104,556 B | Topic modeling visual plot | **Delete** |

*Note on Gitignored Heavy Artifacts:*  
In `D:\CRA\.gitignore` (lines 22–25), the following files are excluded from git:
- `CRA_Models/bert_category_classifier/model.safetensors`
- `CRA_Models/faiss_index.bin`
- `CRA_Models/sbert_embeddings.npy`
- `CRA_Models/resolved_kb.csv`

The entire `D:\CRA\CRA_Models/` folder should be deleted from the filesystem and repository.

### 1.2 Code in `app/cra/`
The directory `D:\CRA\app\cra` contains the legacy inference engine:
- `app/cra/__init__.py`: Exports `CRAEngine`, `cra_engine`, `clean_bert`, and `extract_entities`.
- `app/cra/engine.py` (386 lines, 16.9 KB):
  - Imports: `numpy as np`, `re`, `json`, `os`, `logging`, and lazy imports of `transformers`, `torch`, `sentence_transformers`, `pandas`.
  - Class `CRAEngine`:
    - `_load_label_map()`: Reads `CRA_Models/label_map.json`.
    - `_ensure_bert()`: Attempts to load DistilBERT with PyTorch.
    - `_ensure_sbert()`: Downloads/loads SentenceTransformer `all-MiniLM-L6-v2`.
    - `_ensure_knowledge_base()`: Attempts to load `resolved_kb.csv` and mmap `sbert_embeddings.npy`.
    - `predict_category(text)`: Performs regex heuristics, then falls back to DistilBERT.
    - `find_similar_cases(text, top_k)`: Runs dot-product semantic search over embeddings.
    - `recommend_team(text, category, similar_cases)`: Votes on team assignment.
    - `estimate_sla_and_time(category, similar_cases)`: Computes SLA estimates and fallback defaults.
    - `generate_intelligence_report(complaint_text)`: Runs the multi-step legacy pipeline.
    - `warmup()`: Pre-loads all models in an async background thread.

The entire `app/cra/` module must be deleted and replaced with a zero-ML architecture.

---

## 2. Dependency Audit: `requirements.txt`

### 2.1 Current `requirements.txt`
```text
fastapi
uvicorn[standard]
pydantic
python-dotenv
httpx
cryptography
numpy
pandas
torch
transformers
sentence-transformers
pytest
```

### 2.2 Classification & Utilization Audit

| Package | Classification | Repository Utilization | Action |
|---|---|---|---|
| `fastapi` | **Essential Backend** | Core web framework in `app/main.py` | **Retain** |
| `uvicorn[standard]` | **Essential Backend** | ASGI server used in `run.py` | **Retain** |
| `pydantic` | **Essential Backend** | Data validation & settings in `app/config.py` | **Retain** |
| `python-dotenv` | **Essential Backend** | Environment variable loading in `app/config.py` | **Retain** |
| `httpx` | **Essential Backend** | Async HTTP client used by OpenAI and Anthropic drivers | **Retain** |
| `cryptography` | **Essential Backend** | AES key vault encryption in `app/auth/crypto.py` and `key_vault.py` | **Retain** |
| `pytest` | **Essential Test** | Test runner for test suites | **Retain** |
| `pytest-asyncio` | **Essential Test** | Needed for `@pytest.mark.asyncio` test cases (currently in env, omitted in file) | **Add to requirements** |
| `numpy` | **Heavy ML** | ONLY imported in `app/cra/engine.py:17`. No usage in `app/database.py` or elsewhere. | **Remove** |
| `pandas` | **Heavy ML** | ONLY imported in `app/cra/engine.py:171`. `database.py` uses standard library `csv.DictReader`/`csv.DictWriter`. | **Remove** |
| `torch` | **Heavy ML** | ONLY imported in `app/cra/engine.py:135,152,216`. Multi-gigabyte dependency. | **Remove** |
| `transformers` | **Heavy ML** | ONLY imported in `app/cra/engine.py:134`. | **Remove** |
| `sentence-transformers`| **Heavy ML** | ONLY imported in `app/cra/engine.py:151`. | **Remove** |

### 2.3 Proposed Pruned `requirements.txt`
```text
fastapi
uvicorn[standard]
pydantic
python-dotenv
httpx
cryptography
pytest
pytest-asyncio
```
*Note: Neither `xgboost` nor `faiss` are in `requirements.txt`, but their model files existed in `CRA_Models/`. Pruning `torch`, `transformers`, `sentence-transformers`, `numpy`, and `pandas` eliminates 100% of ML dependencies.*

---

## 3. Code References and Startup Hook Trace

Every reference in the codebase to `app.cra`, `cra_engine`, or ML model loading was audited:

### 3.1 Startup Lifespan Hook (`app/main.py:58-66`)
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: warm up CRA machine learning models asynchronously
    try:
        from app.cra import cra_engine
        asyncio.create_task(asyncio.to_thread(cra_engine.warmup))
    except Exception as e:
        logger.warning(f"Could not launch CRA warmup task: {e}")
    yield
    # Shutdown: clean up SSE connections and sessions
    session_registry.close_all()
    sse_manager.close_all()
    key_vault.dispose()
```
- **Issue:** At backend startup, `warmup()` triggers heavyweight imports and searches for missing PyTorch/HuggingFace model checkpoints.
- **Refactoring:** Remove the startup `warmup` task entirely. The lifespan function should directly `yield` (or only perform lightweight non-ML resource initialization), enabling instant, zero-ML server startup.

### 3.2 Endpoint `/api/customer/verify` (`app/main.py:206-218`)
```python
    cat = (category or "").strip()
    if not cat and query:
        try:
            pred = cra_engine.predict_category(query)
            cat = pred.get("predicted_category", "")
        except Exception:
            pass
```
- **Issue:** If category is absent, it calls `cra_engine.predict_category`.
- **Refactoring:** Replace with a lightweight non-ML category classifier or heuristic mapping based on `app/database.py:get_category_complaint_context`.

### 3.3 Endpoint `/api/resolve/action` (`app/main.py:276-283`)
```python
    if not category and query:
        from app.cra import cra_engine
        try:
            pred = cra_engine.predict_category(query)
            category = pred.get("predicted_category")
        except Exception:
            pass
```
- **Issue:** Uses `cra_engine` to infer missing category before executing database resolution.
- **Refactoring:** Replace with non-ML category determination.

### 3.4 ML Inference Endpoints (`app/main.py:570-610`)
- `POST /api/cra/resolve`: Calls `cra_engine.generate_intelligence_report(complaint)`
- `POST /api/cra/classify`: Calls `cra_engine.predict_category(complaint)`
- **Issue:** These endpoints are called by tests (`tests/test_api_endpoints.py:147-170`) and listed in `app/auth/session.py:PUBLIC_API_PATHS`.
- **Refactoring:** Provide a lightweight, non-ML handler returning the expected JSON contract (`predicted_category`, `category_confidence`, `top_3_categories`, `recommended_team`, `similar_cases`, `extracted_entities`, `sla`) so existing API consumers and tests remain green without PyTorch or HuggingFace.

### 3.5 Troy Resolution Orchestration (`app/simulation/troy.py:240-276, 535-565`)
```python
        self.sse.broadcast("system", {"message": "Running CRA ML models (DistilBERT category classification, semantic search, entity extraction)..."})
        from app.cra import cra_engine
        try:
            report = await asyncio.to_thread(cra_engine.generate_intelligence_report, complaint_text)
        except Exception as e:
            ...
        self.sse.broadcast("cra_report", report)
```
- **Issue:** `troy.py` invokes `cra_engine.generate_intelligence_report`, broadcasts the `cra_report` event via SSE, and incorporates the ML output into the LLM prompt. In `resolution_end` and `council_end` (lines 542, 560), `cra_report: report` is emitted.
- **Refactoring:** 
  1. Troy must use the local LLM directly (or lightweight deterministic entity/category extraction) to analyze the complaint and decide autonomous action.
  2. The `cra_report` SSE event must still be emitted with a valid report structure because `public/js/app.js` listens to it (see Section 4).
  3. Change the broadcast message from "Running CRA ML models..." to "Troy analyzing complaint ticket...".

---

## 4. Frontend UI Compatibility Analysis (`public/`)

Requirement R4 dictates that `public/` files remain completely unmodified. We analyzed `public/js/app.js` to determine all backend contracts:

1. **`cra_report` Event Listener (`public/js/app.js:2360-2368`):**
   ```javascript
   stream.addEventListener("cra_report", (e) => {
     try {
       const report = JSON.parse(e.data);
       cachedCraReport = report;
       if (!isMlCycling && !hasDeliveredPreliminary) {
         deliverPreliminaryAssessment(report);
       }
     } catch (_) {}
   });
   ```
2. **Preliminary Assessment Rendering (`public/js/app.js:2070-2090`):**
   The UI parses:
   - `report.predicted_category` (default: "Subscription")
   - `report.category_confidence` (default: 0.65)
   - `report.recommended_team` (default: "Billing Team")
   - `report.sla.estimated_hours` (default: 18.0)
   - `report.sla.sla_risk` (default: "Low")
   - `report.similar_cases` (array with `.similarity`, `.description`, `.resolution`)
   - `report.extracted_entities` (`references`, `dates`, `amounts`, `products`)
3. **Completion Events (`resolution_end` / `council_end`):**
   Expect `cra_report` inside the payload.

**Implication:** When removing ML, the backend should generate this report structure using a lightweight non-ML helper or Troy's LLM response. The structure is pure JSON and requires no ML libraries.

---

## 5. Existing Tests Coupling and Migration Strategy

### 5.1 `tests/test_category_resolutions.py`
Directly imports:
- `from app.cra import cra_engine` (line 2)
- Asserts on `cra_engine.label_map.values()` (line 26)
- Calls `cra_engine.predict_category(...)` (lines 83, 180)

**Refactoring Required:**
Replace `cra_engine` references with the new non-ML resolution module (e.g. `app.resolution` or lightweight triage classifier in `app`). The 11 categories:
`Account Management`, `Authentication & Access`, `Billing`, `Bugs & Errors`, `Data Sync`, `Integrations & API`, `Onboarding & How-To`, `Performance`, `Refunds`, `Security`, `Subscription`.

### 5.2 `tests/test_api_endpoints.py`
- Lines 147–170 (`test_cra_model_endpoints`): Calls `POST /api/cra/classify` and `POST /api/cra/resolve`.
- By retaining these routes in `app/main.py` backed by the non-ML triage helper, these tests will pass cleanly without loading ML models.

### 5.3 `tests/test_mock_simulation.py`
- Calls `Troy.run(story)`. Once `troy.py` is decoupled from `app.cra`, this test passes without calling any ML code.

---

## 6. Concrete Decoupling & Refactoring Plan

### Step 1: Remove Files and Folders
1. Delete directory `D:\CRA\CRA_Models\` in its entirety.
2. Delete directory `D:\CRA\app\cra\` in its entirety.
3. Clean `.gitignore` lines 22–25.

### Step 2: Prune `requirements.txt`
Remove `numpy`, `pandas`, `torch`, `transformers`, `sentence-transformers`. Ensure `pytest-asyncio` is listed.

### Step 3: Implement Zero-ML Triage & Classification Helper
Create a pure-Python triage utility (e.g. `app/triage.py` or within `app/database.py` / `app/simulation/troy.py`):
- Uses standard library `re` and existing dictionary mappings in `app/database.py`.
- Evaluates the 11 categories and extracts entities (`references`, `dates`, `amounts`, `products`).
- Provides `predict_category(text)` and `generate_triage_report(text)` matching the exact schema expected by `public/js/app.js` and `/api/cra/*`.

### Step 4: Refactor `app/main.py`
1. Remove `cra_engine.warmup` from `lifespan(app)`.
2. Update `/api/customer/verify` and `/api/resolve/action` to use the non-ML triage helper.
3. Update `/api/cra/resolve` and `/api/cra/classify` to use the non-ML triage helper.

### Step 5: Refactor `app/simulation/troy.py`
1. Remove `from app.cra import cra_engine`.
2. Use the local LLM / mock driver for autonomous deliberation.
3. Construct and emit the `cra_report` SSE event cleanly from triage/LLM data.
4. Update `resolution_end` and `council_end` payloads.

### Step 6: Refactor Tests
1. In `tests/test_category_resolutions.py`, import the new zero-ML triage helper.
2. Verify all tests pass with `pytest`.
