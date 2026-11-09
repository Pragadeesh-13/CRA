"""
Real-world example: plugging Troy into a SaaS product ("CloudScribe AI").

Self-contained and runnable with no network access and no API keys:

    python examples/saas_integration_example.py

It demonstrates, end to end:
  1. Creating a SaaSConfig product profile for "CloudScribe AI"
     (custom entity patterns, categories/SLAs, approval policy).
  2. Connecting a data-store adapter (InMemory here; swap in
     CsvDataAdapter or RestApiDataAdapter in production).
  3. Registering custom domain tools ('reset_api_quota' and
     'issue_credit_refund') on the client.
  4. Running ticket resolutions autonomously with TroyClient:
     an automatic duplicate-charge refund, a custom API-quota tool
     invocation, and a dry-run preview that mutates nothing.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# Allow running as `python examples/saas_integration_example.py` from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.adapter import InMemoryDataAdapter
from app.core.saas_config import SaaSConfig
from app.sdk import TroyClient


def build_config() -> SaaSConfig:
    """Product profile for CloudScribe AI."""
    return SaaSConfig(
        product_name="CloudScribe AI",
        domain="cloudscribe.ai",
        support_email="support@cloudscribe.ai",
        entity_patterns={
            "orders": [r"ORD-\d+"],
            "customers": [r"USR-\d+"],
            "transactions": [r"TXN-\d+"],
            "charges": [r"ch_\d+"],
            "invoices": [r"INV-\d+"],
            "organizations": [r"org_\d+"],
        },
        categories=[
            {"name": "Refunds", "sla_hours": 2.0, "sla_risk": "Low", "team": "Billing"},
            {"name": "API Quota", "sla_hours": 4.0, "sla_risk": "Low", "team": "Engineering"},
            {"name": "Billing", "sla_hours": 8.0, "sla_risk": "Low", "team": "Billing"},
        ],
        policies={"auto_refund_limit": 100.0, "dry_run": False},
    )


def build_adapter() -> InMemoryDataAdapter:
    """Seeded demo data: two customers with duplicate subscription charges."""
    return InMemoryDataAdapter(
        customers=[
            {
                "customer_id": "USR-20481",
                "customer_name": "Maya Patel",
                "customer_email": "maya@acme.example",
                "reference_order_id": "ORD-73001",
                "account_status": "Active",
            },
            {
                "customer_id": "USR-20482",
                "customer_name": "Tom Nguyen",
                "customer_email": "tom@globex.example",
                "reference_order_id": "ORD-73002",
                "account_status": "Active",
            },
        ],
        transactions=[
            {
                "transaction_id": "TXN-5001",
                "customer_id": "USR-20481",
                "customer_name": "Maya Patel",
                "customer_email": "maya@acme.example",
                "order_id": "ORD-73001",
                "transaction_date": "2026-11-01 09:00:00",
                "amount": "79.00",
                "status": "Settled",
            },
            {
                "transaction_id": "TXN-5002",
                "customer_id": "USR-20481",
                "customer_name": "Maya Patel",
                "customer_email": "maya@acme.example",
                "order_id": "ORD-73001",
                "transaction_date": "2026-11-01 09:01:00",
                "amount": "79.00",
                "status": "Settled",
            },
            {
                "transaction_id": "TXN-6001",
                "customer_id": "USR-20482",
                "customer_name": "Tom Nguyen",
                "customer_email": "tom@globex.example",
                "order_id": "ORD-73002",
                "transaction_date": "2026-11-02 09:00:00",
                "amount": "59.00",
                "status": "Settled",
            },
            {
                "transaction_id": "TXN-6002",
                "customer_id": "USR-20482",
                "customer_name": "Tom Nguyen",
                "customer_email": "tom@globex.example",
                "order_id": "ORD-73002",
                "transaction_date": "2026-11-02 09:01:00",
                "amount": "59.00",
                "status": "Settled",
            },
        ],
    )


def register_custom_tools(client: TroyClient) -> None:
    """Register CloudScribe-specific actions on Troy."""

    @client.tool(
        name="reset_api_quota",
        description="Reset the API quota for a CloudScribe organization customer.",
        schema={
            "type": "object",
            "required": ["customer_id"],
            "properties": {"customer_id": {"type": "string"}},
        },
    )
    def reset_api_quota(customer_id: str = "", **kwargs) -> dict:
        result = client.adapter.execute_action(
            "update_customer_account",
            {
                "customer_id": customer_id,
                "updates": {
                    "account_status": "Active - API Quota Reset",
                    "notes": "API quota reset by Troy for organization org_88.",
                },
            },
        )
        return {
            "ok": bool(result.get("ok")),
            "customer_id": customer_id,
            "quota": "reset",
            "organization": "org_88",
        }

    def issue_credit_refund(transaction_id: str = "", amount: float = 0.0, **kwargs) -> dict:
        """Credit-refund a duplicate charge via the adapter's refund action."""
        order_id = kwargs.get("order_id", "")
        result = client.adapter.execute_action(
            "remediate_duplicate_charge",
            {
                "order_id": order_id,
                "duplicate_txn_id": transaction_id,
                "transaction_id": transaction_id,
                "refund_amount": str(amount or "79.00"),
                "processed_by": "Troy (CloudScribe AI Resolution Agent)",
            },
        )
        return result

    client.register_tool(
        issue_credit_refund,
        name="issue_credit_refund",
        description="Issue a credit refund for a duplicate CloudScribe charge.",
        schema={
            "type": "object",
            "required": ["transaction_id"],
            "properties": {
                "transaction_id": {"type": "string"},
                "order_id": {"type": "string"},
                "amount": {"type": "number"},
            },
        },
    )


def _summary(title: str, result: dict) -> None:
    print(f"\n=== {title} ===")
    print(
        json.dumps(
            {
                "ok": result.get("ok"),
                "status": result.get("status"),
                "product": result.get("product_name"),
                "category": result.get("category"),
                "customer_id": result.get("customer_id"),
                "order_id": result.get("order_id"),
                "refund_id": result.get("refund_id"),
                "action_status": result.get("action_status"),
                "dry_run": result.get("dry_run"),
            },
            indent=2,
        )
    )
    print(f"\nCustomer communication:\n{result.get('customer_communication')}")


async def main() -> None:
    client = TroyClient(config=build_config(), adapter=build_adapter())
    register_custom_tools(client)

    print(f"Troy for {client.config.product_name} is ready.")
    print(f"Registered tools: {client.tool_registry.tool_names()}")

    # 1. Autonomous duplicate-charge remediation (auto-executed refund).
    duplicate = await client.resolve_ticket(
        "I was charged twice for order ORD-73001, customer USR-20481, transaction TXN-5002",
        auto_execute=True,
    )
    _summary("1. Duplicate charge — autonomous refund", duplicate)

    # 2. Re-resolution is idempotent: the charge is already refunded.
    again = await client.resolve_ticket(
        "I was charged twice for order ORD-73001, customer USR-20481",
        auto_execute=True,
    )
    _summary("2. Same ticket again — already refunded (idempotent)", again)

    # 3. Custom domain tool: reset the API quota for organization org_88.
    quota = client.execute_tool("reset_api_quota", {"customer_id": "USR-20481"})
    print("\n=== 3. Custom tool: reset_api_quota ===")
    print(json.dumps(quota, indent=2, default=str))

    # 4. Dry-run preview for a second customer: nothing is mutated.
    preview = await client.resolve_ticket(
        "I was charged twice for order ORD-73002, customer USR-20482, transaction TXN-6002",
        dry_run=True,
    )
    _summary("4. Dry-run preview — no records changed", preview)

    tom_txns = client.adapter.get_transactions("USR-20482")
    assert all(t["status"] != "Refunded" for t in tom_txns), "dry-run must not mutate data"
    print("\nDry-run verified: Tom Nguyen's transactions are untouched.")


if __name__ == "__main__":
    asyncio.run(main())
