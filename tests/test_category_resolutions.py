import pytest
from app.cra import cra_engine
from app.database import get_category_complaint_context, execute_agent_resolution
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
