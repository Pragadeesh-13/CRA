# Milestone 1 - Explorer 1: Technical Analysis Report
## Artifact & Dependency Removal: `CRA_Models/`, `app/cra/`, `requirements.txt`, and `.gitignore`

- **Author**: M1 Explorer 1 (Artifact & Dependency Removal)
- **Target Repository**: `D:\CRA`
- **Scope**: Requirement R1 / Feature 1 & Feature 2 (Milestone 1)
- **Date**: 2026-11-06T21:14:00Z

---

## 1. Executive Summary

Milestone 1 focuses on eliminating all legacy machine learning dependencies, pipelines, and artifacts from the repository to establish a lightweight, zero-ML backend ready for Troy's local LLM architecture.

This analysis provides:
1. A complete file-by-file audit of all 14 tracked files in `CRA_Models/` (~4.02 MB) and the legacy inference module in `app/cra/` (2 tracked source files + untracked pycache).
2. The exact target content for `requirements.txt`, removing all 5 heavy ML packages (`numpy`, `pandas`, `torch`, `transformers`, `sentence-transformers`) and adding `pytest-asyncio` alongside the 7 retained backend dependencies.
3. Empirical verification proving that no module in `app/` outside `app/cra/` utilizes `numpy`, `pandas`, `torch`, `transformers`, or `sentence-transformers`.
4. The exact cleanup required for `.gitignore` to remove 4 obsolete rules pointing to the deleted `CRA_Models/` directory.
5. Concrete, step-by-step shell execution commands and safety procedures for the M1 Worker.

---

## 2. Inventory of Files to Remove

### 2.1 `CRA_Models/` Directory Inventory

The entire `D:\CRA\CRA_Models\` folder contains 14 files and 1 subdirectory (`bert_category_classifier`). All 14 files are currently tracked by git.

| Relative Path | Size (Bytes) | Git Status | Type / Description | Justification for Deletion |
|---|---|---|---|---|
| `CRA_Models/baseline_confusion_matrix.png` | 166,700 | Tracked | PNG image | Evaluation plot for legacy TF-IDF + Logistic Regression baseline model. Obsolete. |
| `CRA_Models/bert_category_classifier/config.json` | 1,291 | Tracked | JSON config | DistilBERT model architecture and classification head configuration. Legacy HuggingFace artifact. |
| `CRA_Models/bert_category_classifier/tokenizer.json` | 742,346 | Tracked | JSON vocab | Full BPE tokenizer vocabulary for DistilBERT. Legacy HuggingFace artifact. |
| `CRA_Models/bert_category_classifier/tokenizer_config.json` | 366 | Tracked | JSON config | DistilBERT tokenizer hyperparameters and special tokens. Legacy HuggingFace artifact. |
| `CRA_Models/bert_confusion_matrix.png` | 165,954 | Tracked | PNG image | Evaluation plot for DistilBERT classification performance. Obsolete. |
| `CRA_Models/bert_training_curves.png` | 89,144 | Tracked | PNG image | Training and validation loss curves. Obsolete training artifact. |
| `CRA_Models/eda_distributions.png` | 99,989 | Tracked | PNG image | Exploratory data analysis distribution plots. Obsolete. |
| `CRA_Models/label_map.json` | 281 | Tracked | JSON mapping | Maps 11 category indices (`"0"` to `"10"`) to CRA categories. Category definitions will be internalized in pure-Python triage logic (`app/triage.py`). |
| `CRA_Models/sample_reports.json` | 10,609 | Tracked | JSON fixtures | Sample mock outputs from legacy CRAEngine. Replaced by non-ML triage / mock responses. |
| `CRA_Models/text_length_dist.png` | 31,875 | Tracked | PNG image | Complaint text character/token length histogram. Obsolete. |
| `CRA_Models/tfidf_logreg.pkl` | 242,713 | Tracked | Pickle model | Scikit-learn TF-IDF vectorizer + LogisticRegression model binary. Obsolete. |
| `CRA_Models/topic_distribution.png` | 104,556 | Tracked | PNG image | Visual plot of complaint topic clustering. Obsolete. |
| `CRA_Models/xgb_resolution_time.json` | 1,821,978 | Tracked | JSON model | Serialized XGBoost booster model for resolution time regression. Obsolete. |
| `CRA_Models/xgb_sla_breach.json` | 734,152 | Tracked | JSON model | Serialized XGBoost booster model for binary SLA breach classification. Obsolete. |

**Total in `CRA_Models/`**: 14 files, 4,211,953 bytes (~4.02 MB).  
**Action**: Delete entire directory `CRA_Models/` from git and filesystem.

---

### 2.2 `app/cra/` Directory Inventory

The directory `D:\CRA\app\cra\` contains the legacy Python inference engine:

| Relative Path | Size (Bytes) | Git Status | Description | Justification for Deletion |
|---|---|---|---|---|
| `app/cra/__init__.py` | 155 | Tracked | Module exports: `CRAEngine`, `cra_engine`, `clean_bert`, `extract_entities` | Legacy ML module entrypoint. Obsolete. |
| `app/cra/engine.py` | 16,982 | Tracked | 386 lines of inference code importing `numpy`, `pandas`, `torch`, `transformers`, `sentence_transformers` | The legacy inference engine loading models from `CRA_Models/`. Replaced by `app/triage.py`. |
| `app/cra/__pycache__/__init__.cpython-314.pyc` | 270 | Untracked (ignored) | Python bytecode cache | Stale bytecode. Must be purged from disk. |
| `app/cra/__pycache__/engine.cpython-314.pyc` | 23,679 | Untracked (ignored) | Python bytecode cache | Stale bytecode. Must be purged from disk. |

**Total in `app/cra/`**: 2 tracked Python source files, 2 untracked bytecode files.  
**Action**: Delete entire directory `app/cra/` from git and filesystem.

---

## 3. Dependency Audit: `requirements.txt`

### 3.1 Current vs Target Comparison

#### Current `requirements.txt` (12 lines):
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

#### Proposed `requirements.txt` (8 lines):
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

---

### 3.2 Detailed Package Utilization Analysis

| Package | Classification | Status | Usage in Codebase | Rationale |
|---|---|---|---|---|
| `fastapi` | Web Framework | **RETAIN** | Used in `app/main.py`, `app/simulation/state.py` | Core API routing, middleware, SSE StreamingResponse, lifespan management. |
| `uvicorn[standard]` | ASGI Server | **RETAIN** | Used in `run.py` (`uvicorn.run(...)`) | Web application server. |
| `pydantic` | Data Validation | **RETAIN** | Used in `app/config.py`, request models | Settings validation and structured payloads. |
| `python-dotenv` | Environment Config | **RETAIN** | Used in `app/config.py` | Loading `.env` configurations (e.g. `LMSTUDIO_BASE_URL`). |
| `httpx` | Async HTTP Client | **RETAIN** | Used in `app/providers/openai_driver.py`, `anthropic_driver.py` | Communicates asynchronously with OpenAI-compatible LLM endpoint (LM Studio). |
| `cryptography` | Encryption Primitives | **RETAIN** | Used in `app/auth/crypto.py`, `app/auth/key_vault.py` | AES-256-GCM encryption for API key vaults. |
| `pytest` | Test Framework | **RETAIN** | Root test runner for `tests/` | Automated test suite execution. |
| `pytest-asyncio` | Test Plugin | **ADD** | Required for async pytest markers (`@pytest.mark.asyncio`) | Supports asynchronous test cases testing ASGI routes and Troy agent execution. |
| `numpy` | Heavy ML | **REMOVE** | **ONLY** in `app/cra/engine.py:17`. Zero imports in `app/database.py`, `app/simulation/`, etc. | Eliminates ~50MB of binary dependencies. Standard Python math/types suffice. |
| `pandas` | Heavy ML | **REMOVE** | **ONLY** in `app/cra/engine.py:171`. Zero imports in `app/database.py` (which uses standard `csv` module). | Eliminates ~100MB of binary dependencies. |
| `torch` | Heavy ML | **REMOVE** | **ONLY** in `app/cra/engine.py:135,152,216`. Multi-gigabyte dependency. | Eliminates PyTorch runtime, CUDA/CPU model loading overhead. |
| `transformers` | Heavy ML | **REMOVE** | **ONLY** in `app/cra/engine.py:134`. | Eliminates HuggingFace model caching and initialization overhead. |
| `sentence-transformers` | Heavy ML | **REMOVE** | **ONLY** in `app/cra/engine.py:151`. | Eliminates SBERT embedding download and vector compute. |

---

### 3.3 Empirical Verification of Zero ML Dependencies in Remaining Code

A full recursive scan across all remaining Python files (`app/**/*.py`, `run.py`, `tests/**/*.py`) was performed:
```powershell
Get-ChildItem -Path app, tests -Recurse -Filter "*.py" | Select-String -Pattern "(import\s+(numpy|pandas|torch|transformers|sentence_transformers))|(from\s+(numpy|pandas|torch|transformers|sentence_transformers))"
```
**Result**: The *only* matching lines were inside `app/cra/engine.py`.  
Once `app/cra/` is deleted, zero imports of `numpy`, `pandas`, `torch`, `transformers`, or `sentence_transformers` exist in the entire codebase.

Furthermore, importing all core application modules without ML components:
```python
import fastapi, uvicorn, pydantic, dotenv, httpx, cryptography, pytest, pytest_asyncio
import app.config, app.database, app.auth.crypto, app.auth.key_vault, app.auth.session
import app.http.sse_manager, app.prompts.ticket_prompts, app.prompts.policy_prompts
import app.providers.base_driver, app.providers.mock_driver, app.providers.openai_driver
import app.utils.json_extractor, app.utils.sanitizer, app.utils.thought_buffer
```
Executes with return code 0 and zero missing dependency errors.

---

## 4. `.gitignore` Cleanup Audit

### 4.1 Current `.gitignore`
```gitignore
__pycache__/
*.pyc
*.pyo
*.pyd
.Python
env/
venv/
.venv/
.pytest_cache/
.coverage
htmlcov/
.env
firebase_creds.json

# Model weights and large datasets (>100MB)
*.bin
*.npy
*.safetensors
*.csv
!data/transactions.csv
!data/customer_accounts.csv
CRA_Models/bert_category_classifier/model.safetensors
CRA_Models/faiss_index.bin
CRA_Models/sbert_embeddings.npy
CRA_Models/resolved_kb.csv
```

### 4.2 Dead Rules Identified
Lines 22–25 explicitly reference files inside `CRA_Models/`:
- `CRA_Models/bert_category_classifier/model.safetensors`
- `CRA_Models/faiss_index.bin`
- `CRA_Models/sbert_embeddings.npy`
- `CRA_Models/resolved_kb.csv`

Because `CRA_Models/` is deleted entirely, these lines are obsolete dead rules.

### 4.3 Proposed Clean `.gitignore`
```gitignore
__pycache__/
*.pyc
*.pyo
*.pyd
.Python
env/
venv/
.venv/
.pytest_cache/
.coverage
htmlcov/
.env
firebase_creds.json

# Model weights and large datasets (>100MB)
*.bin
*.npy
*.safetensors
*.csv
!data/transactions.csv
!data/customer_accounts.csv
```

**Note on Retained Rules**:
- `*.bin`, `*.npy`, `*.safetensors`, `*.csv` remain in place to prevent accidental commits of binary blobs or ad-hoc data dumps.
- `!data/transactions.csv` and `!data/customer_accounts.csv` explicitly preserve git tracking of the core database CSV files.

---

## 5. Step-by-Step Implementation Guide for Worker

The M1 Worker should execute the following steps in sequence:

### Step 1: Remove `CRA_Models/` and `app/cra/`
Run in PowerShell from repository root `D:\CRA`:
```powershell
# Stage deletions in git
git rm -rf CRA_Models
git rm -rf app/cra

# Ensure any untracked leftover directories/bytecode files are purged from disk
Remove-Item -Path "CRA_Models" -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -Path "app\cra" -Recurse -Force -ErrorAction SilentlyContinue
```

### Step 2: Update `requirements.txt`
Overwrite `D:\CRA\requirements.txt` with the clean 8-package content:
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

### Step 3: Clean `.gitignore`
Update `D:\CRA\.gitignore` by removing lines 22–25 (`CRA_Models/...`), leaving:
```gitignore
__pycache__/
*.pyc
*.pyo
*.pyd
.Python
env/
venv/
.venv/
.pytest_cache/
.coverage
htmlcov/
.env
firebase_creds.json

# Model weights and large datasets (>100MB)
*.bin
*.npy
*.safetensors
*.csv
!data/transactions.csv
!data/customer_accounts.csv
```

### Step 4: Verification Commands for Worker
Verify removal and integrity:
```powershell
# 1. Confirm directories are gone
Test-Path CRA_Models   # Must return False
Test-Path app/cra      # Must return False

# 2. Confirm requirements.txt content
Get-Content requirements.txt

# 3. Confirm git status shows clean staged deletions
git status -s
```

---

## 6. Risk Assessment & Safety Precautions

1. **Preservation of Database Files**:
   - `data/transactions.csv` and `data/customer_accounts.csv` must NEVER be modified or deleted during artifact removal.
   - The `.gitignore` negative patterns `!data/transactions.csv` and `!data/customer_accounts.csv` must be preserved.
2. **Untracked `__pycache__` Cleanup**:
   - Running `git rm -rf app/cra` does not delete untracked `app/cra/__pycache__`. The PowerShell `Remove-Item` command is mandatory to prevent stale bytecode from causing Python import anomalies.
3. **Coordination with Explorer 2 & 3**:
   - Deleting `app/cra` will cause `from app.cra import cra_engine` to raise `ModuleNotFoundError`.
   - Explorer 2 provides `app/triage.py` and decouples `app/main.py`.
   - Explorer 3 provides the refactored imports in `tests/test_category_resolutions.py`.
   - Worker should coordinate the replacement of imports alongside the folder deletion so tests stay green.
