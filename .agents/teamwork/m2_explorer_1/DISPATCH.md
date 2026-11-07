# Dispatch: Milestone 2 Explorer 1 (Query Tools & Unbiased Search)

Working directory: D:\CRA\.agents\teamwork\m2_explorer_1\
Original request file: D:\CRA\.agents\teamwork\ORIGINAL_REQUEST.md
Scope document: D:\CRA\PROJECT.md

## Task
Analyze query tools and unbiased search for Milestone 2 (Feature 5, Feature 6):
1. Investigate the hardcoded query fallback bug in `app/database.py:396-398` that silently defaults to Alex Morgan (`USR-10492`). Detail the fix so queries for Emily Watson (`USR-07231`), Sarah Jenkins (`USR-08914`), or unknown users return accurate results without falling back to Alex Morgan.
2. Detail the exact design and signature for generic inspection tools:
   - `query_customer_account(customer_id, email, order_id)`
   - `query_transactions(customer_id, order_id, status, billing_cycle)`
   - `inspect_account_and_transactions(customer_id, query)`
3. Provide step-by-step instructions for the Worker in `analysis.md` and `handoff.md`.
