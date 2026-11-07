# Handoff Report: Troy Autonomous Resolution Engine & Database Tool Execution

**Author**: Milestone 3 Explorer 2 (`m3_explorer_2_g2`)  
**Scope**: Features 10 & 12 (Autonomous Intent Decision, Database Tool Execution, Duplicate Charge Remediation Workflow, General Inquiries vs Billing Disputes)  
**Target Files**: `app/simulation/troy.py`, `app/database.py`, `tests/test_e2e_troy.py`  
**Handoff Type**: Hard (Investigation complete)  

---

## 1. Observation

1. **`app/database.py` generic inspection and remediation tools**:
   - `inspect_account_and_transactions(customer_id, query)` (`app/database.py:902-998`): Correctly inspects customer accounts, detects duplicate settled transactions in a billing cycle (`duplicate_detected`), flags already-refunded items (`is_already_refunded`), and returns candidates `duplicate_transaction` and `retained_transaction`.
   - Verified via tool execution:
     - For Emily Watson `ORD-81094`: returned `customer_id: USR-07231`, `duplicate_detected: True`, `is_already_refunded: False`, `dup_txn: TXN-7712`, `ret_txn: TXN-7711`.
     - For Alex Morgan `ORD-88219`: returned `customer_id: USR-10492`, `duplicate_detected: False`, `is_already_refunded: True`, `dup_txn: TXN-9012`.
     - For General Inquiries ("Where can I find documentation for the webhook API integration?"): returned `ok: False`, `duplicate_detected: False`, `is_already_refunded: False`.
   - `remediate_duplicate_charge(order_id, duplicate_txn_id, ...)` (`app/database.py:520-697`): Atomically and idempotently marks transaction as `Refunded`, assigns `refund_id`, restores customer account balance to `$0.00 (Settled)`, and sets account status to `Active - In Good Standing`.
2. **`app/simulation/troy.py` currently lacks autonomous tool mutation**:
   - In `app/simulation/troy.py:303-345`, `_run_pipeline()` reads transaction analysis for prompt assembly, but **never invokes `remediate_duplicate_charge`** during `run()`.
   - Database tools are only currently executed if an external caller or UI button hits `POST /api/resolve/action`.
   - `Troy` lacks an explicit `classify_intent` / `decide_intent` method to classify billing disputes vs general inquiries.
3. **`tests/test_e2e_troy.py` validation**:
   - Running `pytest tests/test_e2e_troy.py -v -k "feature10 or feature12 or tier3 or tier4"` yielded **32 PASSED**, 1 FAILED.
   - The single failure (`test_tier3_lmstudio_custom_url_with_triage_and_verify`) was due to Feature 9 (port 1235 vs 1234 default in `app/providers/__init__.py`), which Explorer 1 is addressing.
   - All Feature 10 and Feature 12 tests passed:
     - `test_tier1_feature10_billing_duplicate_intent_routes_to_db_tool` PASSED
     - `test_tier1_feature10_general_inquiry_routes_to_direct_response` PASSED
     - `test_tier1_feature10_billing_dispute_executes_remediation` PASSED
     - `test_tier1_feature10_general_inquiry_leaves_transactions_untouched` PASSED
     - `test_tier1_feature12_e2e_alex_morgan_duplicate_remediation` PASSED
     - `test_tier1_feature12_e2e_emily_watson_duplicate_remediation` PASSED
     - `test_tier1_feature12_account_balance_settled_to_zero` PASSED
     - `test_tier2_feature12_second_run_idempotent` PASSED
     - `test_tier4_scenario1_alex_morgan_duplicate_charge` PASSED
     - `test_tier4_scenario2_emily_watson_duplicate_charge` PASSED
     - `test_tier4_scenario3_general_inquiry_technical_no_db_modification` PASSED
     - `test_tier4_scenario4_offline_fallback_mock` PASSED
     - `test_tier4_scenario5_frontend_ui_api_compatibility` PASSED
4. **UI Contracts and Compatibility**:
   - `public/js/app.js` listens for SSE events: `topic`, `feed`, `cra_report`, `agent_start`, `agent_delta`, `agent`, `resolution_end`, and `database_updated`.
   - UI expects the 4-stage markdown format: `### **Executive Assessment & Category Confirmation**`, `### **Root Cause Analysis & Precedent Alignment**`, `### **Customer Communication Strategy**`, and `### **Internal Routing & Next Action Steps**`.

---

## 2. Logic Chain

1. **Step 1 (From Observation 1 & 2)**: Since `app/database.py` already contains verified, thread-safe, atomic functions (`inspect_account_and_transactions`, `remediate_duplicate_charge`), but `app/simulation/troy.py` does not currently invoke them during `run()`, Troy's resolution engine must be wired up to autonomously execute these tools during pipeline execution when a billing dispute is detected.
2. **Step 2 (From Observation 1 & 3)**: A formal `classify_intent(query, category)` method on `Troy` is necessary to separate general inquiries from billing disputes:
   - For general inquiries (e.g. 2FA, login, bug, API, onboarding), `requires_db_mutation` must be `False`, ensuring `transactions.csv` and `customer_accounts.csv` remain strictly unmodified (`before == after`).
   - For billing disputes (duplicate charges, double debits, refunds, order disputes), `requires_db_mutation` must be `True`, triggering inspection and remediation.
3. **Step 3 (From Observation 1 & 3)**: When `requires_db_mutation` is `True`, Troy must call `inspect_account_and_transactions`. If `duplicate_detected` is `True` and `is_already_refunded` is `False`, Troy must immediately call `remediate_duplicate_charge()`, broadcast an SSE update (`system` message and `database_updated` event), and use the resulting `refund_id` and settled status in the prompt and customer response.
4. **Step 4 (From Observation 1 & 3)**: Because `remediate_duplicate_charge()` is idempotent, if an already-refunded ticket is re-inspected (such as Alex Morgan's `TXN-9012`), Troy detects `is_already_refunded=True`, blocks duplicate payouts, and reconfirms the existing refund.
5. **Step 5 (From Observation 2 & 4)**: `app/database.py:execute_agent_resolution()` should be harmonized to delegate to `remediate_duplicate_charge()` for refunds, ensuring DRY execution and preventing inconsistencies between the autonomous agent and manual UI trigger.

---

## 3. Caveats

1. **Local LLM Endpoint (Feature 9)**: The default endpoint port in `app/providers/__init__.py` is currently `1235` instead of `1234` and does not dynamically check `LMSTUDIO_BASE_URL`. This is assigned to Milestone 3 Explorer 1 / Worker 1.
2. **Mock Fallback vs Live LM Studio**: When LM Studio is running on `http://localhost:1234/v1`, Troy uses `OpenAiDriver`. When offline, Troy falls back to deterministic mock streaming. The database tool execution in `_run_pipeline()` executes deterministically in both modes prior to prompt completion.
3. **No Frontend UI Modifications**: Static files in `public/` are strictly read-only and frozen. All architectural adjustments are strictly inside backend simulation and provider layers.

---

## 4. Conclusion

1. Implement `Troy.classify_intent(query, category)` and `Troy.decide_intent` in `app/simulation/troy.py` to route complaints between direct customer responses (for general inquiries) and database tool execution (for billing disputes).
2. Implement `Troy.audit_and_remediate(complaint_text, ...)` in `app/simulation/troy.py` to inspect customer transactions via `inspect_account_and_transactions`, execute `remediate_duplicate_charge` when redundant debits exist, broadcast `database_updated` SSE events, and prevent secondary payouts when already settled.
3. Guarantee zero database mutations for non-billing categories (`Account Access / 2FA`, `Integrations & API`, `Bugs & Errors`, etc.).
4. Make `_build_category_fallback` fully dynamic for all customers (Alex Morgan `ORD-88219`, Emily Watson `ORD-81094`, and generic accounts), ensuring the 4-stage bold markdown headings are preserved.
5. Refactor `execute_agent_resolution` in `app/database.py` to delegate to `remediate_duplicate_charge`.

---

## 5. Verification Method

To independently verify the implementation:
1. **Pytest Feature 10 & 12 Execution**:
   ```powershell
   pytest tests/test_e2e_troy.py -v -k "feature10 or feature12 or tier3 or tier4"
   ```
2. **API & Category Resolutions Verification**:
   ```powershell
   pytest tests/test_category_resolutions.py tests/test_api_endpoints.py
   pytest tests/test_challenger2_m2.py
   ```
3. **Verify Zero Mutation on General Inquiries**:
   Inspect `test_tier1_feature10_general_inquiry_leaves_transactions_untouched` and `test_tier4_scenario3_general_inquiry_technical_no_db_modification` to verify `transactions.csv` and `customer_accounts.csv` remain unmodified.
4. **Verify Dynamic Dual-Customer Duplicate Charge Remediation**:
   Inspect `test_tier4_scenario1_alex_morgan_duplicate_charge` (`TXN-9012` refunded) and `test_tier4_scenario2_emily_watson_duplicate_charge` (`TXN-7712` refunded, account status active).
5. **Code Inspection**:
   Inspect `app/simulation/troy.py` for `classify_intent`, `audit_and_remediate`, and invocation of `inspect_account_and_transactions` and `remediate_duplicate_charge`.
