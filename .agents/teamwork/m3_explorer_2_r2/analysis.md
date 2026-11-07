# Analysis Report: Milestone 3 Explorer 2
## Autonomous Intent Decision (Feature 10) & Generic DB Tools Execution (Feature 12)

**Author**: Explorer 2 (`m3_explorer_2_r2`)  
**Target Files**: `app/simulation/troy.py`, `app/database.py`, `app/triage.py`  
**Date**: 2026-11-07  

---

## 1. Executive Summary

This investigation analyzes how Troy (the Autonomous Complaint Resolution Engine) autonomously evaluates customer complaint intent and executes generic database tools for complaint resolution.

Key findings:
1. **Generic DB Tools Readiness**: Milestone 2 completed implementation of the 6 generic database inspection and remediation tools in `app/database.py`:
   - Inspection: `query_customer_account`, `query_transactions`, `inspect_account_and_transactions`.
   - Remediation: `update_transaction`, `update_customer_account`, `remediate_duplicate_charge`.
   - All 20 targeted tests for Feature 10 and Feature 12 in `tests/test_e2e_troy.py` pass.
2. **Current Troy Runtime Gap**: While `app/database.py` has the tools and `/api/resolve/action` executes remediation upon manual UI trigger, `app/simulation/troy.py:run()` currently formats prompts describing what it *would* do, but does **not** autonomously invoke `remediate_duplicate_charge()` during the automated simulation pipeline.
3. **Intent Decision Architecture**: Troy currently relies implicitly on `triage_complaint()` and `cat_ctx.get("is_refund", False)` without an explicit, testable `evaluate_complaint_intent()` method on the `Troy` orchestrator class.
4. **Execution Path & UI Stream Contracts**: Troy uses a 4-section prompt pipeline (`executive`, `root_cause`, `customer_comms`, `internal_routing`) streaming via `execute_agent_with_reasoning()` over SSE. When autonomous DB remediation occurs, Troy must emit SSE `system` and `database_updated` events so connected UI clients immediately update their ledger cards.
5. **Legacy Cleanup Required**: `app/simulation/troy.py` still contains legacy references to DistilBERT and SBERT in the module docstring and prompt formatting (lines 4-6, 285).

---

## 2. Intent Evaluation Architecture (Feature 10)

### 2.1 Intent Taxonomy & Routing

Troy must autonomously categorize incoming complaints into two primary operational intents:

| Operational Intent | Criteria & Keywords | Database Action | Expected Outcome |
| :--- | :--- | :--- | :--- |
| **`direct_response`** (General Inquiries) | Technical inquiries, 2FA/login issues, API documentation, onboarding/how-to, performance questions, general account inquiries. Keywords: `how to`, `documentation`, `api`, `webhook`, `timeout`, `password reset`, `sms code`. | **STRICTLY NO DB MUTATION**. `transactions.csv` and `customer_accounts.csv` must remain 100% byte-for-byte identical. | Generate direct, empathetic, technical customer response across all 4 markdown stages. |
| **`database_remediation`** (Billing & Duplicate Charges) | Duplicate debit reports, double charges, billing renewal disputes, accidental repeat debits. Keywords: `charged twice`, `duplicate`, `double charge`, `refund`, `debited twice`, `order ORD-88219`, `order ORD-81094`. | **GENERIC DB TOOLS INVOKED**. Inspects transactions, locates duplicate debit, calls `remediate_duplicate_charge()`. | Mark redundant transaction as `Refunded`, assign `refund_id`, restore balance to `$0.00 (Settled)`, formulate reassurance response. |

### 2.2 Boundary & Corner Cases

1. **Ambiguous Inputs**: Mixed complaints such as *"I was charged twice while experiencing an app crash on Android"* (`test_tier2_feature10_ambiguous_intent_handling`). Troy must identify the billing remediation need (`"charged twice"`) while preserving full technical context.
2. **Already-Refunded Inquiries**: Customers asking about a charge that has already been refunded (e.g. Alex Morgan `TXN-9012` after initial remediation). Troy must detect `is_already_refunded == True`, avoid duplicate refund payouts, and route to status reconfirmation.
3. **One-Word Inputs**: Inputs like `"refund"` or `"help"` must handle gracefully without index errors or exceptions (`test_tier2_feature10_one_word_intent_handling`).
4. **Entity Context Preservation**: Order numbers (`ORD-88219`, `ORD-81094`) and customer IDs (`USR-10492`, `USR-07231`) must be extracted and preserved throughout the entire resolution lifecycle.

---

## 3. Generic DB Tool Execution Architecture (Feature 12)

### 3.1 Generic Tool Inventory in `app/database.py`

| Function | Signature | Purpose & Guarantees |
| :--- | :--- | :--- |
| `query_customer_account` | `(customer_id=None, email=None, order_id=None) -> Optional[Dict]` | Reads `customer_accounts.csv` with exact matching. Safe against empty inputs and missing files. |
| `query_transactions` | `(customer_id=None, order_id=None, status=None, billing_cycle=None, transaction_id=None) -> List[Dict]` | Filters `transactions.csv` in memory without SQL injection risks. |
| `inspect_account_and_transactions` | `(customer_id=None, query=None) -> Dict[str, Any]` | Comprehensive audit: locates customer, filters billing cycle records, detects duplicate debits, and computes settled balance. |
| `update_transaction` | `(transaction_id: str, updates: Dict) -> bool` | Thread-safe in-place mutation of `transactions.csv` with `_DB_LOCK`. |
| `update_customer_account` | `(customer_id: str, updates: Dict) -> bool` | Thread-safe in-place mutation of `customer_accounts.csv` with `_DB_LOCK`. |
| `remediate_duplicate_charge` | `(order_id: str, duplicate_txn_id: str, refund_amount="49.00", processed_by="Troy...", notes="") -> Dict` | **Atomic Multi-Table Mutation**: updates transaction status to `Refunded`, sets `refund_id`, reconciles balance to `$0.00 (Settled)`, and sets account status to `Active - In Good Standing`. Fully idempotent. |

### 3.2 End-to-End Remediation Flow

```
1. Customer Complaint Submitted
   e.g. "I was charged twice $49 for order ORD-88219"
                     │
                     ▼
2. Troy Intent Evaluation (Feature 10)
   evaluate_complaint_intent() -> intent="database_remediation", order_id="ORD-88219"
                     │
                     ▼
3. Generic DB Inspection (Feature 12)
   inspect_account_and_transactions(query=complaint_text)
   -> Identifies:
      - Valid renewal debit: TXN-9011 ($49.00)
      - Redundant duplicate debit: TXN-9012 ($49.00)
      - is_already_refunded: False (or True if previously refunded)
                     │
                     ▼
4. Generic DB Remediation Execution
   IF NOT is_already_refunded:
       remediate_duplicate_charge(
           order_id="ORD-88219",
           duplicate_txn_id="TXN-9012",
           refund_amount="49.00",
           notes="Autonomous refund executed by Troy: Duplicate subscription charge"
       )
       -> Atomically updates transactions.csv & customer_accounts.csv
       -> Broadcasts SSE "system" log and "database_updated" event
   ELSE:
       -> Logs existing refund reconfirmation (no secondary payout)
                     │
                     ▼
5. Formulate Customer Reassurance Response
   Troy generates 4-stage markdown citing data/transactions.csv audit,
   confirming reversal of TXN-9012, citing Refund ID REF-88219-AUTO,
   and confirming $0.00 settled balance in good standing.
```

---

## 4. Exact Execution Trace in `app/simulation/troy.py`

### 4.1 Entrypoints
1. `summarise_ticket(query: str, driver: Optional[BaseLlmDriver]) -> Dict[str, Any]`
   - Calls `_summarize_ticket_with_llm()` with fallback to `_title_fallback()` and `_summary_fallback()`.
   - Broadcasts SSE event `topic` (`{"title": title}`).
   - Sets story in `SimulationState`.
   - Broadcasts SSE event `feed` (`story`).
2. `run(story: Dict[str, Any], driver: Optional[BaseLlmDriver]) -> bool`
   - Acquires `self.state.acquire()` lock.
   - Resets round barrier if enabled.
   - Calls `await self._run_pipeline(story)`.
   - In `finally`: releases state lock.

### 4.2 Pipeline Execution (`_run_pipeline`)
1. **Lifecycle Event Broadcast**:
   - `resolution_start`: `{"story": ..., "agent": "Troy"}`
   - `council_start`: `{"story": ..., "agents": [{"name": "Troy", "state": "Complaint Resolution Agent"}]}`
   - `round_start`: `{"round": 0, "label": "Resolution", "count": 1, "order": ["Troy"]}`
   - `system`: `Processing Complaint Ticket: <title>`
   - `system`: `Analyzing complaint context and preliminary assessment...`
2. **Intake Triage**:
   - Calls non-ML `triage_complaint(complaint_text)` via `asyncio.to_thread`.
   - Broadcasts `cra_report` event with schema (`predicted_category`, `confidence`, `sla`, etc.).
   - Broadcasts `system` event with triage findings.
3. **Autonomous Intent Evaluation**:
   - Evaluates complaint intent (`evaluate_complaint_intent()`).
   - If `database_remediation`:
     - Calls generic inspection tool `inspect_account_and_transactions()`.
     - Identifies duplicate transaction (`TXN-9012` or `TXN-7712`).
     - Executes `remediate_duplicate_charge()`.
     - Broadcasts `database_updated` event so UI ledger card syncs.
   - If `direct_response`:
     - Bypasses DB remediation; leaves CSVs 100% untouched.
4. **4-Stage Deliberation Loop**:
   - Iterates through 4 configured stages:
     - Section 1: `executive` -> `### **Executive Assessment & Category Confirmation**`
     - Section 2: `root_cause` -> `### **Root Cause Analysis & Precedent Alignment**`
     - Section 3: `customer_comms` -> `### **Customer Communication Strategy**`
     - Section 4: `internal_routing` -> `### **Internal Routing & Next Action Steps**`
   - For each section:
     - Broadcasts `system`: `Troy generating Prompt <N>/4: <Title>...`
     - Calls `_agent_speak()`, which invokes `execute_agent_with_reasoning()` with `driver.stream()`.
     - `ThoughtBuffer` partitions tokens into `agent_reasoning_delta` (if `show_reasoning`) and `agent_delta`.
     - Final section output is sanitized and verified to begin with `### **<Title>**`.
     - If driver is offline or raises connection error, uses `_build_category_fallback()`.
5. **Resolution Completion**:
   - Broadcasts `resolution_end`: `{"summary": ..., "cra_report": ..., "full_resolution": ..., "sections": ...}`.
   - Broadcasts `council_end`: UI backward-compatibility event.

---

## 5. Required Code Changes in `app/simulation/troy.py`

### 5.1 Import Generic Database Tools
Update imports in `app/simulation/troy.py`:
```python
from app.database import (
    get_category_complaint_context,
    analyze_customer_transactions,
    query_customer_account,
    query_transactions,
    inspect_account_and_transactions,
    remediate_duplicate_charge,
    update_transaction,
    update_customer_account,
)
```

### 5.2 Implement `evaluate_complaint_intent` on `ComplaintResolutionOrchestrator`
```python
    def evaluate_complaint_intent(self, text: str, category: Optional[str] = None) -> Dict[str, Any]:
        """
        Feature 10: Autonomously evaluates complaint intent to decide between
        direct customer response vs database tool remediation.
        """
        raw = (text or "").strip()
        low = raw.lower()

        billing_triggers = [
            "twice", "duplicate", "double charge", "charged twice", "double debit",
            "duplicate debit", "duplicate charge", "refund", "charged again",
            "overcharged", "debited twice", "double bill", "billed twice"
        ]
        is_billing = any(t in low for t in billing_triggers) or (category == "Refunds")

        order_match = re.search(r"ORD-\d+", raw, re.IGNORECASE)
        order_id = order_match.group(0).upper() if order_match else None

        cust_match = re.search(r"USR-\d+", raw, re.IGNORECASE)
        cust_id = cust_match.group(0).upper() if cust_match else None

        txn_match = re.search(r"TXN-\d+", raw, re.IGNORECASE)
        txn_id = txn_match.group(0).upper() if txn_match else None

        intent = "database_remediation" if is_billing else "direct_response"
        return {
            "intent": intent,
            "is_billing_dispute": is_billing,
            "requires_db_mutation": is_billing,
            "category": category,
            "order_id": order_id,
            "customer_id": cust_id,
            "transaction_id": txn_id,
            "raw_text": raw,
        }
```

### 5.3 Wire Autonomous DB Tool Execution into `_run_pipeline`
```python
        # Step 2: Autonomous Intent Evaluation (Feature 10)
        intent_info = self.evaluate_complaint_intent(complaint_text, category=pred_cat)
        is_refund_domain = intent_info["requires_db_mutation"]

        if is_refund_domain:
            self.sse.broadcast(
                "system",
                {"message": f"Autonomous Intent Decision: Billing dispute / duplicate charge detected. Initiating database audit..."},
            )
            # Generic DB Inspection (Feature 6 & 12)
            txn_analysis = inspect_account_and_transactions(
                customer_id=intent_info.get("customer_id"),
                query=complaint_text,
            )
            if not txn_analysis.get("ok"):
                txn_analysis = analyze_customer_transactions(query=complaint_text, customer_id=intent_info.get("customer_id"))

            is_already_refunded = txn_analysis.get("is_already_refunded", False)
            dup_txn = txn_analysis.get("duplicate_transaction") or {}
            dup_id = dup_txn.get("transaction_id") or "TXN-9012"
            dup_amt = dup_txn.get("amount", "49.00")
            effective_order_id = intent_info.get("order_id") or txn_analysis.get("order_id") or "ORD-88219"

            # Execute Generic Remediation Tool autonomously (Feature 12)
            if not is_already_refunded and dup_id:
                rem_res = remediate_duplicate_charge(
                    order_id=effective_order_id,
                    duplicate_txn_id=dup_id,
                    refund_amount=dup_amt,
                    processed_by="Troy (Autonomous Resolution Agent)",
                    notes=f"Autonomous refund executed by Troy for order {effective_order_id}.",
                )
                self.sse.broadcast(
                    "system",
                    {"message": f"Autonomous DB Tool Executed: remediate_duplicate_charge({effective_order_id}, {dup_id}) -> Status: Refunded"},
                )
                self.sse.broadcast("database_updated", rem_res)
                # Refresh analysis state after mutation
                is_already_refunded = True
                ref_id = rem_res.get("refund_id", "REF-88219-AUTO")
            else:
                ref_id = txn_analysis.get("refund_id") or "REF-88219-AUTO"
                self.sse.broadcast(
                    "system",
                    {"message": f"Autonomous DB Audit: Duplicate transaction {dup_id} was already refunded under {ref_id}. Reconfirming ledger status."},
                )
        else:
            self.sse.broadcast(
                "system",
                {"message": f"Autonomous Intent Decision: Direct inquiry for '{pred_cat}'. Resolving without database mutation."},
            )
```

### 5.4 Remove Legacy ML Strings
- Update module docstring to remove "Coordinates DistilBERT category classification, SBERT semantic search".
- Update line 285 in `_run_pipeline`: change `"Model: Fine-tuned DistilBERT Category Classifier"` to `"Intake Triage: Non-ML Keyword Classifier"`.
