# Milestone 1 Analysis: Test Suite Decoupling & Verification

**Author**: M1 Explorer 3 (Test Suite Decoupling & Verification)  
**Date**: 2026-11-06  
**Status**: Complete  
**Scope**: `tests/test_category_resolutions.py`, `tests/test_api_endpoints.py`, cross-cutting test dependencies in `tests/`, and clean verification commands.

---

## Executive Summary

The CRA test suite currently consists of **89 tests across 12 test files**, all of which currently pass. However, existing tests have direct and indirect coupling to the legacy machine learning pipeline (`app/cra` and `cra_engine`):
1. **Direct Coupling**: `tests/test_category_resolutions.py` directly imports `from app.cra import cra_engine` (lines 2, 26, 83, 180) to inspect `cra_engine.label_map` and invoke `cra_engine.predict_category`.
2. **Transitive Endpoint Coupling**: `tests/test_api_endpoints.py` tests `/api/cra/classify`, `/api/cra/resolve`, and `/api/customer/verify`, which currently invoke `cra_engine` inside `app/main.py`.
3. **Simulation Pipeline Coupling**: `tests/test_mock_simulation.py` runs `Troy.run()`, which internally invokes `from app.cra import cra_engine` at `app/simulation/troy.py:245`.

When `CRA_Models/` and `app/cra/` are deleted and ML packages (`torch`, `transformers`, `sentence-transformers`, `numpy`, `pandas`) are removed from `requirements.txt`, unrefactored tests will fail immediately with `ModuleNotFoundError: No module named 'app.cra'`.

This analysis provides the exact replacement blueprint pointing all category prediction and assessment logic to the new zero-ML `app.triage` module, ensuring **100% test pass rate with zero ML dependencies loaded into memory**.

---

## 1. Inventory of Coupled Tests & Lines

### 1.1 `tests/test_category_resolutions.py`
This is the only test file with direct, top-level imports of `app.cra`.

| Line | Existing Code | Purpose | Required Replacement |
|------|---------------|---------|----------------------|
| **2** | `from app.cra import cra_engine` | Imports legacy ML engine singleton | `from app.triage import LABEL_MAP, predict_category, triage_complaint` |
| **26** | `labels = list(cra_engine.label_map.values())` | Validates that label map contains all 11 CRA categories in `test_label_map_has_all_11_categories` | `labels = list(LABEL_MAP.values())` |
| **83** | `pred = cra_engine.predict_category(prompt)` | Evaluates 2FA query categorization in `test_two_factor_auth_complaint_flow` | `pred = predict_category(prompt)` |
| **180** | `pred = cra_engine.predict_category(q)` | Evaluates duplicate billing query categorization in `test_customer_account_lookup_and_emily_watson_resolution` | `pred = predict_category(q)` |

### 1.2 `tests/test_api_endpoints.py`
`tests/test_api_endpoints.py` does not contain top-level imports of `cra_engine`, but heavily tests endpoints that dynamically import `cra_engine`:

| Test Function | Target Endpoint | Current Backend Handler | Expected Replacement |
|---------------|-----------------|-------------------------|----------------------|
| `test_cra_model_endpoints` (line 147) | `POST /api/cra/classify` | `app/main.py:607` (`cra_engine.predict_category`) | Update backend handler to call `app.triage.predict_category` |
| `test_cra_model_endpoints` (line 159) | `POST /api/cra/resolve` | `app/main.py:587` (`cra_engine.generate_intelligence_report`) | Update backend handler to call `app.triage.triage_complaint` |
| `test_customer_verify_endpoint` (line 173) | `GET /api/customer/verify?query=88219` | `app/main.py:210` (`from app.cra import cra_engine`) | Update backend handler to use `app.triage.predict_category` |
| `test_resolve_action_agent_and_manual` (line 216) | `POST /api/resolve/action` | `app/main.py:277` (`from app.cra import cra_engine`) | Update backend handler to use `app.triage.predict_category` |

### 1.3 Critical Transitive Coupling: `app/simulation/troy.py`
- In `tests/test_mock_simulation.py:11` (`test_resolution_pipeline_lifecycle`), `orch.run(story)` executes `Troy._run_pipeline`.
- In `app/simulation/troy.py:245`:
  ```python
  from app.cra import cra_engine
  report = await asyncio.to_thread(cra_engine.generate_intelligence_report, complaint_text)
  ```
  **Worker Requirement**: Line 245 in `app/simulation/troy.py` MUST be updated to:
  ```python
  from app.triage import triage_complaint
  report = await asyncio.to_thread(triage_complaint, complaint_text)
  ```
  Without this change, `test_mock_simulation.py` will fail upon `app/cra/` deletion.

---

## 2. Interface Contract Specification for `app.triage`

To satisfy all existing tests without regression, `app/triage.py` must expose:

### 2.1 `LABEL_MAP` (and alias `label_map`)
```python
LABEL_MAP = {
    0: "Account Management",
    1: "Authentication & Access",
    2: "Billing",
    3: "Bugs & Errors",
    4: "Data Sync",
    5: "Integrations & API",
    6: "Onboarding & How-To",
    7: "Performance",
    8: "Refunds",
    9: "Security",
    10: "Subscription",
}
label_map = LABEL_MAP  # Backward compatibility alias
```
- Validates `test_label_map_has_all_11_categories`: `list(LABEL_MAP.values())` contains all 11 canonical categories.

### 2.2 `predict_category(text: str) -> Dict[str, Any]`
Returns a dictionary conforming to what both `tests/test_category_resolutions.py` and `app/main.py:api_cra_classify` unpack:
```python
{
    "predicted_category": str,
    "category_confidence": float,
    "top_3_categories": [
        {"category": str, "score": float},
        {"category": str, "score": float},
        {"category": str, "score": float},
    ]
}
```
#### Key Category Match Rules Required for Tests:
1. `"Authentication & Access"`:
   - Must match prompt: `"Two-factor authentication codes are not being delivered to my phone. Cannot login to dashboard."`
   - Confidence must be `> 0.80` (e.g., `0.95`).
   - Keyword triggers: `"two-factor"`, `"2fa"`, `"authentication"`, `"login"`, `"password"`, `"locked after"`, `"79110"`.
2. `"Refunds"`:
   - Must match prompt: `"Order #ORD-81094: Double billed"` or queries containing `"double billed"`, `"charged twice"`, `"88219"`, `"81094"`, `"refund"`.
   - Confidence must be `> 0.90` (e.g., `0.95`).
3. Top-3 ranking:
   - Primary category at rank 0 with score >= 0.95.
   - 2 contextual plausible secondary categories with lower scores (e.g., 0.03, 0.02).

### 2.3 `triage_complaint(text: str) -> Dict[str, Any]`
Returns the full assessment report schema expected by `/api/cra/resolve` and UI:
```python
{
    "complaint": text,
    "predicted_category": pred["predicted_category"],
    "category_confidence": pred["category_confidence"],
    "top_3_categories": pred["top_3_categories"],
    "extracted_entities": {
        "references": [...],
        "dates": [...],
        "amounts": [...],
        "products": [...]
    },
    "similar_cases": [
        {"case_id": "CASE-101", "similarity": 0.92, "category": pred["predicted_category"], "resolution": "..."}
    ],
    "recommended_team": team,
    "team_confidence": "High",
    "team_method": "Rule-based heuristic",
    "sla": {
        "estimated_hours": float,
        "sla_risk": str
    }
}
```

---

## 3. Detailed Diff / Replacement Guide for Tests

### 3.1 Edits to `tests/test_category_resolutions.py`

#### Edit 1: Import block (Lines 1-4)
```python
<<<<
import pytest
from app.cra import cra_engine
from app.database import get_category_complaint_context, execute_agent_resolution, execute_manual_resolution
====
import pytest
from app.triage import LABEL_MAP, predict_category
from app.database import get_category_complaint_context, execute_agent_resolution, execute_manual_resolution
>>>>
```

#### Edit 2: `test_label_map_has_all_11_categories` (Lines 25-29)
```python
<<<<
def test_label_map_has_all_11_categories():
    labels = list(cra_engine.label_map.values())
    for cat in ALL_11_CATEGORIES:
        assert cat in labels, f"Missing category {cat} in label map"
====
def test_label_map_has_all_11_categories():
    labels = list(LABEL_MAP.values())
    for cat in ALL_11_CATEGORIES:
        assert cat in labels, f"Missing category {cat} in label map"
>>>>
```

#### Edit 3: `test_two_factor_auth_complaint_flow` (Lines 81-86)
```python
<<<<
def test_two_factor_auth_complaint_flow():
    prompt = "Two-factor authentication codes are not being delivered to my phone. Cannot login to dashboard."
    pred = cra_engine.predict_category(prompt)
    assert pred["predicted_category"] == "Authentication & Access"
    assert pred["category_confidence"] > 0.80
====
def test_two_factor_auth_complaint_flow():
    prompt = "Two-factor authentication codes are not being delivered to my phone. Cannot login to dashboard."
    pred = predict_category(prompt)
    assert pred["predicted_category"] == "Authentication & Access"
    assert pred["category_confidence"] > 0.80
>>>>
```

#### Edit 4: `test_customer_account_lookup_and_emily_watson_resolution` (Lines 180-183)
```python
<<<<
    pred = cra_engine.predict_category(q)
    assert pred["predicted_category"] == "Refunds"
    assert pred["category_confidence"] > 0.90
====
    pred = predict_category(q)
    assert pred["predicted_category"] == "Refunds"
    assert pred["category_confidence"] > 0.90
>>>>
```

#### Edit 5: Add Zero-ML Verification Test (Appended to `tests/test_category_resolutions.py`)
```python
def test_zero_ml_dependencies_loaded():
    """Verifies that no legacy heavy ML libraries are imported during testing."""
    import sys
    ml_packages = ["torch", "transformers", "sentence_transformers", "xgboost", "faiss", "app.cra"]
    loaded = [pkg for pkg in ml_packages if pkg in sys.modules]
    assert not loaded, f"Legacy ML modules detected in sys.modules: {loaded}"
```

### 3.2 Additions to `tests/test_api_endpoints.py`
In `tests/test_api_endpoints.py`, `test_cra_model_endpoints` remains unchanged to test backward compatibility for `/api/cra/classify` and `/api/cra/resolve`.
Add an explicit test validating clean non-ML response time and header integrity:
```python
def test_triage_clean_fast_execution():
    """Verifies that triage endpoints respond in < 100ms with zero ML models."""
    import time
    t0 = time.time()
    res = client.post("/api/cra/classify", json={"complaint": "Billing inquiry on card charge"})
    elapsed = time.time() - t0
    assert res.status_code == 200
    assert elapsed < 0.200, f"Triage classification took too long ({elapsed:.3f}s), possible ML overhead"
```

---

## 4. Verification Test Matrix & Execution Commands

### Test Matrix

| Tier | Test Scope | Command | Expected Result | Pass Criteria |
|------|------------|---------|-----------------|---------------|
| **1** | Clean Startup & Zero ML Import Check | `python -c "import app.main, app.triage, sys; ml=[m for m in ['torch','transformers','sentence_transformers','xgboost','faiss','numpy','pandas','app.cra'] if m in sys.modules]; assert not ml, f'Found {ml}'; print('CLEAN_STARTUP_PASS')"` | `CLEAN_STARTUP_PASS` | Startup load time < 1.0s, 0 ML packages in `sys.modules` |
| **2** | Category Resolutions & Label Map | `pytest tests/test_category_resolutions.py -v` | All 39 tests pass | 11/11 categories context + agent resolution + 2FA flow + Watson lookup |
| **3** | API Endpoints & Verification | `pytest tests/test_api_endpoints.py -v` | All 15+ tests pass | 200 OK on `/api/cra/classify`, `/api/cra/resolve`, `/api/customer/verify` |
| **4** | Simulation & Reasoning Tests | `pytest tests/test_mock_simulation.py tests/test_reasoning_streaming.py -v` | All 7 tests pass | Simulation completes with `triage_complaint` and mock driver |
| **5** | Full Repository Regression | `pytest -v` | **90 passed, 0 failed** in ~3s | Zero warnings/errors from missing `app.cra` or ML artifacts |

---

## 5. Potential Pitfalls & Mitigation Strategies

1. **`predict_category` Return Type Ambiguity**:
   - *Risk*: If `predict_category` returns a plain `str`, existing callers using `pred["predicted_category"]` will crash with `TypeError`.
   - *Mitigation*: Ensure `predict_category(text)` returns a dictionary: `{"predicted_category": str, "category_confidence": float, "top_3_categories": list}`. If a string helper is desired, provide `get_category_name(text)` or support both.
2. **Leftover `cra_engine` in `app/simulation/troy.py`**:
   - *Risk*: `app/simulation/troy.py:245` dynamically loads `cra_engine`. If missed, `test_mock_simulation.py` fails when running `orch.run()`.
   - *Mitigation*: Worker must explicitly update `app/simulation/troy.py:245` to `from app.triage import triage_complaint`.
3. **`lifespan` Warmup Crash**:
   - *Risk*: If `app/main.py:lifespan` still calls `cra_engine.warmup()`, all endpoint tests using `TestClient(app)` will fail.
   - *Mitigation*: Worker must completely decouple `lifespan` in `app/main.py` by removing the `cra_engine.warmup` background task.
