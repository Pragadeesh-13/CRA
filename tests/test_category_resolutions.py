import pytest
from app.cra import cra_engine
from app.database import get_category_complaint_context, execute_agent_resolution, execute_manual_resolution
from app.simulation.troy import Troy
from app.config import settings
from app.http.sse_manager import SseManager
from app.simulation.state import SimulationState


ALL_11_CATEGORIES = [
    "Account Management",
    "Authentication & Access",
    "Billing",
    "Bugs & Errors",
    "Data Sync",
    "Integrations & API",
    "Onboarding & How-To",
    "Performance",
    "Refunds",
    "Security",
    "Subscription",
]


def test_label_map_has_all_11_categories():
    labels = list(cra_engine.label_map.values())
    for cat in ALL_11_CATEGORIES:
        assert cat in labels, f"Missing category {cat} in label map"


@pytest.mark.parametrize("category", ALL_11_CATEGORIES)
def test_category_complaint_context_all_11(category):
    ctx = get_category_complaint_context(category=category, query="test complaint")
    assert ctx["category"] == category
    assert "system_label" in ctx
    assert "status_pill" in ctx
    assert "action_btn_text" in ctx
    assert "reference_id" in ctx
    assert "steps" in ctx
    assert len(ctx["steps"]) >= 4


@pytest.mark.parametrize("category", ALL_11_CATEGORIES)
def test_category_agent_resolution_execution(category):
    res = execute_agent_resolution(query="Testing domain resolution", category=category)
    assert res["ok"] is True
    assert res["action"] == "agent_resolve"
    assert "customer" in res
    assert "ticket" in res
    assert "timestamp" in res


def test_troy_fallback_generation_all_11():
    troy = Troy(
        config=settings,
        driver=None,
        sse_manager=SseManager(),
        state=SimulationState(),
        enable_barrier=False,
    )

    for cat in ALL_11_CATEGORIES:
        ctx = get_category_complaint_context(category=cat, query="sample complaint")
        fallback = troy._build_category_fallback(
            pred_cat=cat,
            complaint_text="Customer reported issue with system.",
            conf_pct=88.5,
            team="Engineering" if cat in ["Bugs & Errors", "Performance", "Data Sync", "Integrations & API"] else "Tier 1 Support",
            sla_hours=4.0,
            report={"team_method": "Rule-based", "sla": {"sla_risk": "Low"}},
            cat_ctx=ctx,
        )

        assert "Executive Assessment & Category Confirmation" in fallback
        assert "Root Cause Analysis & Precedent Alignment" in fallback
        assert "Customer Communication Strategy" in fallback
        assert "Internal Routing & Next Action Steps" in fallback
        assert cat in fallback


def test_two_factor_auth_complaint_flow():
    prompt = "Two-factor authentication codes are not being delivered to my phone. Cannot login to dashboard."
    pred = cra_engine.predict_category(prompt)
    assert pred["predicted_category"] == "Authentication & Access"
    assert pred["category_confidence"] > 0.80

    ctx = get_category_complaint_context(category=pred["predicted_category"], query=prompt)
    assert ctx["is_refund"] is False
    assert "Identity" in ctx["system_label"]
    assert "AUTH-" in ctx["reference_id"]

    res = execute_agent_resolution(query=prompt, category="Authentication & Access")
    assert res["ok"] is True
    assert res["category"] == "Authentication & Access"
    assert "data/transactions.csv" not in res.get("database_file", "")
    assert res["customer"]["customer_name"] == "Sarah Jenkins"

    troy = Troy(
        config=settings,
        driver=None,
        sse_manager=SseManager(),
        state=SimulationState(),
        enable_barrier=False,
    )
    fallback = troy._build_category_fallback(
        pred_cat="Authentication & Access",
        complaint_text=prompt,
        conf_pct=pred["category_confidence"] * 100,
        team="Security",
        sla_hours=3.5,
        report={"team_method": "Rule-based", "sla": {"sla_risk": "Low"}},
        cat_ctx=ctx,
    )
    assert "data/transactions.csv" not in fallback
    assert "Alex Morgan" not in fallback
    assert "$49.00" not in fallback
    assert "AUTH-" in fallback


def test_non_refund_categories_have_no_csv_references():
    non_refund_cats = [c for c in ALL_11_CATEGORIES if c != "Refunds"]
    troy = Troy(
        config=settings,
        driver=None,
        sse_manager=SseManager(),
        state=SimulationState(),
        enable_barrier=False,
    )

    for cat in non_refund_cats:
        ctx = get_category_complaint_context(category=cat, query="issue description")
        assert ctx["is_refund"] is False
        assert "data/transactions.csv" not in ctx.get("database_file", "")

        fallback = troy._build_category_fallback(
            pred_cat=cat,
            complaint_text="issue description",
            conf_pct=90.0,
            team="Engineering",
            sla_hours=4.0,
            report={"team_method": "Rule-based", "sla": {"sla_risk": "Low"}},
            cat_ctx=ctx,
        )
        assert "data/transactions.csv" not in fallback
        assert "Alex Morgan" not in fallback
        assert "$49.00" not in fallback


@pytest.mark.parametrize("category", ALL_11_CATEGORIES)
def test_manual_resolution_all_11_categories(category):
    res = execute_manual_resolution(query=f"Issue requiring human intervention for {category}", category=category)
    assert res["ok"] is True
    assert res["action"] == "manual_resolve"
    assert res["category"] == category
    assert "assigned_queue" in res
    assert "resolution_action" in res
    assert "ticket" in res
    assert "customer" in res

    if category == "Refunds":
        assert res["database_file"] == "data/transactions.csv"
        assert "Billing" in res["assigned_queue"]
    else:
        assert res["database_file"] != "data/transactions.csv"
        assert "data/transactions.csv" not in res.get("database_file", "")
        assert res["ticket"]["predicted_team"] == res["assigned_queue"]
        assert "Specialist" in res["assigned_queue"] or "Queue" in res["assigned_queue"]


def test_customer_account_lookup_and_emily_watson_resolution():
    from app.database import find_customer_account_by_query, analyze_customer_transactions, get_category_complaint_context

    q = "Order #ORD-81094: Double billed"
    acc = find_customer_account_by_query(query=q)
    assert acc is not None
    assert acc["customer_id"] == "USR-07231"
    assert acc["customer_name"] == "Emily Watson"
    assert acc["customer_email"] == "emily.w@consulting.co"

    pred = cra_engine.predict_category(q)
    assert pred["predicted_category"] == "Refunds"
    assert pred["category_confidence"] > 0.90

    ctx = get_category_complaint_context("Refunds", query=q)
    assert ctx["is_refund"] is True
    assert ctx["customer_id"] == "USR-07231"
    assert ctx["customer_name"] == "Emily Watson"
    assert ctx["customer_email"] == "emily.w@consulting.co"
    assert ctx["action_btn_text"] == "Let agent resolve it"
    assert ctx["is_already_refunded"] is False
    assert "TXN-7712" in ctx["steps"][2]["text"]
    assert "Emily Watson" in ctx["steps"][1]["text"]

    analysis = analyze_customer_transactions(query=q)
    assert analysis["customer_name"] == "Emily Watson"
    assert analysis["customer_id"] == "USR-07231"
    assert analysis["has_pending_duplicate"] is True
    assert analysis["duplicate_transaction"]["transaction_id"] == "TXN-7712"
    assert analysis["retained_transaction"]["transaction_id"] == "TXN-7711"


def test_all_scenarios_customer_account_resolution():
    from app.database import find_customer_account_by_query

    test_cases = [
        ("Order #88219 was debited twice", "USR-10492", "Alex Morgan"),
        ("Order #86102 was charged at full rate", "USR-09148", "Michael Scott"),
        ("Order #79110 two-factor locked", "USR-08914", "Sarah Jenkins"),
        ("Order #93041 webhook timeout 504", "USR-12053", "David Chen"),
        ("Order #85420 VAT tax invoice", "USR-14309", "Elena Rostova"),
        ("Order #ORD-81094: Double billed", "USR-07231", "Emily Watson"),
    ]

    for q, expected_id, expected_name in test_cases:
        acc = find_customer_account_by_query(query=q)
        assert acc is not None, f"Failed to match account for query: {q}"
        assert acc["customer_id"] == expected_id
        assert acc["customer_name"] == expected_name

