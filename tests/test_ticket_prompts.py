import pytest
from app.prompts.ticket_prompts import (
    build_ticket_title_prompt,
    build_ticket_summary_prompt,
    build_title_prompt,
    build_summary_prompt,
)


def test_ticket_title_prompt():
    complaint = "User was double-charged $49.99 for renewal on October 14. Demands immediate refund."
    messages, max_tokens = build_ticket_title_prompt(complaint)
    assert len(messages) == 2
    assert "ticket" in messages[0]["content"].lower()
    assert "policy" not in messages[0]["content"].lower()
    assert "double-charged" in messages[1]["content"]
    assert max_tokens <= 100


def test_ticket_summary_prompt():
    complaint = "User cannot log in to mobile app after 2FA update. Account is locked."
    messages, max_tokens = build_ticket_summary_prompt(complaint)
    assert len(messages) == 2
    assert "ticket" in messages[0]["content"].lower()
    assert "policy" not in messages[0]["content"].lower()
    assert "2FA update" in messages[1]["content"]
    assert max_tokens <= 200


def test_backward_compatibility_aliases():
    assert build_title_prompt is build_ticket_title_prompt
    assert build_summary_prompt is build_ticket_summary_prompt
