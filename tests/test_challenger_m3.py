import hashlib
import json
import os
import re
from pathlib import Path
from typing import List, Dict, Any

import pytest
import pytest_asyncio

from app.config import settings
from app.database import (
    read_all_transactions,
    read_all_customers,
)
from app.http.sse_manager import SseManager
from mock_driver import MockDriver
from app.simulation.state import SimulationState
from app.simulation.troy import Troy


def compute_sha256(file_path: Path) -> str:
    """Computes SHA256 hash of a file."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


class CapturingSseManager(SseManager):
    """Captures all broadcast events for empirical inspection."""

    def __init__(self):
        super().__init__()
        self.captured_events: List[Dict[str, Any]] = []

    def broadcast(self, event: str, data: Any):
        self.captured_events.append({"event": event, "data": data})
        return super().broadcast(event, data)


# ==============================================================================
# CHALLENGER SUITE 1: Autonomous Intent Evaluation & Boundary Inputs
# ==============================================================================


@pytest.mark.parametrize(
    "text,category",
    [
        ("How do I configure two-factor authentication (2FA)?", "Authentication & Access"),
        ("The Android mobile application crashes on PDF export with 500 error", "Bugs & Errors"),
        ("Where can I find documentation for the webhook API integration?", "Integrations & API"),
        ("How do I update my primary email address and profile photo?", "Account Management"),
        ("Dashboard queries are taking over 15 seconds during morning hours", "Performance"),
        ("Mobile app contacts are not syncing with the desktop app", "Data Sync"),
        ("How do I invite teammates to our shared workspace?", "Onboarding & How-To"),
        ("I received a suspicious phishing email claiming to be from your team", "Security"),
        ("What features are included in the Pro vs Enterprise tier?", "Subscription"),
    ],
)
def test_evaluate_intent_non_billing_categories(text, category):
    """Verifies that clear non-billing inquiries evaluate to direct_response with zero DB mutation required."""
    res = Troy.evaluate_complaint_intent(text, category=category)
    assert res["intent"] == "direct_response", f"Expected direct_response for '{text}', got {res['intent']}"
    assert res["requires_db_mutation"] is False
    assert res["is_billing_dispute"] is False
    assert res["category"] == category


@pytest.mark.parametrize(
    "edge_input",
    [
        "",  # empty string
        "   \t\n\r  ",  # whitespace only
        None,  # NoneType
        "?",  # single punctuation
        "!",
        "...",
        "!@#$%^&*()_+=-~`{}[]|:;'<>?,./",  # pure symbols
        "a" * 100000,  # 100k long input
        "💳❌🤷‍♂️ 💸 \u200b\ufeff",  # unicode emojis and zero-width chars
        "help",  # single word
        "hello support team",
        "1234567890",
    ],
    ids=lambda x: f"len_{len(str(x))}" if x is not None and len(str(x)) > 50 else str(x),
)
def test_evaluate_intent_edge_cases_no_crash(edge_input):
    """Stress-tests evaluate_complaint_intent with abnormal, boundary, and edge-case inputs."""
    try:
        res = Troy.evaluate_complaint_intent(edge_input)
    except Exception as exc:
        pytest.fail(f"evaluate_complaint_intent crashed on edge input {repr(edge_input)[:40]}: {exc}")

    assert isinstance(res, dict)
    assert "intent" in res
    assert res["intent"] in ["direct_response", "database_remediation"]
    assert "requires_db_mutation" in res
    assert isinstance(res["requires_db_mutation"], bool)
    assert "is_billing_dispute" in res
    assert isinstance(res["is_billing_dispute"], bool)


def test_evaluate_intent_id_extraction_boundaries():
    """Tests regex extraction of order, customer, and transaction IDs under edge formats."""
    # Valid IDs
    res = Troy.evaluate_complaint_intent("Charged for ORD-88219 by USR-10492 txn TXN-9012")
    assert res["order_id"] == "ORD-88219"
    assert res["customer_id"] == "USR-10492"
    assert res["transaction_id"] == "TXN-9012"

    # Lowercase IDs normalized
    res_low = Troy.evaluate_complaint_intent("ord-81094 usr-07231 txn-7712")
    assert res_low["order_id"] == "ORD-81094"
    assert res_low["customer_id"] == "USR-07231"
    assert res_low["transaction_id"] == "TXN-7712"

    # Malformed IDs should not crash and should return None
    malformed = "ORD- ABC USR- TXN- ORD USR9999 TXN_123"
    res_mal = Troy.evaluate_complaint_intent(malformed)
    assert res_mal["order_id"] is None
    assert res_mal["customer_id"] is None
    assert res_mal["transaction_id"] is None


@pytest.mark.parametrize(
    "billing_text",
    [
        "I was charged twice $49 for order ORD-88219. Please refund.",
        "Double debit on my card for subscription renewal",
        "Duplicate charge occurred yesterday on my statement",
        "Please issue a refund for the accidental extra charge",
        "I was overcharged by $49.00 on my account",
        "My account was debited twice for order ORD-81094",
        "Unauthorized charge on my account, please reverse",
    ],
)
def test_evaluate_intent_billing_disputes(billing_text):
    """Verifies that billing dispute queries reliably evaluate to database_remediation."""
    res = Troy.evaluate_complaint_intent(billing_text)
    assert res["intent"] == "database_remediation"
    assert res["requires_db_mutation"] is True
    assert res["is_billing_dispute"] is True


# ==============================================================================
# CHALLENGER SUITE 2: Empirical Zero-Mutation Verification on General Inquiries
# ==============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "inquiry_ticket",
    [
        {
            "title": "2FA Authentication Setup Help",
            "description": "How do I set up two-factor authentication (2FA) with Google Authenticator on my account?",
            "category": "Authentication & Access",
        },
        {
            "title": "Android PDF Export Crash",
            "description": "The Android application crashes with an unhandled exception whenever I click Export PDF on the reports screen.",
            "category": "Bugs & Errors",
        },
        {
            "title": "Webhook Documentation Request",
            "description": "Where can I find complete documentation for configuring outbound webhook endpoints and signature verification?",
            "category": "Integrations & API",
        },
        {
            "title": "Profile Email Update Inquiry",
            "description": "I need assistance updating the primary email address and account owner details for our organization.",
            "category": "Account Management",
        },
        {
            "title": "Dashboard Query Latency",
            "description": "The analytics dashboard has high latency and takes over 20 seconds to load metrics during morning peak hours.",
            "category": "Performance",
        },
        {
            "title": "Contact Synchronization Error",
            "description": "My contacts are failing to sync between the desktop macOS client and the mobile application.",
            "category": "Data Sync",
        },
        {
            "title": "Team Member Onboarding Guide",
            "description": "What is the procedure to invite new teammates and assign granular workspace permissions?",
            "category": "Onboarding & How-To",
        },
        {
            "title": "Phishing Email Security Report",
            "description": "I received an email claiming to be from security asking for my credentials. Forwarding for investigation.",
            "category": "Security",
        },
    ],
)
async def test_zero_mutation_during_general_inquiry_processing(inquiry_ticket, isolated_database):
    """
    EMPIRICAL PROOF OF ZERO MUTATION:
    Runs Troy end-to-end on non-billing inquiries and checks SHA256 hashes and row counts
    of transactions.csv and customer_accounts.csv before and after execution.
    """
    tx_file = isolated_database["transactions_file"]
    acc_file = isolated_database["accounts_file"]

    # Record BEFORE state
    tx_hash_before = compute_sha256(tx_file)
    acc_hash_before = compute_sha256(acc_file)
    tx_rows_before = len(read_all_transactions())
    acc_rows_before = len(read_all_customers())
    tx_bytes_before = tx_file.read_bytes()
    acc_bytes_before = acc_file.read_bytes()

    # Initialize Troy with MockDriver and CapturingSseManager
    sse_capturer = CapturingSseManager()
    driver = MockDriver()
    troy = Troy(
        config=settings,
        driver=driver,
        sse_manager=sse_capturer,
        state=SimulationState(),
        enable_barrier=False,
    )

    # Execute full pipeline
    success = await troy.run(inquiry_ticket, driver=driver)
    assert success is True, f"Troy.run failed for inquiry: {inquiry_ticket['title']}"

    # Record AFTER state
    tx_hash_after = compute_sha256(tx_file)
    acc_hash_after = compute_sha256(acc_file)
    tx_rows_after = len(read_all_transactions())
    acc_rows_after = len(read_all_customers())
    tx_bytes_after = tx_file.read_bytes()
    acc_bytes_after = acc_file.read_bytes()

    # EMPIRICAL ZERO MUTATION CHECKS
    assert tx_hash_before == tx_hash_after, (
        f"VIOLATION: transactions.csv hash changed during inquiry '{inquiry_ticket['title']}'! "
        f"Before: {tx_hash_before}, After: {tx_hash_after}"
    )
    assert acc_hash_before == acc_hash_after, (
        f"VIOLATION: customer_accounts.csv hash changed during inquiry '{inquiry_ticket['title']}'! "
        f"Before: {acc_hash_before}, After: {acc_hash_after}"
    )
    assert tx_rows_before == tx_rows_after, "Transaction row count altered!"
    assert acc_rows_before == acc_rows_after, "Customer account row count altered!"
    assert tx_bytes_before == tx_bytes_after, "transactions.csv raw bytes altered!"
    assert acc_bytes_before == acc_bytes_after, "customer_accounts.csv raw bytes altered!"

    # SSE Event Auditing
    event_names = [e["event"] for e in sse_capturer.captured_events]
    assert "database_updated" not in event_names, (
        f"VIOLATION: 'database_updated' SSE event was broadcast during general inquiry '{inquiry_ticket['title']}'!"
    )

    # Check for direct inquiry confirmation in system messages
    sys_msgs = [e["data"].get("message", "") for e in sse_capturer.captured_events if e["event"] == "system"]
    assert any("Resolving without database mutation" in m for m in sys_msgs), (
        f"Missing 'Resolving without database mutation' system event in: {sys_msgs}"
    )


# ==============================================================================
# CHALLENGER SUITE 3: Contrast Verification - Billing Inquiries DO Mutate and are Idempotent
# ==============================================================================


@pytest.mark.asyncio
async def test_billing_inquiry_executes_mutation_and_is_idempotent(isolated_database):
    """
    CONTRAST TEST:
    Verifies that a duplicate charge inquiry DOES execute remediation on first run,
    and running it a SECOND time is IDEMPOTENT (no secondary corruption or balance distortion).
    """
    tx_file = isolated_database["transactions_file"]
    acc_file = isolated_database["accounts_file"]

    # Run 1: Emily Watson duplicate charge ORD-81094 / TXN-7712 (initially Pending/Unremediated)
    billing_ticket = {
        "title": "Double debit for ORD-81094",
        "description": "I was debited twice $49.00 for order ORD-81094. Duplicate charge TXN-7712 needs refund.",
        "category": "Refunds",
    }

    sse_1 = CapturingSseManager()
    driver = MockDriver()
    troy_1 = Troy(
        config=settings,
        driver=driver,
        sse_manager=sse_1,
        state=SimulationState(),
        enable_barrier=False,
    )

    tx_hash_0 = compute_sha256(tx_file)
    acc_hash_0 = compute_sha256(acc_file)

    res_1 = await troy_1.run(billing_ticket, driver=driver)
    assert res_1 is True

    tx_hash_1 = compute_sha256(tx_file)
    acc_hash_1 = compute_sha256(acc_file)

    # Verify that database WAS modified on first run
    assert tx_hash_0 != tx_hash_1, "Expected transactions.csv to be updated for billing dispute, but hash was unchanged"
    assert acc_hash_0 != acc_hash_1, "Expected customer_accounts.csv to be updated for billing dispute, but hash was unchanged"

    # Verify transaction status changed to Refunded
    txns_1 = read_all_transactions()
    txn_7712 = next((t for t in txns_1 if t.get("transaction_id") == "TXN-7712"), None)
    assert txn_7712 is not None
    assert txn_7712.get("status") == "Refunded"

    # Verify database_updated SSE event was emitted
    event_names_1 = [e["event"] for e in sse_1.captured_events]
    assert "database_updated" in event_names_1

    # Run 2: Same billing ticket executed again (Idempotency test)
    sse_2 = CapturingSseManager()
    troy_2 = Troy(
        config=settings,
        driver=driver,
        sse_manager=sse_2,
        state=SimulationState(),
        enable_barrier=False,
    )

    res_2 = await troy_2.run(billing_ticket, driver=driver)
    assert res_2 is True

    tx_hash_2 = compute_sha256(tx_file)
    acc_hash_2 = compute_sha256(acc_file)

    # Since already refunded, transactions and accounts should NOT be mutated again
    assert tx_hash_1 == tx_hash_2, "Idempotency violation: transactions.csv mutated again on already-refunded charge"
    assert acc_hash_1 == acc_hash_2, "Idempotency violation: customer_accounts.csv mutated again on already-refunded charge"

    # Verify already-refunded message in SSE system events
    sys_msgs_2 = [e["data"].get("message", "") for e in sse_2.captured_events if e["event"] == "system"]
    assert any("already refunded" in m for m in sys_msgs_2), f"Expected 'already refunded' in system messages: {sys_msgs_2}"


# ==============================================================================
# CHALLENGER SUITE 4: Response Formulation & UI Section Headings Compliance
# ==============================================================================


@pytest.mark.asyncio
async def test_response_formulation_all_4_sections_and_headings(isolated_database):
    """
    Verifies that Troy generates all 4 required resolution sections with bold markdown headings:
    Section 1: Executive Assessment & Category Confirmation
    Section 2: Root Cause Analysis & Precedent Alignment
    Section 3: Customer Communication Strategy
    Section 4: Internal Routing & Next Action Steps
    """
    ticket = {
        "title": "2FA SMS Delivery Failure",
        "description": "My 2FA verification SMS is not arriving when trying to log in.",
        "category": "Authentication & Access",
    }

    sse = CapturingSseManager()
    driver = MockDriver()
    troy = Troy(
        config=settings,
        driver=driver,
        sse_manager=sse,
        state=SimulationState(),
        enable_barrier=False,
    )

    ok = await troy.run(ticket, driver=driver)
    assert ok is True

    # Find resolution_end event
    res_end = next((e for e in sse.captured_events if e["event"] == "resolution_end"), None)
    assert res_end is not None, "resolution_end event was not broadcast"

    data = res_end["data"]
    sections = data.get("sections", {})
    assert "executive" in sections
    assert "root_cause" in sections
    assert "customer_comms" in sections
    assert "internal_routing" in sections

    # Check headings
    assert sections["executive"].startswith("### **Executive Assessment & Category Confirmation**")
    assert sections["root_cause"].startswith("### **Root Cause Analysis & Precedent Alignment**")
    assert sections["customer_comms"].startswith("### **Customer Communication Strategy**")
    assert sections["internal_routing"].startswith("### **Internal Routing & Next Action Steps**")

    # Check customer comms structure: greeting, reassuring body, professional sign-off
    comms = sections["customer_comms"]
    assert "Dear Valued Customer" in comms or "Dear" in comms
    assert "Troy" in comms
    assert "Lead Complaint Resolution Agent" in comms

    # Inquiries must NOT mention transactions.csv or refunds in their resolution text
    full_text = data.get("full_resolution", "")
    assert "transactions.csv" not in full_text.lower(), "Technical inquiry resolution inappropriately mentioned transactions.csv"
