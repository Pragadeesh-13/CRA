"""Test-only deterministic LLM driver.

This mock exists solely for unit tests that need offline assertions.
Production code under ``app/`` has no mock fallback driver; the default
driver is ``OpenAiDriver(provider='lmstudio')`` (see ``app/sdk/client.py``).
"""

import asyncio
import json
import re
from typing import List, Dict, Any, Callable, Optional
from app.providers.base_driver import BaseLlmDriver


class MockDriver(BaseLlmDriver):
    def __init__(self, provider: str = "mock", **options: Any):
        super().__init__(provider=provider, **options)

    def _determine_response(self, messages: List[Dict[str, str]]) -> str:
        prompt_text = " ".join(m.get("content", "") for m in (messages or [])).lower()
        user_messages = [m.get("content", "") for m in (messages or []) if m.get("role") == "user"]
        latest_user_prompt = user_messages[-1].lower() if user_messages else prompt_text

        # 0. Triage and Case Assessment JSON (triage_complaint / predict_category)
        if any(k in prompt_text for k in ("predicted_category", "11 standard cra categories", "triage agent", "intake triage")):
            complaint_prompt = latest_user_prompt
            order_m = re.search(r"ord-\d+", prompt_text, re.IGNORECASE)
            order_id = order_m.group(0).upper() if order_m else None
            txn_m = re.search(r"txn-\d+", prompt_text, re.IGNORECASE)
            txn_id = txn_m.group(0).upper() if txn_m else None
            usr_m = re.search(r"usr-\d+", prompt_text, re.IGNORECASE)
            usr_id = usr_m.group(0).upper() if usr_m else None

            if any(k in complaint_prompt for k in ("double bill", "billed twice", "charged twice", "double charge", "duplicate charge", "duplicate debit", "double debit", "duplicate bill", "charged again", "refund", "overcharge", "debited twice", "88219", "81094", "twice")):
                cat = "Refunds"
                conf = 0.98
                team = "Billing"
                sla = {"estimated_hours": 3.5, "sla_target_hours": 24.0, "sla_risk": "Low", "priority": "P3 - Normal"}
                oid = order_id or ("ORD-81094" if "81094" in prompt_text else "ORD-88219")
                tid = txn_id or ("TXN-7712" if "81094" in prompt_text else "TXN-9012")
                entities = {
                    "references": [f"Order {oid}", tid],
                    "dates": ["yesterday"],
                    "amounts": ["$49.00"],
                    "products": ["checkout", "payment gateway"],
                    "order_id": oid,
                    "transaction_id": tid,
                    "customer_id": usr_id or ("USR-07231" if "81094" in prompt_text else "USR-10492"),
                    "account_number": None,
                    "merchant": "Stripe",
                }
                summary = f"Customer reports accidental duplicate billing for order {oid} and requests immediate refund remediation."
                similar = [
                    {"ticket_id": 81094, "description": "Customer charged twice for monthly subscription renewal under order ORD-81094.", "category": "Refunds", "resolution": "Identified duplicate transaction TXN-7712, processed $49.00 refund via Stripe API, restored account balance.", "assigned_team": "Billing", "resolution_time_hours": 3.5, "similarity": 0.94},
                    {"ticket_id": 88219, "description": "Order ORD-88219 debited twice on checkout due to connection retry.", "category": "Refunds", "resolution": "Refunded secondary charge TXN-9012, verified primary payment TXN-9011 settled cleanly.", "assigned_team": "Billing", "resolution_time_hours": 3.0, "similarity": 0.91},
                ]
            elif any(k in complaint_prompt for k in ("2fa", "two-factor", "mfa", "otp", "sms", "authenticator", "locked out", "account locked", "cannot log in", "can't log in", "login fail", "password", "sso", "79110", "access")):
                cat = "Authentication & Access"
                conf = 0.95
                team = "Security"
                sla = {"estimated_hours": 3.5, "sla_target_hours": 12.0, "sla_risk": "Low", "priority": "P2 - High"}
                entities = {"references": ["AUTH-9921"], "dates": ["today"], "amounts": [], "products": ["mobile app", "web portal"], "order_id": None, "transaction_id": None, "customer_id": usr_id or "USR-08914", "account_number": None, "merchant": None}
                summary = "Two-factor authentication SMS verification codes failing to deliver; account locked out."
                similar = [{"ticket_id": 79110, "description": "Two-factor authentication codes not delivered to mobile phone; account locked out.", "category": "Authentication & Access", "resolution": "Verified enterprise SSO credentials, issued temporary emergency bypass token AUTH-9921, reset lockout counter.", "assigned_team": "Security", "resolution_time_hours": 3.5, "similarity": 0.93}]
            elif any(k in complaint_prompt for k in ("crash", "500 error", "internal server error", "unhandled exception", "exception", "traceback", "stack trace", "null pointer", "pdf", "bug", "defect", "glitch", "broken")):
                cat = "Bugs & Errors"
                conf = 0.92
                team = "Engineering"
                sla = {"estimated_hours": 18.0, "sla_target_hours": 48.0, "sla_risk": "Medium", "priority": "P2 - High"}
                entities = {"references": ["BUG-ENG-4401"], "dates": ["today"], "amounts": [], "products": ["mobile app", "android"], "order_id": None, "transaction_id": None, "customer_id": usr_id, "account_number": None, "merchant": None}
                summary = "Mobile application crashes with HTTP 500 error during PDF report export on Android."
                similar = [{"ticket_id": 84401, "description": "Application crashes with HTTP 500 error when exporting large reports to PDF.", "category": "Bugs & Errors", "resolution": "Correlated Sentry crash log with memory buffer exception in client v2.4.1; logged BUG-ENG-4401 in sprint queue.", "assigned_team": "Engineering", "resolution_time_hours": 18.0, "similarity": 0.89}]
            elif any(k in complaint_prompt for k in ("api", "webhook", "rate limit", "429", "504", "gateway timeout", "93041", "rest api", "endpoint", "sdk", "integration")):
                cat = "Integrations & API"
                conf = 0.92
                team = "Engineering"
                sla = {"estimated_hours": 8.0, "sla_target_hours": 24.0, "sla_risk": "Low", "priority": "P2 - High"}
                entities = {"references": ["API-HOOK-7703"], "dates": ["today"], "amounts": [], "products": ["api"], "order_id": None, "transaction_id": None, "customer_id": usr_id or "USR-12053", "account_number": None, "merchant": None}
                summary = "Webhook delivery returning HTTP 504 gateway timeout and 429 burst rate limit exceeded."
                similar = [{"ticket_id": 93041, "description": "Webhook payloads dead-lettered due to HTTP 504 gateway timeout; burst rate limit 429 hit.", "category": "Integrations & API", "resolution": "Reset burst token bucket for API key, initiated automated replay of 12 dead-lettered events (API-HOOK-7703).", "assigned_team": "Engineering", "resolution_time_hours": 8.0, "similarity": 0.91}]
            elif any(k in complaint_prompt for k in ("sync", "offline queue", "desync", "divergence", "reconcil", "merge conflict", "replication")):
                cat = "Data Sync"
                conf = 0.91
                team = "Engineering"
                sla = {"estimated_hours": 12.0, "sla_target_hours": 48.0, "sla_risk": "Medium", "priority": "P3 - Normal"}
                entities = {"references": ["SYNC-RECON-2201"], "dates": ["today"], "amounts": [], "products": ["mobile app", "web portal"], "order_id": None, "transaction_id": None, "customer_id": usr_id, "account_number": None, "merchant": None}
                summary = "Data synchronization divergence detected between offline mobile queue and cloud database."
                similar = [{"ticket_id": 82201, "description": "Timestamp divergence detected between mobile client offline queue and cloud database.", "category": "Data Sync", "resolution": "Executed three-way merge resolution algorithm SYNC-RECON-2201, invalidated stale client cache.", "assigned_team": "Engineering", "resolution_time_hours": 12.0, "similarity": 0.90}]
            elif any(k in complaint_prompt for k in ("slow", "sluggish", "high latency", "latency", "load time", "loading", "bottleneck", "p99", "seconds to load", "timeout", "speed", "performance")):
                cat = "Performance"
                conf = 0.90
                team = "Engineering"
                sla = {"estimated_hours": 14.0, "sla_target_hours": 48.0, "sla_risk": "Medium", "priority": "P3 - Normal"}
                entities = {"references": ["PERF-OPT-3301"], "dates": ["today"], "amounts": [], "products": ["dashboard"], "order_id": None, "transaction_id": None, "customer_id": usr_id, "account_number": None, "merchant": None}
                summary = "Full-text search queries and dashboard charts exhibiting severe latency exceeding SLA thresholds."
                similar = [{"ticket_id": 83301, "description": "Full-text search queries exceeding SLA threshold with 42.8s response latency.", "category": "Performance", "resolution": "Invalidated stale search index cache, executed query defragmentation PERF-OPT-3301 (<450ms baseline).", "assigned_team": "Engineering", "resolution_time_hours": 14.0, "similarity": 0.90}]
            elif any(k in complaint_prompt for k in ("security", "unauthorized", "suspicious", "compromised", "breach", "threat", "malicious", "gdpr", "personal data", "deletion", "data deletion", "privacy", "ip allowlist", "jwt")):
                cat = "Security"
                conf = 0.95
                team = "Security"
                sla = {"estimated_hours": 2.0, "sla_target_hours": 8.0, "sla_risk": "Critical", "priority": "P1 - Critical"}
                entities = {"references": ["SEC-REVOKE-8812"], "dates": ["today"], "amounts": [], "products": ["web portal"], "order_id": None, "transaction_id": None, "customer_id": usr_id, "account_number": None, "merchant": None}
                summary = "Security incident report regarding anomalous authorization tokens or unauthorized account access."
                similar = [{"ticket_id": 88812, "description": "Anomalous authorization token detected from unverified IP range; possible breach.", "category": "Security", "resolution": "Revoked active JWT bearer tokens SEC-REVOKE-8812, enforced mandatory MFA challenge.", "assigned_team": "Security", "resolution_time_hours": 2.0, "similarity": 0.94}]
            elif any(k in complaint_prompt for k in ("downgrade", "proration", "prorated", "cancel", "renewal", "upgrade plan", "86102", "starter tier", "pro tier", "subscription")):
                cat = "Subscription"
                conf = 0.92
                team = "Billing"
                sla = {"estimated_hours": 3.0, "sla_target_hours": 24.0, "sla_risk": "Low", "priority": "P3 - Normal"}
                entities = {"references": ["SUB-ADJ-6601"], "dates": ["this month"], "amounts": ["$50.00"], "products": ["dashboard"], "order_id": None, "transaction_id": None, "customer_id": usr_id or "USR-09148", "account_number": None, "merchant": None}
                summary = "Customer requested plan tier downgrade and proration credit adjustments."
                similar = [{"ticket_id": 86102, "description": "Customer requested downgrade to Starter Tier prior to renewal; legacy renewal billed at full rate.", "category": "Subscription", "resolution": "Approved $50.00 prorated credit under SUB-ADJ-6601, adjusted recurring renewal tier to Starter ($29/mo).", "assigned_team": "Billing", "resolution_time_hours": 3.0, "similarity": 0.92}]
            elif any(k in complaint_prompt for k in ("vat", "tax invoice", "tax id", "vies", "invoice", "receipt", "declined", "card declined", "credit card", "402", "billing", "payment")):
                cat = "Billing"
                conf = 0.93
                team = "Billing"
                sla = {"estimated_hours": 4.5, "sla_target_hours": 24.0, "sla_risk": "Low", "priority": "P3 - Normal"}
                entities = {"references": ["INV-85420-VAT"], "dates": ["this month"], "amounts": [], "products": ["dashboard"], "order_id": None, "transaction_id": None, "customer_id": usr_id or "USR-14309", "account_number": None, "merchant": None}
                summary = "Customer requested formal German VAT ID addition and stamped tax invoice."
                similar = [{"ticket_id": 85420, "description": "Customer requested formal German VAT ID addition and stamped tax invoice for Q4 2026.", "category": "Billing", "resolution": "Validated European VIES registration, issued official stamped tax receipt and invoice INV-85420-VAT.", "assigned_team": "Billing", "resolution_time_hours": 4.5, "similarity": 0.92}]
            elif any(k in complaint_prompt for k in ("how to", "how do i", "get started", "getting started", "invite", "invitation", "tutorial", "guide", "dark mode", "feature request", "toggle", "csv export")):
                cat = "Onboarding & How-To"
                conf = 0.91
                team = "Tier 1 Support"
                sla = {"estimated_hours": 2.5, "sla_target_hours": 12.0, "sla_risk": "Low", "priority": "P4 - Low"}
                entities = {"references": ["GUIDE-ONB-1102"], "dates": ["today"], "amounts": [], "products": ["workspace"], "order_id": None, "transaction_id": None, "customer_id": usr_id, "account_number": None, "merchant": None}
                summary = "User requesting guidance on product features, team invitations, or workspace configuration."
                similar = [{"ticket_id": 81102, "description": "New workspace administrator requesting guidance on inviting team members and setting roles.", "category": "Onboarding & How-To", "resolution": "Dispatched interactive role delegation guide GUIDE-ONB-1102 and automated invitation template.", "assigned_team": "Tier 1 Support", "resolution_time_hours": 2.5, "similarity": 0.92}]
            else:
                cat = "Account Management"
                conf = 0.88
                team = "Tier 1 Support"
                sla = {"estimated_hours": 6.0, "sla_target_hours": 24.0, "sla_risk": "Low", "priority": "P3 - Normal"}
                entities = {"references": ["USR-DIR-8770"], "dates": ["today"], "amounts": [], "products": ["dashboard"], "order_id": None, "transaction_id": None, "customer_id": usr_id, "account_number": None, "merchant": None}
                summary = "Customer inquiry regarding organization profile consolidation or account settings."
                similar = [{"ticket_id": 87701, "description": "Customer requested organization profile consolidation and workspace ownership transfer.", "category": "Account Management", "resolution": "Consolidated duplicate enterprise account records, transferred admin rights to verified primary contact.", "assigned_team": "Tier 1 Support", "resolution_time_hours": 6.0, "similarity": 0.89}]

            top_3 = [
                {"category": cat, "score": round(conf, 4)},
                {"category": "Billing" if cat != "Billing" else "Refunds", "score": round(max(0.01, (1.0 - conf) * 0.7), 4)},
                {"category": "Account Management" if cat != "Account Management" else "Bugs & Errors", "score": round(max(0.01, (1.0 - conf) * 0.3), 4)},
            ]

            report = {
                "predicted_category": cat,
                "category_confidence": conf,
                "top_3_categories": top_3,
                "recommended_team": team,
                "team_confidence": "High",
                "team_method": "Local LLM (Deterministic Fallback)",
                "sla": sla,
                "predicted_sla_hours": sla["estimated_hours"],
                "extracted_entities": entities,
                "summary": summary,
                "similar_cases": similar,
            }
            return json.dumps(report, indent=2)

        # 1. Title and summary extraction JSON (summarise_ticket)
        if "title" in prompt_text and "summary" in prompt_text and ("json" in prompt_text or "keys" in prompt_text):
            if "pdf" in prompt_text or "crash" in prompt_text:
                return json.dumps({
                    "title": "Android PDF Export Crash",
                    "summary": "Customer reports that the mobile application crashes with an unhandled 500 error when clicking export PDF on Android."
                })
            if "ord-" in prompt_text or "charge" in prompt_text or "duplicate" in prompt_text or "refund" in prompt_text:
                order_m = re.search(r"ord-\d+", prompt_text, re.IGNORECASE)
                order_id = order_m.group(0).upper() if order_m else "ORD-88219"
                return json.dumps({
                    "title": f"Billing Dispute - Duplicate Charge ({order_id})",
                    "summary": f"Customer reports accidental duplicate billing for order {order_id} and requests immediate refund remediation."
                })
            return json.dumps({
                "title": "Customer Complaint Resolution Request",
                "summary": "Customer submitted an inquiry regarding account resolution and billing entitlements."
            })

        # 2. Concise title generation
        if "output only a 2-3 word title" in prompt_text or "title:" in prompt_text:
            return "Refund Request"

        # 3. Concise summary generation
        if "summarize" in prompt_text:
            return "Complaint regarding delayed delivery. Requesting full refund and expedited handling."

        # 4. Multi-prompt 4-stage resolution sections (Troy agent deliberation)
        is_inquiry = (
            "2fa" in prompt_text or "sms" in prompt_text or "password" in prompt_text
            or "documentation" in prompt_text or "webhook" in prompt_text or "login" in prompt_text
            or "access" in prompt_text or "crash" in prompt_text or "bug" in prompt_text
            or "technical support" in prompt_text
        )

        is_watson = "81094" in prompt_text or "emily" in prompt_text or "7712" in prompt_text
        order_id = "ORD-81094" if is_watson else "ORD-88219"
        cust_name = "Emily Watson" if is_watson else "Alex Morgan"
        c_first = "Emily" if is_watson else "Alex"
        dup_id = "TXN-7712" if is_watson else "TXN-9012"
        ret_id = "TXN-7711" if is_watson else "TXN-9011"
        ref_id = "REF-81094-AUTO" if is_watson else "REF-88219-AUTO"

        eval_prompt = latest_user_prompt if latest_user_prompt else prompt_text

        if "section 4" in eval_prompt or ("internal routing" in eval_prompt and "do not" not in eval_prompt):
            if is_inquiry:
                return (
                    "<thought>Logging operational audit trail and closing support ticket.</thought>"
                    "### **Internal Routing & Next Action Steps**\n\n"
                    "1. Dispatched telemetry alert to Identity & Security Operations.\n"
                    "2. Confirmed zero database mutation required for inquiries.\n"
                    "3. Ticket marked Resolved under technical support SLA."
                )
            return (
                "<thought>Recording database audit trail and transitioning ticket status.</thought>"
                "### **Internal Routing & Next Action Steps**\n\n"
                f"1. Database record in data/transactions.csv ({dup_id}) marked as Refunded.\n"
                f"2. Customer account balance for {cust_name} settled at $0.00.\n"
                "3. Resolution audit log dispatched to Billing & Finance team for reconciliation."
            )

        if "section 3" in eval_prompt or ("customer communication" in eval_prompt and "do not" not in eval_prompt):
            if is_inquiry:
                return (
                    "<thought>Drafting personalized technical guidance for the customer.</thought>"
                    "### **Customer Communication Strategy**\n\n"
                    "**Subject:** Update regarding your authentication verification code\n\n"
                    "Dear Valued Customer,\n\n"
                    "Thank you for contacting support. We have verified gateway traffic and refreshed your verification routing. "
                    "Please retry requesting your verification code now.\n\n"
                    "Sincerely,\nTroy (Autonomous Resolution Agent)\nLead Complaint Resolution Agent"
                )
            return (
                "<thought>Drafting personalized customer reassurance and confirmation of credit.</thought>"
                "### **Customer Communication Strategy**\n\n"
                f"**Subject:** Duplicate Charge Reversal Confirmation & Account Settled\n\n"
                f"Hello {c_first}, thank you for reaching out. We analyzed our transaction records in data/transactions.csv and confirmed an accidental duplicate charge of $49.00 occurred during renewal. We have reversed transaction {dup_id} immediately under refund reference {ref_id}. Your account balance is restored to $0.00 in good standing.\n\n"
                "Sincerely,\nTroy (Autonomous Resolution Agent)\nLead Complaint Resolution Agent"
            )

        if "section 2" in eval_prompt or ("root cause" in eval_prompt and "do not" not in eval_prompt):
            if is_inquiry:
                return (
                    "<thought>Investigating SMS gateway logs and delivery retry queues.</thought>"
                    "### **Root Cause Analysis & Precedent Alignment**\n\n"
                    "Root cause identified as downstream telecommunication SMS carrier latency. "
                    "Matches knowledge base precedent KB-4402; resending verification code clears the condition."
                )
            return (
                "<thought>Analyzing payment gateway retry events and transaction logs.</thought>"
                "### **Root Cause Analysis & Precedent Alignment**\n\n"
                f"- **Root Cause**: Payment gateway retry triggered an accidental second debit for {order_id} during automated subscription renewal.\n"
                "- **Precedent Match**: 98.4% match with historical billing precedent resolved via automated refund reversal."
            )

        if "section 1" in eval_prompt or "executive assessment" in eval_prompt:
            if is_inquiry:
                return (
                    "<thought>Evaluating inquiry details against authentication system telemetry.</thought>"
                    "### **Executive Assessment & Category Confirmation**\n\n"
                    "The customer inquiry regarding account authentication access has been reviewed. "
                    "System telemetry confirms standard technical support procedures apply with no billing ledger adjustments required."
                )
            return (
                "<thought>Auditing customer transaction ledger in data/transactions.csv.</thought>"
                "### **Executive Assessment & Category Confirmation**\n\n"
                f"- **Classified Category**: Refunds (98.5% confidence)\n"
                f"- **Assigned Department**: Billing Operations\n"
                f"- **Target SLA**: 4.0 hours (Risk: Low)\n"
                f"- **Database Analysis**: Analyzed `data/transactions.csv` and confirmed 2 settled transaction records for {cust_name} ({order_id}). Redundant duplicate debit {dup_id} ($49.00) identified alongside valid renewal {ret_id} ($49.00). Remediation authorized under SLA guidelines to restore account standing."
            )

        # Default agent response demonstrating thinking tags and resolution output
        return "<thought>Evaluating complaint details and customer impact.</thought>Your refund request has been accepted and is being processed."

    async def complete(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int = 4096,
        **options: Any,
    ) -> str:
        return self._determine_response(messages)

    async def stream(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int = 4096,
        on_delta: Optional[Callable[[str], None]] = None,
        on_reasoning_delta: Optional[Callable[[str], None]] = None,
        **options: Any,
    ) -> str:
        response = self._determine_response(messages)
        # Emit in small token chunks
        words = response.split(" ")
        for i, word in enumerate(words):
            chunk = word if i == 0 else " " + word
            if on_delta:
                on_delta(chunk)
        return response

    async def fetch_models(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        return {"ok": True, "models": ["qwen/qwen3.5-9b", "google/gemma-4-e4b", "google/gemma-4-12b-qat", "mock-gpt-4o", "mock-claude-3.5-sonnet"]}
