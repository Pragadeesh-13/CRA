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
