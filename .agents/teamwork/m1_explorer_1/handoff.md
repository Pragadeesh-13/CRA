# Milestone 1 - Explorer 1: Handoff Report
## Artifact & Dependency Removal: `CRA_Models/`, `app/cra/`, `requirements.txt`, and `.gitignore`

- **Author**: M1 Explorer 1 (Artifact & Dependency Removal)
- **Recipient**: M1 Worker / Orchestrator
- **Working Directory**: `D:\CRA\.agents\teamwork\m1_explorer_1\`
- **Date**: 2026-11-06T21:14:00Z
- **Handoff Type**: Hard (Investigation & Analysis Complete)

---

### 1. Observation

1. **`CRA_Models/` Directory Contents**:
   Command: `Get-ChildItem -Path CRA_Models -Recurse -Force | Select-Object FullName, Length`
   Observed 14 files and 1 directory (`bert_category_classifier`), totaling 4,211,953 bytes:
   - `CRA_Models/baseline_confusion_matrix.png` (166,700 bytes)
   - `CRA_Models/bert_category_classifier/config.json` (1,291 bytes)
   - `CRA_Models/bert_category_classifier/tokenizer.json` (742,346 bytes)
   - `CRA_Models/bert_category_classifier/tokenizer_config.json` (366 bytes)
   - `CRA_Models/bert_confusion_matrix.png` (165,954 bytes)
   - `CRA_Models/bert_training_curves.png` (89,144 bytes)
   - `CRA_Models/eda_distributions.png` (99,989 bytes)
   - `CRA_Models/label_map.json` (281 bytes)
   - `CRA_Models/sample_reports.json` (10,609 bytes)
   - `CRA_Models/text_length_dist.png` (31,875 bytes)
   - `CRA_Models/tfidf_logreg.pkl` (242,713 bytes)
   - `CRA_Models/topic_distribution.png` (104,556 bytes)
   - `CRA_Models/xgb_resolution_time.json` (1,821,978 bytes)
   - `CRA_Models/xgb_sla_breach.json` (734,152 bytes)
   Command: `git ls-files CRA_Models` returned all 14 files above (all 14 are tracked in git).

2. **`app/cra/` Directory Contents**:
   Command: `Get-ChildItem -Path app\cra -Recurse -Force | Select-Object FullName, Length`
   Observed:
   - `app/cra/__init__.py` (155 bytes, tracked by git)
   - `app/cra/engine.py` (16,982 bytes, tracked by git)
   - `app/cra/__pycache__/__init__.cpython-314.pyc` (270 bytes, untracked bytecode)
   - `app/cra/__pycache__/engine.cpython-314.pyc` (23,679 bytes, untracked bytecode)

3. **Current `requirements.txt` (12 lines)**:
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

4. **Codebase Scan for Heavy ML Imports**:
   Command: `Get-ChildItem -Path app, tests -Recurse -Filter "*.py" | Select-String -Pattern "(import\s+(numpy|pandas|torch|transformers|sentence_transformers))|(from\s+(numpy|pandas|torch|transformers|sentence_transformers))"`
   Verbatim output:
   ```text
   app\cra\engine.py:17:import numpy as np
   app\cra\engine.py:134:            from transformers import AutoTokenizer, AutoModelForSequenceClassification
   app\cra\engine.py:135:            import torch
   app\cra\engine.py:151:            from sentence_transformers import SentenceTransformer
   app\cra\engine.py:152:            import torch
   app\cra\engine.py:171:                import pandas as pd
   app\cra\engine.py:216:            import torch
   ```
   No file outside `app/cra/engine.py` imports `numpy`, `pandas`, `torch`, `transformers`, or `sentence-transformers`. Specifically, `app/database.py` relies solely on the standard library `csv.DictReader` and `csv.DictWriter`.

5. **Empirical Non-ML Import Verification**:
   Command:
   ```python
   python -c "
   import fastapi, uvicorn, pydantic, dotenv, httpx, cryptography, pytest, pytest_asyncio
   import app.config, app.database, app.auth.crypto, app.auth.key_vault, app.auth.session
   import app.http.sse_manager, app.prompts.ticket_prompts, app.prompts.policy_prompts
   import app.providers.base_driver, app.providers.mock_driver, app.providers.openai_driver
   import app.utils.json_extractor, app.utils.sanitizer, app.utils.thought_buffer
   print('All core app modules imported successfully with zero ML!')
   "
   ```
   Output: `All core app modules imported successfully with zero ML!` (exit code 0).

6. **Current `.gitignore` (Lines 15–26)**:
   ```gitignore
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
   Lines 22–25 explicitly reference files in `CRA_Models/`.

---

### 2. Logic Chain

1. **Step 1 (Scope of Deletion)**: Observations 1 and 2 identify every file and folder in `CRA_Models/` (14 tracked files) and `app/cra/` (2 tracked source files, 2 untracked pycache files). Since R1 mandates complete removal of legacy ML pipelines, models, and artifacts, both directories must be removed in full from both git and the filesystem.
2. **Step 2 (Safety of Dependency Removal)**: Observation 4 proves that `numpy`, `pandas`, `torch`, `transformers`, and `sentence-transformers` are exclusively imported within `app/cra/engine.py`. When `app/cra/` is deleted, these 5 packages have zero dependents across `app/`, `tests/`, and `run.py`.
3. **Step 3 (Composition of Clean `requirements.txt`)**: Observation 3 and PROJECT.md Feature 2 specify retaining `fastapi`, `uvicorn[standard]`, `pydantic`, `python-dotenv`, `httpx`, `cryptography`, `pytest`, and adding `pytest-asyncio`. Observation 5 confirms that all non-ML core application modules load without error using only these retained packages.
4. **Step 4 (`.gitignore` Sanitization)**: Observation 6 shows that lines 22–25 of `.gitignore` point directly inside `CRA_Models/`. When `CRA_Models/` is deleted, these 4 lines become dead rules and must be removed while keeping the binary ignores and negative data patterns (`!data/transactions.csv`, `!data/customer_accounts.csv`).
5. **Step 5 (Worker Execution Sequencing)**: Because git tracks the 16 files across `CRA_Models/` and `app/cra/`, but does not track `.pyc` files in `__pycache__`, an execution sequence combining `git rm -rf` and PowerShell `Remove-Item -Recurse -Force` ensures clean git staging without leaving stale filesystem artifacts.

---

### 3. Caveats

1. **Coupled Code Invocations**:
   - `app/main.py` lines 62, 210, 277, 587, 607 contain lazy imports `from app.cra import cra_engine`. Removing `app/cra/` will cause runtime `ModuleNotFoundError` on those routes until Explorer 2 / Worker decouples `app/main.py` and implements `app/triage.py`.
   - `tests/test_category_resolutions.py` lines 2, 26, 83, 180 import `from app.cra import cra_engine`. Tests will fail until Explorer 3 / Worker points these tests to `app.triage`.
2. **Database Integrity**:
   - Deletion commands must strictly target `CRA_Models` and `app/cra`. Under no circumstances should `data/transactions.csv` or `data/customer_accounts.csv` be modified or deleted.

---

### 4. Conclusion

The legacy ML artifact and dependency removal for Milestone 1 is completely mapped:
1. **Delete Directories**: Completely delete `CRA_Models/` (14 files) and `app/cra/` (2 source files + `__pycache__`).
2. **Update `requirements.txt`**: Replace contents with the 8 clean dependencies (`fastapi`, `uvicorn[standard]`, `pydantic`, `python-dotenv`, `httpx`, `cryptography`, `pytest`, `pytest-asyncio`).
3. **Update `.gitignore`**: Delete lines 22–25 (`CRA_Models/...`), leaving intact binary filters and data CSV tracking.
4. Zero application code outside `app/cra/` relies on `torch`, `transformers`, `sentence-transformers`, `numpy`, or `pandas`.

---

### 5. Verification Method

To independently verify the implementation:

1. **Verify Filesystem Removal**:
   ```powershell
   Test-Path D:\CRA\CRA_Models      # Must return False
   Test-Path D:\CRA\app\cra         # Must return False
   ```

2. **Verify Git Staging**:
   ```powershell
   git status -s
   # Must show D CRA_Models/... and D app/cra/... staged for deletion
   ```

3. **Verify Clean `requirements.txt`**:
   ```powershell
   Get-Content D:\CRA\requirements.txt
   # Confirm exactly 8 lines; ensure numpy, pandas, torch, transformers, sentence-transformers are absent
   ```

4. **Verify `.gitignore`**:
   ```powershell
   Select-String -Path D:\CRA\.gitignore -Pattern "CRA_Models"
   # Must return 0 matching lines
   ```

5. **Verify Zero ML Imports in Codebase**:
   ```powershell
   Get-ChildItem -Path D:\CRA\app, D:\CRA\tests -Recurse -Filter "*.py" | Select-String -Pattern "(import\s+(numpy|pandas|torch|transformers|sentence_transformers))|(from\s+(numpy|pandas|torch|transformers|sentence_transformers))"
   # Must return 0 matching lines
   ```

6. **Invalidation Conditions**:
   - Any file in `CRA_Models/` or `app/cra/` remains on disk or tracked by git.
   - Any ML package (`torch`, `transformers`, `sentence-transformers`, `numpy`, `pandas`) remains in `requirements.txt`.
   - `data/transactions.csv` or `data/customer_accounts.csv` is deleted or un-tracked.
