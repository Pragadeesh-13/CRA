"""
Milestone 1 Forensic Integrity Verification Script
Executes deep behavioral, structural, and edge-case testing of app/triage.py
and checks for facades, hardcoding, or bypasses.
"""
import sys
import time
import json
from collections.abc import Mapping
from pathlib import Path

# Verify project root
root = Path(__file__).resolve().parents[3]
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

import app.triage as triage
from app.triage import (
    predict_category,
    triage_complaint,
    extract_entities,
    CategoryResult,
    ALL_11_CATEGORIES,
    CATEGORY_TO_TEAM,
    SLA_DEFAULTS,
    CATEGORY_PRECEDENTS,
)

def test_dual_type():
    cr = CategoryResult("Refunds", 0.95)
    assert isinstance(cr, str), "CategoryResult must inherit from str"
    assert isinstance(cr, Mapping), "CategoryResult must implement Mapping"
    assert cr == "Refunds", "String equality comparison must succeed"
    assert cr.startswith("Ref"), "String methods must work"
    assert cr["predicted_category"] == "Refunds", "Dict lookup must succeed"
    assert cr["category_confidence"] == 0.95, "Confidence lookup must succeed"
    assert len(cr["top_3_categories"]) == 3, "Top 3 categories must have 3 items"
    assert dict(cr)["predicted_category"] == "Refunds", "dict(cr) conversion must succeed"
    assert json.dumps(cr.to_dict()) is not None, "json serialization must succeed"
    print("PASS: Dual-type CategoryResult works identically as str and Mapping.")

def test_novel_generalization():
    novel_inputs = {
        "Refunds": [
            "I was charged twice on my invoice yesterday.",
            "Please reverse the duplicate debit of $49.00 on my mastercard.",
            "I want a refund for the extra charge on my card.",
            "Why was I debited twice for order 88219?",
        ],
        "Authentication & Access": [
            "I cannot log in to my account because 2FA code is not sending.",
            "My authenticator app OTP is failing and I am locked out.",
            "Password reset link is not arriving in my inbox.",
        ],
        "Security": [
            "We detected an unauthorized breach from an untrusted IP address.",
            "Someone hacked into our admin portal.",
            "Please revoke all active JWT tokens immediately.",
        ],
        "Integrations & API": [
            "Our webhook endpoint received a 504 gateway timeout.",
            "We are getting rate limit 429 errors from your REST API.",
            "The SDK is failing to connect to the api endpoint.",
        ],
        "Bugs & Errors": [
            "The web dashboard crashes with an unhandled exception.",
            "We encountered a null pointer error and traceback when saving.",
            "System throws a 500 internal server error upon export.",
        ],
        "Data Sync": [
            "Mobile app is out of sync with cloud backend.",
            "We have offline queue merge conflict issues.",
            "Synchronization between web portal and database has diverged.",
        ],
        "Performance": [
            "The search query has extremely high latency over 30 seconds.",
            "The application is very slow and sluggish during report generation.",
            "We are seeing high load time and p99 query duration bottlenecks.",
        ],
        "Subscription": [
            "I want to cancel my subscription renewal before next month.",
            "Please process a downgrade to the starter tier plan.",
            "How does proration credit work when changing subscription tiers?",
        ],
        "Billing": [
            "I need an official VAT tax invoice receipt for European accounting.",
            "Our corporate credit card was declined during payment processing.",
            "Please update our billing address on the next invoice.",
        ],
        "Onboarding & How-To": [
            "How do I invite team members to our workspace?",
            "Where can I find the getting started tutorial guide?",
            "How do I toggle dark mode in the settings?",
        ],
        "Account Management": [
            "We want to transfer workspace ownership to a new administrator.",
            "Please delete our account profile completely.",
            "We need to consolidate our enterprise licensing seats.",
        ],
    }

    total_tested = 0
    for expected_cat, samples in novel_inputs.items():
        for sample in samples:
            total_tested += 1
            pred = predict_category(sample)
            assert str(pred) == expected_cat, f"Mismatch for '{sample}': expected {expected_cat}, got {str(pred)}"
            report = triage_complaint(sample)
            assert report["predicted_category"] == expected_cat
            assert report["recommended_team"] == CATEGORY_TO_TEAM[expected_cat]
            assert report["sla"] == SLA_DEFAULTS[expected_cat]
            assert len(report["similar_cases"]) > 0

    print(f"PASS: Novel generalization test passed across {total_tested} diverse unseen prompts.")

def test_entity_extraction():
    sample = "Customer with order #81094 reported a double charge of $49.00 on 2026-09-18 via mobile app checkout"
    ents = extract_entities(sample)
    assert any("81094" in r for r in ents["references"]), f"Expected order ref, got {ents['references']}"
    assert any("49.00" in a or "49" in a for a in ents["amounts"]), f"Expected amount, got {ents['amounts']}"
    assert "mobile app" in ents["products"]
    assert "checkout" in ents["products"]
    print("PASS: Non-ML regex entity extraction correctly extracts references, amounts, dates, products.")

def test_unknown_fallbacks():
    nonsense = "The quick brown fox jumps over the lazy dog."
    pred = predict_category(nonsense)
    assert str(pred) == "Account Management", f"Expected default Account Management, got {str(pred)}"
    assert pred["category_confidence"] == 0.65, f"Expected 0.65 confidence, got {pred['category_confidence']}"
    print("PASS: Unmatched nonsense input gracefully falls back to default category with lower confidence.")

def test_zero_ml_sys_modules():
    import app.main
    prohibited = ["torch", "transformers", "sentence_transformers", "xgboost", "faiss", "numpy", "pandas", "app.cra"]
    present = [p for p in prohibited if p in sys.modules]
    assert not present, f"Prohibited ML packages found loaded in sys.modules: {present}"
    print("PASS: Clean runtime with zero legacy ML packages loaded in sys.modules.")

if __name__ == "__main__":
    t0 = time.time()
    test_dual_type()
    test_novel_generalization()
    test_entity_extraction()
    test_unknown_fallbacks()
    test_zero_ml_sys_modules()
    print(f"All forensic verification tests completed in {time.time() - t0:.4f}s.")
