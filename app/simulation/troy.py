"""
Complaint Resolution Orchestrator - Troy.

Base orchestrator for the Complaint Resolution Agent (CRA) Troy.
Coordinates Troy Local LLM Resolution Engine, autonomous decision intent routing,
and LLM resolution deliberation.
"""

import asyncio
import datetime
import json
import logging
import re
import time
from typing import List, Dict, Any, Optional, Tuple

from app.utils.sanitizer import sanitize_model_output
from app.utils.json_extractor import parse_json
from app.simulation.state import SimulationState
from app.simulation.round_barrier import RoundBarrier
from app.http.sse_manager import SseManager
from app.providers.base_driver import BaseLlmDriver
from app.prompts.ticket_prompts import (
    build_ticket_title_prompt,
    build_ticket_summary_prompt,
    build_ticket_title_and_summary_prompt,
    build_title_prompt,
    build_summary_prompt,
)
from app.reference.reasoning_and_output_reference import execute_agent_with_reasoning

logger = logging.getLogger("simulation:orchestrator")


class ComplaintResolutionOrchestrator:
    """
    Orchestrator managing session state and LLM execution for the Complaint Resolution Agent Troy.
    """

    def __init__(
        self,
        config: Any,
        driver: BaseLlmDriver,
        sse_manager: SseManager,
        state: Optional[SimulationState] = None,
        barrier: Optional[RoundBarrier] = None,
        step_delay: float = 0.0,
        speech_delay: float = 0.0,
        verdict_delay: float = 0.0,
        enable_barrier: bool = False,
    ):
        self.config = config
        self.driver = driver
        self.sse = sse_manager
        self.state = state if state is not None else SimulationState()
        self.barrier = barrier if enable_barrier else None

        self.step_delay = step_delay
        self.speech_delay = speech_delay
        self.verdict_delay = verdict_delay
        self._run_driver: Optional[BaseLlmDriver] = None

    def is_running(self) -> bool:
        return self.state.is_running()

    def is_council_running(self) -> bool:
        # Compatibility alias for existing API endpoints
        return self.is_running()

    def get_latest_story(self) -> Optional[Dict[str, Any]]:
        return self.state.get_story()

    def set_story(self, story: Optional[Dict[str, Any]]):
        self.state.set_story(story)

    def mark_round_begun(self, round_num: Any) -> Dict[str, Any]:
        if self.barrier:
            return self.barrier.mark_round_begun(round_num)
        return {"ok": True, "round": round_num}

    @classmethod
    def evaluate_complaint_intent(cls, text: str, category: Optional[str] = None) -> Dict[str, Any]:
        """
        Feature 10: Autonomously evaluates complaint intent to determine whether the complaint
        requires 'direct_response' (technical, inquiry, access, policy) or
        'database_remediation' (duplicate debit, billing dispute, unauthorized charge, refund request).
        """
        raw = (text or "").strip()
        low = raw.lower()

        billing_triggers = [
            "twice", "duplicate", "double charge", "charged twice", "double debit",
            "duplicate debit", "duplicate charge", "refund", "charged again",
            "overcharged", "debited twice", "double bill", "billed twice",
            "unauthorized charge", "double-billed"
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
            "category": category or ("Refunds" if is_billing else "General Inquiry"),
            "order_id": order_id,
            "customer_id": cust_id,
            "transaction_id": txn_id,
            "raw_text": raw,
        }

    @classmethod
    def classify_intent(cls, query: str = "", category: Optional[str] = None, text: Optional[str] = None) -> Dict[str, Any]:
        """
        Differentiates general customer inquiries (requires_db_mutation = False) from
        billing disputes requiring database inspection and remediation (requires_db_mutation = True).
        """
        raw = text if text is not None else query
        return cls.evaluate_complaint_intent(raw, category=category)

    @classmethod
    def decide_intent(cls, query: str = "", category: Optional[str] = None, text: Optional[str] = None) -> Dict[str, Any]:
        """
        Alias for classify_intent.
        """
        raw = text if text is not None else query
        return cls.evaluate_complaint_intent(raw, category=category)

    # Unique marker for the strict-JSON intent decision prompt.
    INTENT_MARKER = "troy_intent_decision"

    def _active_driver(self) -> BaseLlmDriver:
        return self._run_driver or self.driver

    async def decide_intent_via_llm(
        self,
        complaint_text: str,
        category: Optional[str] = None,
        driver: Optional[BaseLlmDriver] = None,
    ) -> Dict[str, Any]:
        """
        Ask the configured LLM (active driver) for a strict-JSON intent decision.

        Prompt uses the unique ``troy_intent_decision`` marker and asks for
        ``database_investigation vs direct_response``.  Only the two exact
        decision strings are accepted; any invalid output, error, timeout,
        or missing driver defaults safely to ``direct_response`` (no DB
        investigation / no mutation) so unverifiable claims go to human review.
        """
        complaint = (complaint_text or "").strip()
        safe = {
            "decision": "direct_response",
            "rationale": "Defaulted to direct_response: no valid LLM intent decision.",
            "category": category,
        }
        d = driver or self._active_driver()
        if d is None:
            return safe
        messages = [
            {
                "role": "system",
                "content": (
                    "You are Troy's intent router. Decide whether a complaint needs "
                    "database_investigation vs direct_response. "
                    "database_investigation means the customer makes a concrete duplicate "
                    "billing claim that must be verified against transaction records. "
                    "direct_response means a policy, general, or non-duplicate inquiry that "
                    "needs no database investigation. "
                    "Respond with ONLY strict JSON, no markdown, no extra text: "
                    '{"decision": "database_investigation" | "direct_response", "rationale": "string"}. '
                    "Marker: troy_intent_decision"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"troy_intent_decision: database_investigation vs direct_response\n"
                    f"Triage category: {category or 'Unknown'}\n"
                    f"Customer complaint:\n{complaint}\n"
                    "Return only the JSON object with decision and rationale."
                ),
            },
        ]
        try:
            raw = await asyncio.wait_for(d.complete(messages, 256), timeout=10.0)
        except Exception as e:
            logger.info(f"LLM intent decision failed, defaulting to direct_response: {e}")
            return safe
        data = parse_json(raw) if raw else None
        if not isinstance(data, dict):
            return safe
        decision = str(data.get("decision") or "").strip()
        if decision not in ("database_investigation", "direct_response"):
            return safe
        return {
            "decision": decision,
            "rationale": str(data.get("rationale") or ""),
            "category": category,
        }

    # Backwards-compatible alias
    async def _decide_intent_via_llm(self, complaint_text: str, category: Optional[str] = None, driver=None):
        return await self.decide_intent_via_llm(complaint_text, category=category, driver=driver)

    async def _complete(
        self, messages: List[Dict[str, str]], max_tokens: int = 4096, driver: Optional[BaseLlmDriver] = None
    ) -> str:
        d = driver or self._active_driver()
        return await d.complete(messages, max_tokens)

    async def summarise_ticket(self, query: str, driver: Optional[BaseLlmDriver] = None) -> Optional[Dict[str, Any]]:
        """
        Sends the complaint ticket to the LLM to generate both a concise Title and an informative Summary.
        Pre-seeds with domain-intelligent extraction so the title is never truncated or malformed.
        """
        text = (query or "").strip()
        if not text:
            return None

        d = driver or self._active_driver()
        title = ""
        summary = ""

        # Pre-seed with smart domain fallback so we never emit a broken or truncated title
        smart_title = self._title_fallback(text)
        smart_summary = self._summary_fallback(text)

        if d:
            try:
                # Send ticket to LLM to generate both Title and Summary subtext
                t, s = await asyncio.wait_for(self._summarize_ticket_with_llm(text, d), timeout=10.0)
                if t and len(t.split()) >= 2:
                    title = t
                if s and len(s) > 10:
                    summary = s
            except Exception as e:
                logger.info(f"LLM ticket title/summary generation used smart fallback: {e}")

        if not title:
            title = smart_title

        if not summary:
            summary = smart_summary

        summary = re.sub(r"\bpolicy\b", "ticket", summary, flags=re.IGNORECASE)
        title = re.sub(r"\bpolicy\b", "Ticket", title, flags=re.IGNORECASE)

        self.sse.broadcast("topic", {"title": title or "Complaint Ticket"})

        story = {
            "title": title or "Complaint Ticket",
            "source": "Customer Ticket",
            "url": "",
            "publishedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "description": text,
            "summary": summary,
        }
        self.set_story(story)
        self.sse.broadcast("feed", story)
        return story

    async def _summarize_ticket_with_llm(self, text: str, driver: BaseLlmDriver) -> Tuple[str, str]:
        """
        Sends the complaint ticket to the LLM to generate both the Title and the Summary subtext.
        """
        messages, max_tokens = build_ticket_title_and_summary_prompt(text)
        try:
            raw = await driver.complete(messages, max_tokens)
            cleaned = sanitize_model_output(raw).strip()

            # Robust JSON extraction handling markdown fences, thought tags, and substrings
            data = parse_json(cleaned) or parse_json(raw)

            if isinstance(data, dict):
                title = str(data.get("title") or "").strip()
                summary = str(data.get("summary") or "").strip()
                if title or summary:
                    title = re.sub(r'^["\']|["\']$', '', title).strip()
                    title = re.sub(r'[\.:]+$', '', title).strip()
                    return title, summary

            # Line-based regex fallback
            title_match = re.search(r"(?:Title|Ticket Title)\s*:\s*([^\n]+)", cleaned, re.IGNORECASE)
            summary_match = re.search(r"(?:Summary|Issue Summary)\s*:\s*([^\n]+(?:\n[^\n]+)?)", cleaned, re.IGNORECASE)

            t = title_match.group(1).strip() if title_match else ""
            s = summary_match.group(1).strip() if summary_match else ""
            if t or s:
                t = re.sub(r'^["\']|["\']$', '', t).strip()
                t = re.sub(r'[\.:]+$', '', t).strip()
                return t, s

        except Exception as e:
            logger.warning(f"Failed to generate title and summary via LLM: {e}")

        return "", ""

    async def summarise_policy(self, query: str, driver: Optional[BaseLlmDriver] = None) -> Optional[Dict[str, Any]]:
        # Backward compatibility alias
        return await self.summarise_ticket(query, driver=driver)

    async def run(self, story: Dict[str, Any], driver: Optional[BaseLlmDriver] = None) -> bool:
        """
        Main entry point for complaint resolution.
        """
        if not self.state.acquire():
            logger.warning("Resolution run requested while another is in progress; ignoring")
            return False

        if story:
            self.set_story(story)
        if self.barrier:
            self.barrier.reset()
        self._run_driver = driver

        try:
            await self._run_pipeline(story)
            return True
        except Exception as e:
            logger.error(f"Resolution encountered an error: {e}", exc_info=True)
            self.sse.broadcast(
                "error", {"message": "Resolution encountered an error. Check settings and retry."}
            )
            return False
        finally:
            self._run_driver = None
            self.state.release()

    async def run_council(self, story: Dict[str, Any], driver: Optional[BaseLlmDriver] = None) -> bool:
        # Compatibility alias
        return await self.run(story, driver=driver)

    async def _run_pipeline(self, story: Dict[str, Any]):
        """
        Executes the CRA machine learning pipeline and Troy's resolution deliberation.
        """
        complaint_text = story.get("description") or story.get("title") or ""

        self.sse.broadcast(
            "resolution_start",
            {
                "story": {"title": story.get("title", ""), "url": story.get("url", "")},
                "agent": "Troy",
            },
        )
        self.sse.broadcast(
            "council_start",
            {
                "story": {"title": story.get("title", ""), "url": story.get("url", "")},
                "agents": [{"name": "Troy", "state": "Complaint Resolution Agent"}],
            },
        )
        self.sse.broadcast("round_start", {"round": 0, "label": "Resolution", "count": 1, "order": ["Troy"]})
        self.sse.broadcast(
            "system",
            {
                "message": f"Processing Complaint Ticket: {story.get('title', 'Ticket')}",
            },
        )
        self.sse.broadcast(
            "system",
            {
                "message": "Analyzing complaint context and preliminary assessment...",
            },
        )

        from app.triage import triage_complaint

        # Run triage asynchronously without blocking event loop
        try:
            report = await asyncio.to_thread(triage_complaint, complaint_text)
        except Exception as e:
            logger.error(f"Triage intake encountered an error: {e}", exc_info=True)
            report = {
                "complaint": complaint_text,
                "predicted_category": "Support",
                "category_confidence": 0.50,
                "top_3_categories": [{"category": "Support", "score": 0.50}],
                "extracted_entities": {"references": [], "dates": [], "amounts": [], "products": []},
                "similar_cases": [],
                "recommended_team": "Tier 1 Support",
                "team_confidence": "Medium",
                "sla": {"estimated_hours": 8.0, "sla_risk": "Low"},
            }

        pred_cat = report.get("predicted_category", "Support")
        conf_pct = report.get("category_confidence", 0.0) * 100
        team = report.get("recommended_team", "Tier 1 Support")
        sla_hours = report.get("sla", {}).get("estimated_hours", 4.0)

        # Broadcast the structured ML intelligence report
        self.sse.broadcast("cra_report", report)
        self.sse.broadcast(
            "system",
            {
                "message": f"Intake Triage: Classified as '{pred_cat}' ({conf_pct:.1f}% confidence) | Routed to: {team} | Estimated SLA: {sla_hours}h",
            },
        )

        top_cats = [f"{c['category']} ({c['score']*100:.1f}%)" for c in report.get("top_3_categories", [])]
        top_cats_str = ", ".join(top_cats)

        # Build prompt for Troy incorporating triage findings and historical precedents
        prompt_lines = [
            f"CUSTOMER COMPLAINT TICKET:\n\"{complaint_text}\"\n",
            "TROY INTELLIGENCE & TRIAGE FINDINGS:",
            f"- Resolution Engine: Troy Local LLM Engine",
            f"- Predicted Category: {pred_cat} (Confidence: {conf_pct:.1f}%)",
            f"- Top Categories: {top_cats_str}",
            f"- Extracted Entities: References: {report.get('extracted_entities', {}).get('references', [])}, Dates: {report.get('extracted_entities', {}).get('dates', [])}, Products: {report.get('extracted_entities', {}).get('products', [])}",
            f"- Recommended Department Routing: {team} (Method: {report.get('team_method', 'Rule-based')})",
            f"- Estimated SLA & Resolution Time: {sla_hours} hours (Risk: {report.get('sla', {}).get('sla_risk', 'Low')})\n",
        ]

        similar_cases = report.get("similar_cases", [])
        if similar_cases:
            prompt_lines.append("HISTORICAL PRECEDENTS & RESOLUTION NOTES (from Knowledge Base):")
            for idx, c in enumerate(similar_cases, 1):
                prompt_lines.append(
                    f"{idx}. Precedent [{c.get('similarity', 0.0)*100:.1f}% Match]: \"{c.get('description', '')}\"\n"
                    f"   Outcome / Action: {c.get('resolution', '')}"
                )
            prompt_lines.append("")

        from app.database import (
            get_category_complaint_context,
            analyze_customer_transactions,
            inspect_account_and_transactions,
            remediate_duplicate_charge,
        )

        # Step 2: Autonomous Intent Evaluation via configured LLM (Feature 10).
        # Only ID extraction (no routing) reuses the legacy helper shape.
        # NOTE: get_category_complaint_context(category="Refunds", query=...) would
        # itself call analyze_customer_transactions (DB inspection), even for a
        # direct policy inquiry. Do not call that billing context before/on
        # direct_response; use a neutral non-billing context without raw query.
        _id_info = self.evaluate_complaint_intent(complaint_text, category=pred_cat)
        intent_decision = await self.decide_intent_via_llm(complaint_text, category=pred_cat)
        decision = intent_decision.get("decision", "direct_response")
        if decision == "database_investigation":
            # Avoid redundant analyze via billing context; inspect() below is the
            # single read-only audit and cat_ctx is overridden with its result.
            cat_ctx = get_category_complaint_context(category="Account Management", query="")
        else:
            _neutral_cat = "Account Management" if pred_cat == "Refunds" else pred_cat
            cat_ctx = get_category_complaint_context(category=_neutral_cat, query="")
        cat_ctx["is_refund"] = False
        is_refund_domain = False
        human_review = False
        policy_decision: Optional[Dict[str, Any]] = None
        direct_refund_policy = decision != "database_investigation" and pred_cat == "Refunds"

        if decision == "database_investigation":
            self.sse.broadcast(
                "system",
                {"message": "Autonomous Intent Decision: LLM requested database investigation. Initiating read-only database audit..."},
            )
            txn_analysis = inspect_account_and_transactions(
                customer_id=_id_info.get("customer_id"),
                query=complaint_text,
            )
            dup_txn = txn_analysis.get("duplicate_transaction") if txn_analysis.get("ok") else None
            if not isinstance(dup_txn, dict):
                dup_txn = {}
            dup_id = dup_txn.get("transaction_id") or ""
            # Concrete evidence only: duplicate txn present, and its order/customer
            # match the audit record (and complaint IDs when present). No hardcoded fallbacks.
            dup_order = str(dup_txn.get("order_id") or "").strip().upper()
            dup_cust = str(dup_txn.get("customer_id") or "").strip().upper()
            audit_order = str(txn_analysis.get("order_id") or "").strip().upper()
            audit_cust = str(txn_analysis.get("customer_id") or "").strip().upper()
            complaint_order = _id_info.get("order_id") or ""
            complaint_cust = _id_info.get("customer_id") or ""
            order_match = bool(dup_order) and (not audit_order or dup_order == audit_order) and (not complaint_order or dup_order == complaint_order)
            cust_match = bool(dup_cust) and (not audit_cust or dup_cust == audit_cust) and (not complaint_cust or dup_cust == complaint_cust)
            # Require a real positive amount (never default to $49.00).
            try:
                _amt_val = float(str(dup_txn.get("amount") or "").strip())
            except (TypeError, ValueError):
                _amt_val = 0.0
            has_positive_amount = _amt_val > 0
            # Unrefunded: duplicate_detected must be True. Already-refunded:
            # explicit duplicate_verified on account/customer plus account notes.
            _already = bool(txn_analysis.get("is_already_refunded", False))
            _acct = txn_analysis.get("account") or {}
            _cust = txn_analysis.get("customer") or {}
            _dup_verified = str(_acct.get("duplicate_verified") or _cust.get("duplicate_verified") or "")
            _acct_notes = str(_acct.get("notes") or _cust.get("notes") or "")
            _already_verified = _already and ("yes" in _dup_verified.lower()) and bool(_acct_notes.strip())
            _unrefunded_detected = (not _already) and bool(txn_analysis.get("duplicate_detected"))
            has_concrete_evidence = bool(
                txn_analysis.get("ok") and dup_id and order_match and cust_match
                and has_positive_amount and (_unrefunded_detected or _already_verified)
            )

            if has_concrete_evidence:
                is_refund_domain = True
                is_already_refunded = _already
                dup_amt = f"{_amt_val:.2f}"
                effective_order_id = dup_order or audit_order or complaint_order
                # Tenant policy gate (admin fork: NO bypass). Trusted tenant
                # context is an explicit story field (never parsed from
                # complaint text); per-user tenant membership is NOT
                # implemented in auth/session, so this is a demo tenant
                # context validated only against a saved config. Absent /
                # missing / mismatched / invalid config fails closed to
                # escalation/manual review with NO Stripe call and NO
                # CSV/customer mutation (remediate_duplicate_charge is
                # never called from this Troy path in this fork).
                _tenant_id = story.get("tenant_id") or story.get("tenant") if isinstance(story, dict) else None
                from app.admin_config import AdminConfigStore, evaluate_policy, load_policy_for_tenant
                from pathlib import Path as _P
                _cfg_dir = getattr(self.config, "admin_config_dir", "") or ""
                _store = AdminConfigStore(_P(_cfg_dir) if _cfg_dir else None)
                _amount_cents = int(round(_amt_val * 100))
                _dry = bool(story.get("dry_run")) if isinstance(story, dict) else False
                if _tenant_id:
                    _cfg, _cfg_errs = load_policy_for_tenant(_store, _tenant_id)
                    policy_decision = evaluate_policy(
                        _cfg, tenant_id=_tenant_id, action="duplicate_refund",
                        amount_cents=_amount_cents, dry_run=_dry,
                    )
                    if _cfg_errs and _cfg is None:
                        policy_decision = evaluate_policy(
                            None, tenant_id=_tenant_id, action="duplicate_refund",
                            amount_cents=_amount_cents, dry_run=_dry,
                        )
                        policy_decision["rationale"] = f"missing/mismatched tenant config: {_cfg_errs[0]}"
                else:
                    policy_decision = evaluate_policy(
                        None, tenant_id=None, action="duplicate_refund",
                        amount_cents=_amount_cents, dry_run=_dry,
                    )
                    policy_decision["rationale"] = "missing trusted tenant context: no tenant_id in story (complaint text is not trusted); fail closed to escalation/manual review"
                cat_ctx["policy_decision"] = policy_decision
                cat_ctx["txn_analysis"] = txn_analysis
                self.sse.broadcast(
                    "system",
                    {"message": f"Tenant Policy Decision ({policy_decision.get('decision')}): {policy_decision.get('rationale')} No Stripe/live execution; no customer-data mutation."},
                )
                is_refund_domain = False
                human_review = True
                cat_ctx["is_refund"] = False
                cat_ctx["human_review"] = True
                cat_ctx["policy_simulated"] = True
                prompt_lines.append("TENANT POLICY DECISION (simulated, enforced before any action):")
                prompt_lines.append(f"- Decision: {policy_decision.get('decision')}; rationale: {policy_decision.get('rationale')}")
                prompt_lines.append(f"- Escalation target: {policy_decision.get('escalation_target') or policy_decision.get('escalated_to') or 'billing-queue'}; dry_run={policy_decision.get('dry_run')}; external_contact=False; mutated_customer_data=False")
                prompt_lines.append(
                    "Please deliver your assessment as Troy. State the tenant policy decision and rationale, "
                    "that no Stripe/live refund was executed and no customer data was mutated, and route to the escalation target if denied/requiring approval. "
                    "Do not claim a refund was issued or cite a refund ID.\n"
                )
                if is_refund_domain:
                    cat_ctx["txn_analysis"] = txn_analysis
                    cat_ctx["is_refund"] = True
                    cat_ctx["reference_id"] = ref_id
                    c_name = txn_analysis.get("customer_name") or dup_txn.get("customer_name") or ""
                    c_id = txn_analysis.get("customer_id") or dup_cust
                    c_email = txn_analysis.get("customer_email") or dup_txn.get("customer_email") or ""
                    plan = txn_analysis.get("subscription_plan") or dup_txn.get("subscription_plan") or ""
                    cycle = txn_analysis.get("billing_cycle") or dup_txn.get("billing_cycle") or ""
                    txns = txn_analysis.get("transactions", [])
            else:
                # Insufficient / mismatched evidence: no write, human review.
                human_review = True
                cat_ctx["txn_analysis"] = txn_analysis
                cat_ctx["is_refund"] = False
                cat_ctx["human_review"] = True
                cat_ctx["insufficient_duplicate"] = True
                self.sse.broadcast(
                    "system",
                    {"message": "Autonomous Intent Decision: Database investigation requested but insufficient duplicate evidence. No database mutation; Resolving without database mutation and routing to human review."},
                )
                prompt_lines.append("DATABASE TRANSACTION RECORDS AUDIT (read-only, from data/transactions.csv):")
                prompt_lines.append(f"- Audit result: ok={txn_analysis.get('ok')}; duplicate_transaction={dup_id or 'none'}; insufficient duplicate evidence found.")
                prompt_lines.append("- Decision: insufficient duplicate evidence - zero database mutation; human review required for unverifiable billing claim.\n")
                prompt_lines.append(
                    "Please deliver your formal Complaint Resolution Assessment as Troy. "
                    "State that a read-only audit found insufficient duplicate evidence, that no database mutation was performed (zero database mutation), "
                    "and that the ticket is routed to human review for manual verification. Do not claim a refund was issued.\n"
                )
                # Skip refund prompt construction below by falling through to direct-style context
                is_refund_domain = False
                # Build a direct-style human-review prompt section
                prompt_lines.append(f"INTERNAL SYSTEM & TELEMETRY AUDIT ({cat_ctx.get('system_label', 'Master Records')}):")
                prompt_lines.append(f"- Account ID: {cat_ctx.get('customer_id')} | Customer: {cat_ctx.get('customer_name')} ({cat_ctx.get('customer_email')})")
                prompt_lines.append(f"- Human review: insufficient duplicate evidence; escalate to Billing specialist.\n")
                prompt_lines.append(
                    f"DOMAIN RESOLUTION INSTRUCTIONS:\n"
                    f"You are Troy, Lead Complaint Resolution Agent. This is a '{pred_cat}' ticket routed to {team}.\n"
                    f"human review: insufficient duplicate evidence, zero database mutation. Explain the read-only audit finding, that no refund was issued, "
                    f"and that a Billing specialist will manually verify. Provide clear next steps."
                )

            if is_refund_domain:
                prompt_lines.append("DATABASE TRANSACTION RECORDS AUDIT (from data/transactions.csv):")
                prompt_lines.append(f"- Customer: {c_name} (ID: {c_id} | Email: {c_email})")
                prompt_lines.append(f"- Subscription Plan: {plan} | Billing Cycle: {cycle}")
                prompt_lines.append(f"- Transaction Records in {cycle} ({len(txns)} found):")
                for t in txns:
                    prompt_lines.append(f"  * {t.get('transaction_id')}: ${t.get('amount')} {t.get('currency', 'USD')} on {t.get('transaction_date')} [Status: {t.get('status')}] - {t.get('payment_method')}")
                if is_already_refunded:
                    prompt_lines.append(f"- Autonomous Audit Finding: Accidental duplicate debit {dup_id} (${dup_amt}) was ALREADY REFUNDED on {ref_date} under Refund ID {ref_id}.")
                    prompt_lines.append(f"- Ledger Status: Customer has 1 settled active renewal record and 1 refunded duplicate record. Current ledger balance is $0.00 in good standing.")
                    prompt_lines.append(f"- Decision: Duplicate refund prevented. Reconfirm existing refund status (ID: {ref_id}) and advise customer of payment processing timeline.\n")
                    prompt_lines.append(
                        "Please deliver your formal Complaint Resolution Assessment as Troy, Lead Complaint Resolution Agent. "
                        f"Explicitly cite your analysis of data/transactions.csv: confirm that duplicate transaction {dup_id} was ALREADY REFUNDED under Refund ID {ref_id}, that the account balance is settled at $0.00, and that no duplicate charge or payout is needed. "
                    )
                else:
                    prompt_lines.append(f"- Autonomous Audit Finding: Customer has {len(txns)} transaction records in a single billing cycle for a monthly subscription.")
                    prompt_lines.append(f"- Decision: Authorize immediate refund specifically for duplicate transaction {dup_id} (${dup_amt}), updating its status to 'Refunded' in data/transactions.csv.\n")
                    prompt_lines.append(
                        "Please deliver your formal Complaint Resolution Assessment as Troy, Lead Complaint Resolution Agent. "
                        f"Explicitly cite your analysis of the transaction records in data/transactions.csv and your decision to refund the redundant transaction ({dup_id}). "
                    )
        else:
            self.sse.broadcast(
                "system",
                {"message": f"Autonomous Intent Decision: Direct inquiry for '{pred_cat}'. Resolving without database mutation."},
            )
            cat_ctx["is_refund"] = False

            if direct_refund_policy:
                # Dedicated direct informational path for Refunds: may explain
                # general refund guidance, must not claim account inspection,
                # refund issuance, or known account status.
                prompt_lines.append("DIRECT REFUND POLICY INFORMATION (general guidance only, no account inspection):")
                prompt_lines.append("- No account was inspected, no refund was issued, and account status is not known in this direct response.")
                prompt_lines.append("- You may explain general refund guidance in general terms without asserting exact timelines or account-specific outcomes.")
                prompt_lines.append("- Ask the customer to provide an order ID for account-specific help so a specialist can review the specific charge.\n")
                prompt_lines.append(
                    f"DOMAIN RESOLUTION INSTRUCTIONS:\n"
                    f"You are Troy, Lead Complaint Resolution Agent. This is a general refund policy inquiry routed to {team}.\n"
                    f"Provide general refund policy guidance only. Do NOT claim an account was inspected, a refund was issued, or that account status is known. "
                    f"Do NOT mention database transactions, payment ledgers, CSV files, or transactions.csv. "
                    f"Ask for an order ID for account-specific help and provide clear next steps."
                )
            else:
                # Domain-specific prompt for the other 10 categories
                prompt_lines.append(f"INTERNAL SYSTEM & TELEMETRY AUDIT ({cat_ctx.get('system_label', 'Master Records')}):")
                prompt_lines.append(f"- Account ID: {cat_ctx.get('customer_id')} | Customer: {cat_ctx.get('customer_name')} ({cat_ctx.get('customer_email')})")
                prompt_lines.append(f"- Subscription Tier: {cat_ctx.get('subscription_plan')}")
                prompt_lines.append(f"- Current Diagnostic Status: {cat_ctx.get('status_pill')}")
                prompt_lines.append(f"- Domain Findings: {cat_ctx.get('system_details')}")
                prompt_lines.append(f"- Recommended Autonomous Action: {cat_ctx.get('action_btn_text')} (Reference: {cat_ctx.get('reference_id')})\n")

                prompt_lines.append(
                    f"DOMAIN RESOLUTION INSTRUCTIONS:\n"
                    f"You are Troy, Lead Complaint Resolution Agent. This is a '{pred_cat}' complaint ticket routed to {team}.\n"
                    f"CRITICAL CONSTRAINT: This ticket is strictly about '{pred_cat}'. Do NOT mention database transactions, bank charges, refunds, payment ledgers, CSV files, or transactions.csv.\n"
                    f"Address the technical and operational root cause of the {pred_cat} issue directly. "
                    f"Explain the concrete actions taking place under reference ID {cat_ctx.get('reference_id')}, and reassure the customer with clear next steps."
                )

        base_context_str = "\n".join(prompt_lines)

        if is_refund_domain:
            system_instruction = (
                "You are Troy, Lead Autonomous Complaint Resolution Agent (CRA) specializing in billing disputes and refund resolutions. "
                "You analyze customer transaction records in data/transactions.csv to reconcile accounts, prevent duplicate debits, and execute or reconfirm refunds. "
                "Provide a structured, authoritative, empathetic, and definitive resolution. Never mention 'policy simulation' or 'council'."
            )
        elif human_review:
            system_instruction = (
                "You are Troy, Lead Autonomous Complaint Resolution Agent (CRA). "
                "A read-only transaction audit found insufficient duplicate evidence. "
                "Explain the audit finding, that no database mutation was performed, and that the ticket is routed to human review. "
                "Do not claim a refund was issued. Never mention 'policy simulation' or 'council'."
            )
        elif direct_refund_policy:
            system_instruction = (
                "You are Troy, Lead Autonomous Complaint Resolution Agent (CRA) providing direct refund policy information. "
                "You may explain general refund guidance in general terms. "
                "You must not claim an account was inspected, a refund was issued, or that account status is known. "
                "Do not mention database transactions, payment ledgers, CSV files, or transactions.csv. "
                "Ask the customer to provide an order ID for account-specific help. "
                "Never mention 'policy simulation' or 'council'."
            )
        else:
            system_instruction = (
                f"You are Troy, Lead Autonomous Complaint Resolution Agent (CRA) specializing in {pred_cat}. "
                f"You diagnose technical issues using ML model findings, system telemetry, and historical precedents. "
                f"CRITICAL CONSTRAINT: Do NOT mention transactions, refunds, charges, payments, CSV files, or transactions.csv for this ticket. "
                f"Provide a structured, authoritative, empathetic, and definitive resolution tailored strictly to this {pred_cat} complaint. "
                "Never mention 'policy simulation' or 'council'."
            )

        troy_agent = {"name": "Troy", "state": f"Assigned: {team}"}

        # Multi-prompt architecture: 1 dedicated prompt per resolution section = 1 chatbox at a time
        section_configs = [
            {
                "id": "executive",
                "index": 0,
                "title": "Executive Assessment & Category Confirmation",
                "card_id": "troy-card",
                "speech_id": "troy-speech",
                "label": "Executive Assessment",
                "prompt": (
                    "TASK: Deliver ONLY Section 1: Executive Assessment & Category Confirmation.\n"
                    "FORMAT REQUIREMENT: Start directly with the bold heading '### **Executive Assessment & Category Confirmation**'.\n"
                    "CONTENT REQUIREMENT: Formally confirm the complaint category, assigned department, SLA timeline, and audit findings from database records or system telemetry. State definitively whether the customer's assertion is valid or already reconciled.\n"
                    "CRITICAL CONSTRAINT: Do NOT include Root Cause, Customer Communication, or Internal Routing. Stop after completing this section."
                ),
            },
            {
                "id": "root_cause",
                "index": 1,
                "title": "Root Cause Analysis & Precedent Alignment",
                "card_id": "troy-card-root-cause",
                "speech_id": "troy-root-cause-speech",
                "label": "Root Cause Analysis",
                "prompt": (
                    "TASK: Deliver ONLY Section 2: Root Cause Analysis & Precedent Alignment.\n"
                    "FORMAT REQUIREMENT: Start directly with the bold heading '### **Root Cause Analysis & Precedent Alignment**'.\n"
                    "CONTENT REQUIREMENT: Explain the root cause of the customer issue and explicitly cite alignment with the historical precedents retrieved from the knowledge base.\n"
                    "CRITICAL CONSTRAINT: Do NOT repeat Section 1. Do NOT include Customer Communication or Internal Routing. Stop after completing this section."
                ),
            },
            {
                "id": "customer_comms",
                "index": 2,
                "title": "Customer Communication Strategy",
                "card_id": "troy-card-communication",
                "speech_id": "troy-communication-speech",
                "label": "Customer Communication",
                "prompt": (
                    "TASK: Deliver ONLY Section 3: Customer Communication Strategy.\n"
                    "FORMAT REQUIREMENT: Start directly with the bold heading '### **Customer Communication Strategy**'.\n"
                    "CONTENT REQUIREMENT: Write the exact empathetic message to be sent to the customer. Include '**Subject:** [Subject line]', personalized greeting with customer name, clear reassuring explanation of actions taken or already resolved, and professional sign-off from Troy.\n"
                    "CRITICAL CONSTRAINT: Do NOT repeat previous sections. Do NOT output internal routing. Stop after completing this section."
                ),
            },
            {
                "id": "internal_routing",
                "index": 3,
                "title": "Internal Routing & Next Action Steps",
                "card_id": "troy-card-routing",
                "speech_id": "troy-routing-speech",
                "label": "Internal Routing",
                "prompt": (
                    "TASK: Deliver ONLY Section 4: Internal Routing & Next Action Steps.\n"
                    "FORMAT REQUIREMENT: Start directly with the bold heading '### **Internal Routing & Next Action Steps**'.\n"
                    "CONTENT REQUIREMENT: State the internal department routing, database ledger updates or system actions executed, internal ticket status update, and concrete next operational steps.\n"
                    "CRITICAL CONSTRAINT: Do NOT repeat previous sections. Stop after completing this section."
                ),
            },
        ]

        # Prepare full category fallback in case LLM is offline or times out
        fallback_full = self._build_category_fallback(
            pred_cat=pred_cat,
            complaint_text=complaint_text,
            conf_pct=conf_pct,
            team=team,
            sla_hours=sla_hours,
            report=report,
            cat_ctx=cat_ctx,
        )
        fallback_sections = self._split_fallback_into_sections(fallback_full)

        conversation_history: List[Dict[str, str]] = [
            {"role": "system", "content": system_instruction},
        ]
        section_results = {}

        for sec in section_configs:
            sec_id = sec["id"]
            sec_idx = sec["index"]
            sec_title = sec["title"]
            card_id = sec["card_id"]
            speech_id = sec["speech_id"]
            label = sec["label"]

            self.sse.broadcast(
                "system",
                {"message": f"Troy generating Prompt {sec_idx + 1}/4: {sec_title}..."}
            )

            # Build user prompt with base telemetry on first step
            if sec_idx == 0:
                user_msg = f"{base_context_str}\n\n{sec['prompt']}"
            else:
                user_msg = sec["prompt"]

            step_messages = list(conversation_history)
            step_messages.append({"role": "user", "content": user_msg})

            extra_payload = {
                "section": sec_id,
                "section_index": sec_idx,
                "section_title": sec_title,
                "card_id": card_id,
                "speech_id": speech_id,
            }

            sec_output = await self._agent_speak(
                agent=troy_agent,
                messages=step_messages,
                max_tokens=1024,
                round_num=sec_idx,
                label=label,
                extra_payload=extra_payload,
            )

            if not sec_output or "Signal lost" in sec_output:
                sec_output = fallback_sections.get(sec_id) or fallback_full
                self.sse.broadcast(
                    "agent_start",
                    {"agent": "Troy", "round": sec_idx, "state": f"Assigned: {team}", **extra_payload}
                )
                self.sse.broadcast(
                    "agent",
                    {
                        "agent": "Troy",
                        "round": sec_idx,
                        "state": f"Assigned: {team}",
                        "message": sec_output,
                        **extra_payload,
                    }
                )

            # Ensure bold heading is present at the very top of each section
            clean_bold_heading = f"### **{sec_title}**"
            if not re.search(r"###\s*\*\*", sec_output):
                if re.search(rf"^(?:[#*•\->]|\d+[\.:\)])*[ \t]*{re.escape(sec_title)}", sec_output, re.IGNORECASE | re.MULTILINE):
                    sec_output = re.sub(
                        rf"^(?:[#*•\->]|\d+[\.:\)])*[ \t]*{re.escape(sec_title)}[^\n]*",
                        clean_bold_heading,
                        sec_output,
                        count=1,
                        flags=re.IGNORECASE | re.MULTILINE,
                    )
                else:
                    sec_output = f"{clean_bold_heading}\n\n{sec_output.strip()}"

            section_results[sec_id] = sec_output
            conversation_history.append({"role": "user", "content": user_msg})
            conversation_history.append({"role": "assistant", "content": sec_output})

        full_resolution_text = "\n\n".join(section_results.values())
        resolution_summary = f"Complaint ticket categorized as '{pred_cat}' ({conf_pct:.1f}% confidence) and routed to {team}."
        self.sse.broadcast(
            "resolution_end",
            {
                "summary": resolution_summary,
                "category": pred_cat,
                "confidence": conf_pct,
                "team": team,
                "sla_hours": sla_hours,
                "cra_report": report,
                "full_resolution": full_resolution_text,
                "sections": section_results,
                "policy_decision": policy_decision,
            },
        )
        self.sse.broadcast(
            "council_end",
            {
                "round": 0,
                "summary": resolution_summary,
                "impacts": {
                    "positive": [
                        f"Category: {pred_cat}",
                        f"Team: {team}",
                        f"SLA: {sla_hours}h",
                    ],
                    "negative": [],
                },
                "cra_report": report,
                "full_resolution": full_resolution_text,
                "sections": section_results,
                "policy_decision": policy_decision,
            },
        )

    async def _agent_speak(
        self,
        agent: Dict[str, Any],
        messages: List[Dict[str, str]],
        max_tokens: int,
        round_num: int = 0,
        label: str = "Resolution",
        extra_payload: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Executes an agent call using the reference reasoning and output streaming handler.
        """
        use_stream = getattr(self.config, "llm_stream", True) is not False
        # For LM Studio / local models: reasoning is shown only if explicitly enabled
        show_reasoning = getattr(self.config, "show_reasoning", False) is True

        result = await execute_agent_with_reasoning(
            driver=self._active_driver(),
            sse_manager=self.sse,
            agent_name=agent.get("name", "Troy"),
            role_or_state=agent.get("state", "Complaint Resolution Agent"),
            messages=messages,
            max_tokens=max_tokens,
            round_num=round_num,
            label=label,
            show_reasoning=show_reasoning,
            use_stream=use_stream,
            extra_payload=extra_payload,
        )
        return result.get("message", "")

    def _split_fallback_into_sections(self, text: str) -> Dict[str, str]:
        """
        Splits a full fallback resolution into the 4 dedicated sections,
        ensuring each section starts with a bold markdown heading.
        """
        if not text:
            return {}

        patterns = [
            ("executive", r"###\s*Executive Assessment[^\n]*", "### **Executive Assessment & Category Confirmation**"),
            ("root_cause", r"###\s*Root Cause[^\n]*", "### **Root Cause Analysis & Precedent Alignment**"),
            ("customer_comms", r"###\s*Customer Communication[^\n]*", "### **Customer Communication Strategy**"),
            ("internal_routing", r"###\s*Internal Routing[^\n]*", "### **Internal Routing & Next Action Steps**"),
        ]

        matches = []
        for sec_id, pattern, bold_heading in patterns:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                matches.append((sec_id, m.start(), m.end(), bold_heading))

        matches.sort(key=lambda x: x[1])
        sections = {}

        for i, (sec_id, start, end, bold_heading) in enumerate(matches):
            next_start = matches[i + 1][1] if i + 1 < len(matches) else len(text)
            body = text[end:next_start].strip()
            sections[sec_id] = f"{bold_heading}\n{body}"

        return sections

    async def _summarize_title(self, text: str, driver: BaseLlmDriver) -> str:
        messages, max_tokens = build_ticket_title_prompt(text)
        try:
            res = await driver.complete(messages, max_tokens)
            cleaned = sanitize_model_output(res)
            words = [w for w in re.sub(r"[^\w\s]", " ", cleaned).strip().split() if w]
            if words:
                candidate = " ".join(words[:4])
                if not re.match(r"^(ticket|policy|complaint)$", candidate, re.IGNORECASE):
                    return candidate
        except Exception as e:
            logger.error(f"Title extraction failed: {e}")
        return self._title_fallback(text)

    def _title_fallback(self, text: str) -> str:
        raw = (text or "").strip()
        if not raw:
            return "Customer Complaint Ticket"

        low = raw.lower()

        # 1. High-priority domain and keyword intent matching
        if ("crash" in low or "500" in low or "exception" in low) and ("pdf" in low or "export" in low):
            if "android" in low:
                return "Android PDF Export Crash"
            return "App Crash on PDF Export"
        elif "crash" in low or "crashed" in low or "crashes" in low:
            if "android" in low:
                return "Android Mobile App Crash"
            elif "ios" in low or "iphone" in low:
                return "iOS Mobile App Crash"
            elif "camera" in low:
                return "Mobile Camera Crash Error"
            return "Application Crash Error"

        if "2fa" in low or "two-factor" in low or "mfa" in low or ("auth" in low and "code" in low):
            return "2FA Authentication Code Delivery Issue"

        if "password reset" in low or "reset password" in low:
            return "Password Reset Assistance"

        if "vat" in low or "tax id" in low or "tax invoice" in low:
            return "Company VAT & Tax Invoice Request"

        if "sync" in low or "syncing" in low:
            return "Cross-Device Data Sync Failure"

        if "429" in low or "rate limit" in low:
            return "Webhook API 429 Rate Limit Error"
        elif "api" in low and ("webhook" in low or "endpoint" in low or "documentation" in low):
            return "API Webhook Configuration Issue"

        if ("invite" in low or "team" in low) and ("permission" in low or "role" in low):
            return "Workspace Team Invite & Permissions"

        if "latency" in low or "seconds to load" in low or "45 seconds" in low or "slow" in low or "timeout" in low:
            return "Dashboard Search Query Latency"

        if "suspicious" in low or "unauthorized" in low or "unknown ip" in low:
            return "Suspicious Login Attempt Detected"

        if ("renewal" in low or "downgrade" in low) and ("charge" in low or "charged" in low or "unexpected" in low):
            return "Unexpected Plan Renewal Charge"

        if "merge" in low and "account" in low:
            return "Workspace Account Merge & Name Update"

        if "charged twice" in low or "duplicate" in low:
            return "Duplicate Subscription Charge"

        if "refund" in low:
            return "Customer Refund Request"

        if "payment" in low or "checkout" in low or "gateway" in low:
            if "404" in low or "error" in low or "fail" in low:
                return "Payment Gateway Checkout Error"
            return "Payment & Checkout Inquiry"

        # 2. Grammar-aware clause & phrase extractor (avoids cutting off mid-phrase)
        cleaned = re.sub(
            r"^(?:hello|hi|hey|please|can you|could you|i need to|i want to|i am writing to|i have an issue where|we have an issue where|customer reported that|there is an issue with|issue with|problem with|bug with|how do i|how to|can i|getting an?|there is an?)\s+",
            "",
            raw,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r'[#*_>`~\[\]\(\)\{\}\"\'\-]', " ", cleaned)

        first_clause = re.split(r'[\.\?!;\n]|,\s*(?:but|and|or|because|since|however)\s*', cleaned)[0].strip()
        words = [w for w in first_clause.split() if w]

        dangling = {
            "every", "the", "a", "an", "and", "or", "of", "to", "in", "for", "on", "with", "by",
            "as", "is", "are", "be", "at", "from", "into", "my", "our", "your", "their", "when",
            "that", "which", "if", "where", "how", "what", "who", "while", "after", "before", "all"
        }

        # Trim dangling prepositions/determiners from the end
        while words and words[-1].lower() in dangling:
            words.pop()

        if len(words) > 5:
            words = words[:5]
            while words and words[-1].lower() in dangling:
                words.pop()

        if words:
            return " ".join(w.capitalize() for w in words)

        return "Customer Complaint Ticket"

    def _summary_fallback(self, text: str) -> str:
        raw = (text or "").strip()
        if not raw:
            return "Customer complaint ticket submitted for autonomous resolution."

        # Preserve the customer's actual complaint details
        clean = re.sub(r"^(?:hello|hi|hey)\s*[\.,!]?\s*", "", raw, flags=re.IGNORECASE).strip()
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", clean) if s.strip()]

        if len(sentences) >= 2:
            combined = f"{sentences[0]} {sentences[1]}"
            if len(combined) > 220:
                combined = combined[:217] + "..."
            return f"Customer reported: {combined}"
        elif sentences:
            single = sentences[0]
            if len(single) > 220:
                single = single[:217] + "..."
            return f"Customer reported: {single}"

        return f"Customer complaint ticket regarding {raw[:140]}..."

    async def _summarize_text(self, text: str, driver: BaseLlmDriver) -> str:
        messages, max_tokens = build_ticket_summary_prompt(text)
        try:
            res = await driver.complete(messages, max_tokens)
            cleaned = sanitize_model_output(res).strip()
            # Remove any accidental "The policy is about..." or "The ticket is about..."
            cleaned = re.sub(
                r"^(?:the\s+policy\s+is\s+about|this\s+policy\s+is|the\s+ticket\s+is\s+about|customer\s+complaint\s+ticket\s*:)\s*",
                "",
                cleaned,
                flags=re.IGNORECASE,
            ).strip()
            cleaned = re.sub(r"\bpolicy\b", "ticket", cleaned, flags=re.IGNORECASE)
            if cleaned:
                sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", cleaned) if s.strip()]
                if len(sentences) >= 2:
                    return f"{sentences[0]} {sentences[1]}"
                if sentences:
                    return sentences[0]
        except Exception as e:
            logger.warning(f"Summary text failed: {e}")

        cleaned_text = (text or "").strip()
        return f"Customer ticket regarding {cleaned_text[:140]}."

    def _build_category_fallback(
        self,
        pred_cat: str,
        complaint_text: str,
        conf_pct: float,
        team: str,
        sla_hours: float,
        report: Dict[str, Any],
        cat_ctx: Dict[str, Any],
    ) -> str:
        """
        Generates structured, authoritative resolution fallbacks tailored to each of the 11 CRA categories.
        Used when LLM provider is offline or stream is completed.
        """
        ref_id = cat_ctx.get("reference_id") or ""

        if isinstance(cat_ctx.get("policy_decision"), dict):
            _pd = cat_ctx["policy_decision"]
            _esc = _pd.get("escalation_target") or _pd.get("escalated_to") or team
            return (
                f"### Executive Assessment & Category Confirmation\n"
                f"- **Classified Category**: {pred_cat} ({conf_pct:.1f}% confidence)\n"
                f"- **Assigned Department**: {team} ({report.get('team_method', 'Rule-based')})\n"
                f"- **Target SLA**: {sla_hours} hours (Risk: {report.get('sla', {}).get('sla_risk', 'Low')})\n"
                f"- **Tenant Policy Decision**: {_pd.get('decision')} — {_pd.get('rationale')} No Stripe/live execution was performed and no customer data was mutated.\n\n"
                f"### Root Cause Analysis & Precedent Alignment\n"
                f"- **Root Cause**: Tenant resolution policy evaluated before any action.\n"
                f"- **Precedent Match**: Routed per configured escalation target.\n\n"
                f"### Customer Communication Strategy\n"
                f"\"Dear Valued Customer, thank you for reaching out. Your case was evaluated under the configured tenant policy (decision: {_pd.get('decision')}). No refund has been issued in this response. {'Please provide additional information so we can review it.' if _pd.get('decision') in ('require_approval', 'deny', 'dry_run') else 'This would be eligible under simulated policy only; live execution is not enabled.'} Your ticket has been routed to {_esc} for next steps.\"\n\n"
                f"### Internal Routing & Next Action Steps\n"
                f"- **Internal Routing**: Routed to {_esc}.\n"
                f"- **Database Sync**: No mutation; simulated tenant policy decision only."
            )

        if cat_ctx.get("human_review") or cat_ctx.get("insufficient_duplicate") or cat_ctx.get("remediation_failed"):
            _failed = bool(cat_ctx.get("remediation_failed"))
            _analysis = (
                "Automated remediation failed after read-only audit of `data/transactions.csv`. "
                "No refund was issued, no refund reference was assigned, and no database mutation was confirmed."
                if _failed else
                "Read-only audit of `data/transactions.csv` found insufficient duplicate evidence. "
                "No eligible duplicate debit was identified; no refund was issued and zero database mutation was performed."
            )
            _root = (
                "Automated remediation tool returned failure; manual billing review is required."
                if _failed else
                "Transaction ledger verification showed no unrefunded duplicate debit matching the customer inquiry."
            )
            return (
                f"### Executive Assessment & Category Confirmation\n"
                f"- **Classified Category**: {pred_cat} ({conf_pct:.1f}% confidence)\n"
                f"- **Assigned Department**: {team} ({report.get('team_method', 'Rule-based')})\n"
                f"- **Target SLA**: {sla_hours} hours (Risk: {report.get('sla', {}).get('sla_risk', 'Low')})\n"
                f"- **Database Analysis**: {_analysis}\n\n"
                f"### Root Cause Analysis & Precedent Alignment\n"
                f"- **Root Cause**: {_root}\n"
                f"- **Precedent Match**: Ticket escalated to human specialist under Billing Audit precedent.\n\n"
                f"### Customer Communication Strategy\n"
                f"\"Dear Valued Customer, thank you for reaching out. We performed a read-only audit of our transaction records. {'Our automated remediation attempt failed, so no refund has been issued at this time.' if _failed else 'We did not find an eligible duplicate debit on file.'} To ensure complete accuracy, we have escalated your ticket to our Billing Domain Specialist team for manual verification (human review). No charge has been changed at this time.\"\n\n"
                f"### Internal Routing & Next Action Steps\n"
                f"- **Internal Routing**: Routed to {team} for human review.\n"
                f"- **Database Sync**: {'No refund issued; remediation failed and escalated for manual verification.' if _failed else 'Zero database mutation; escalated for manual verification.'}"
            )

        if (("policy" in (complaint_text or "").lower() or "refund policy" in (complaint_text or "").lower() or pred_cat == "Refunds") and not cat_ctx.get("is_refund", False) and not cat_ctx.get("human_review") and not cat_ctx.get("remediation_failed")):
            return (
                f"### Executive Assessment & Category Confirmation\n"
                f"- **Classified Category**: {pred_cat} ({conf_pct:.1f}% confidence)\n"
                f"- **Assigned Department**: {team} ({report.get('team_method', 'Rule-based')})\n"
                f"- **Target SLA**: {sla_hours} hours (Risk: {report.get('sla', {}).get('sla_risk', 'Low')})\n"
                f"- **Policy Review**: General refund policy inquiry. General informational guidance only; no account was inspected, no refund was issued, and account status is not known. Zero database ledger adjustments were made.\n\n"
                f"### Root Cause Analysis & Precedent Alignment\n"
                f"- **Root Cause**: Customer requested clarification regarding general refund guidance and terms.\n"
                f"- **Precedent Match**: Direct informational response provided without account-specific review.\n\n"
                f"### Customer Communication Strategy\n"
                f"\"Dear Valued Customer, thank you for contacting support regarding refund policies. Generally, duplicate charges may be eligible for refund after verification, and plan changes may be eligible for prorated credit where applicable; exact eligibility and timing depend on the specific case. We have not inspected your account or issued a refund in this response. If you need help with a specific charge, please provide your order ID so we can review it.\"\n\n"
                f"### Internal Routing & Next Action Steps\n"
                f"- **Internal Routing**: Routed to {team}.\n"
                f"- **Database Sync**: Zero database mutation required for policy inquiries; ask for order ID for account-specific help."
            )

        # Gate solely on verified is_refund (set only after concrete DB evidence
        # in troy pipeline, or explicit verified context). Never on pred_cat alone,
        # so a direct_response for a Refunds-classified ticket cannot emit a
        # fabricated refund via fallback.
        if cat_ctx.get("is_refund", False):
            txn_analysis = cat_ctx.get("txn_analysis") or {}
            is_already_ref = txn_analysis.get("is_already_refunded", False)
            dup_txn = txn_analysis.get("duplicate_transaction") or {}
            ret_txn = txn_analysis.get("retained_transaction") or {}
            dup_id = dup_txn.get("transaction_id") or ""
            ret_id = ret_txn.get("transaction_id") or ""
            dup_amt = dup_txn.get("amount") or ""
            ret_amt = ret_txn.get("amount") or ""
            ref_date = txn_analysis.get("refunded_at") or dup_txn.get("refunded_at") or ""

            c_name = txn_analysis.get("customer_name") or ""
            c_first = c_name.split()[0] if c_name else "Valued Customer"
            c_cycle = txn_analysis.get("billing_cycle") or ""
            order_ref = txn_analysis.get("order_id") or (txn_analysis.get("customer") or {}).get("reference_order_id") or ""
            order_num = order_ref.replace("ORD-", "")
            active_ref = ref_id or txn_analysis.get("refund_id") or ""

            if is_already_ref:
                return (
                    f"### Executive Assessment & Category Confirmation\n"
                    f"- **Classified Category**: Refunds ({conf_pct:.1f}% confidence)\n"
                    f"- **Assigned Department**: {team} ({report.get('team_method', 'Rule-based')})\n"
                    f"- **Target SLA**: {sla_hours} hours (Risk: {report.get('sla', {}).get('sla_risk', 'Low')})\n"
                    f"- **Database Analysis**: Analyzed `data/transactions.csv` and confirmed customer account is already reconciled. The duplicate charge ({dup_id}: ${dup_amt}) was **ALREADY REFUNDED** on {ref_date} under Refund ID **{active_ref}**. The single active renewal ({ret_id}: ${ret_amt}) remains valid, and customer balance is **$0.00 (Settled)**.\n\n"
                    f"### Root Cause Analysis & Precedent Alignment\n"
                    f"- **Root Cause**: Payment gateway retry created duplicate authorization which was caught and refunded during automated settlement.\n"
                    f"- **Precedent Match**: 99.1% match with historical re-inquiry cases where customer needed reconfirmation of an already-processed refund receipt.\n\n"
                    f"### Customer Communication Strategy\n"
                    f"\"Hello {c_first}, thank you for checking in. We verified our transaction records in the ledger and confirm that the duplicate charge of ${dup_amt} ({dup_id}) was already refunded to your original payment method under Refund ID {active_ref}. Depending on your bank's posting cycle, this typically reflects within 3-5 business days. Your subscription remains active with zero overdue balance.\"\n\n"
                    f"### Internal Routing & Next Action Steps\n"
                    f"- **Internal Routing**: Routed to {team}.\n"
                    f"- **Duplicate Prevention**: Verified existing refund in `data/transactions.csv`. No secondary refund payout required. Closed ticket as resolved and dispatched receipt reconfirmation."
                )
            else:
                return (
                    f"### Executive Assessment & Category Confirmation\n"
                    f"- **Classified Category**: Refunds ({conf_pct:.1f}% confidence)\n"
                    f"- **Assigned Department**: {team} ({report.get('team_method', 'Rule-based')})\n"
                    f"- **Target SLA**: {sla_hours} hours (Risk: {report.get('sla', {}).get('sla_risk', 'Low')})\n"
                    f"- **Database Analysis**: Analyzed `data/transactions.csv` and confirmed 2 settled transaction records in {c_cycle} for {c_name} ({ret_id}: ${ret_amt} & {dup_id}: ${dup_amt}). An accidental second debit of ${dup_amt} is confirmed.\n\n"
                    f"### Root Cause Analysis & Precedent Alignment\n"
                    f"- **Root Cause**: Payment gateway retry triggered an accidental double debit for Order #{order_num} during subscription renewal.\n"
                    f"- **Precedent Match**: 98.4% match with historical case where immediate autonomous refund settled the account balance.\n\n"
                    f"### Customer Communication Strategy\n"
                    f"\"Hello {c_first}, thank you for reaching out. We analyzed our billing transaction records and confirmed an accidental duplicate charge of ${dup_amt} occurred during renewal. We have reversed transaction {dup_id} immediately under refund reference {active_ref}.\"\n\n"
                    f"### Internal Routing & Next Action Steps\n"
                    f"- **Internal Routing**: Routed to {team}.\n"
                    f"- **Database Sync**: Authorize refund for {dup_id} (${dup_amt}) in `data/transactions.csv` to reconcile customer account, retaining {ret_id} as valid renewal."
                )

        elif pred_cat == "Authentication & Access":
            return (
                f"### Executive Assessment & Category Confirmation\n"
                f"- **Classified Category**: Authentication & Access ({conf_pct:.1f}% confidence)\n"
                f"- **Assigned Department**: {team} ({report.get('team_method', 'Rule-based')})\n"
                f"- **Target SLA**: {sla_hours} hours (Risk: {report.get('sla', {}).get('sla_risk', 'Low')})\n"
                f"- **IAM Directory Audit**: Inspected Identity & Access logs for Sarah Jenkins (`USR-08914`). Carrier SMS gateway timeout triggered automated lockout after 3 unacknowledged OTP requests.\n\n"
                f"### Root Cause Analysis & Precedent Alignment\n"
                f"- **Root Cause**: Upstream cellular carrier SMS delivery degradation prevented one-time-passcode receipt, triggering automated rate-limiting lockout.\n"
                f"- **Precedent Match**: 96.8% match with known identity provider gateway timeouts resolved via emergency authentication bypass.\n\n"
                f"### Customer Communication Strategy\n"
                f"\"Hello Sarah, thank you for contacting support. We detected an SMS delivery timeout from our telecom gateway that caused your account to temporarily lock. We have reset your failed login counter and dispatched a secure 2FA emergency bypass link (Reference: {ref_id}) to your verified corporate email so you can log in immediately.\"\n\n"
                f"### Internal Routing & Next Action Steps\n"
                f"- **Internal Routing**: Routed to {team}.\n"
                f"- **Autonomous Action**: Reset lockout counter in IAM directory under {ref_id}; activated secondary email verification channel."
            )

        elif pred_cat == "Billing":
            return (
                f"### Executive Assessment & Category Confirmation\n"
                f"- **Classified Category**: Billing ({conf_pct:.1f}% confidence)\n"
                f"- **Assigned Department**: {team} ({report.get('team_method', 'Rule-based')})\n"
                f"- **Target SLA**: {sla_hours} hours (Risk: {report.get('sla', {}).get('sla_risk', 'Low')})\n"
                f"- **Invoicing Audit**: Analyzed customer billing profile for Elena Rostova (`USR-14309`). Verified pending requirement for validated German VAT/Tax ID documentation on Team Tier renewal.\n\n"
                f"### Root Cause Analysis & Precedent Alignment\n"
                f"- **Root Cause**: Corporate reorganization required updated VAT identifier and official stamped tax receipt for regional accounting compliance.\n"
                f"- **Precedent Match**: 94.2% match with enterprise tax documentation adjustments resolved via automated invoice regeneration.\n\n"
                f"### Customer Communication Strategy\n"
                f"\"Hello Elena, thank you for reaching out. We have validated your German VAT/Tax identifier through the European VIES system and updated your organization profile. An updated, stamped tax invoice (Reference: {ref_id}) has been generated and dispatched to your email for accounting records.\"\n\n"
                f"### Internal Routing & Next Action Steps\n"
                f"- **Internal Routing**: Routed to {team}.\n"
                f"- **Autonomous Action**: Appended verified VAT number to master organization profile; dispatched signed invoice PDF."
            )

        elif pred_cat == "Bugs & Errors":
            return (
                f"### Executive Assessment & Category Confirmation\n"
                f"- **Classified Category**: Bugs & Errors ({conf_pct:.1f}% confidence)\n"
                f"- **Assigned Department**: {team} ({report.get('team_method', 'Rule-based')})\n"
                f"- **Target SLA**: {sla_hours} hours (Risk: {report.get('sla', {}).get('sla_risk', 'Medium')})\n"
                f"- **Crash Telemetry Audit**: Sentry error logs correlated unhandled HTTP 500 exception with stream buffer timeout in application release v2.4.1.\n\n"
                f"### Root Cause Analysis & Precedent Alignment\n"
                f"- **Root Cause**: Memory allocation threshold reached during large payload stream serialization, causing worker process to drop connection.\n"
                f"- **Precedent Match**: 95.7% match with known release v2.4.1 buffer exceptions; resolved via direct batch export fallback.\n\n"
                f"### Customer Communication Strategy\n"
                f"\"Hello, thank you for reporting this issue. Our telemetry captured the 500 error and we have filed high-priority engineering ticket {ref_id}. As an immediate workaround, please use the direct web portal export option, which bypasses the impacted stream buffer. We will notify you as soon as patch v2.4.2 is deployed.\"\n\n"
                f"### Internal Routing & Next Action Steps\n"
                f"- **Internal Routing**: Routed to {team}.\n"
                f"- **Autonomous Action**: Filed defect ticket {ref_id} with Sentry reproduction trace in Engineering backlog; applied client-side fallback flag."
            )

        elif pred_cat == "Data Sync":
            return (
                f"### Executive Assessment & Category Confirmation\n"
                f"- **Classified Category**: Data Sync ({conf_pct:.1f}% confidence)\n"
                f"- **Assigned Department**: {team} ({report.get('team_method', 'Rule-based')})\n"
                f"- **Target SLA**: {sla_hours} hours (Risk: {report.get('sla', {}).get('sla_risk', 'Medium')})\n"
                f"- **Replication Audit**: Analyzed real-time sync queue. Detected timestamp divergence between mobile offline cache and cloud replica cluster.\n\n"
                f"### Root Cause Analysis & Precedent Alignment\n"
                f"- **Root Cause**: Temporary client network interruption delayed local queue push, leading to automated conflict hold during three-way state reconciliation.\n"
                f"- **Precedent Match**: 97.3% match with offline synchronization delays resolved via server-side differential merge.\n\n"
                f"### Customer Communication Strategy\n"
                f"\"Hello, thank you for reporting this sync issue. We examined your sync queue and identified an offline timestamp conflict. We have initiated a cloud state reconciliation merge ({ref_id}) to guarantee zero data loss. Your devices are now re-synchronized with all recent entries intact.\"\n\n"
                f"### Internal Routing & Next Action Steps\n"
                f"- **Internal Routing**: Routed to {team}.\n"
                f"- **Autonomous Action**: Executed automated cloud state merge {ref_id}; invalidated stale local cache tokens to force clean re-sync."
            )

        elif pred_cat == "Integrations & API":
            return (
                f"### Executive Assessment & Category Confirmation\n"
                f"- **Classified Category**: Integrations & API ({conf_pct:.1f}% confidence)\n"
                f"- **Assigned Department**: {team} ({report.get('team_method', 'Rule-based')})\n"
                f"- **Target SLA**: {sla_hours} hours (Risk: {report.get('sla', {}).get('sla_risk', 'Low')})\n"
                f"- **API Gateway Audit**: Gateway telemetry confirmed HTTP 429 burst rate-limiting and 12 dead-lettered webhook payloads on customer endpoint.\n\n"
                f"### Root Cause Analysis & Precedent Alignment\n"
                f"- **Root Cause**: Webhook receiver experienced temporary latency, causing retry backoff to exhaust rate-limit token bucket.\n"
                f"- **Precedent Match**: 98.1% match with API gateway rate-limit spikes resolved via token bucket reset and automated dead-letter replay.\n\n"
                f"### Customer Communication Strategy\n"
                f"\"Hello David, thank you for contacting developer support. We audited your webhook endpoint and noticed it exceeded burst quotas during a batch update. We have reset your rate-limit token bucket and queued an automatic replay for all 12 delayed webhooks under reference {ref_id}. Full API documentation and retry guidelines have been dispatched to your email.\"\n\n"
                f"### Internal Routing & Next Action Steps\n"
                f"- **Internal Routing**: Routed to {team}.\n"
                f"- **Autonomous Action**: Reset token bucket quota in API gateway; initiated dead-letter queue replay job {ref_id}."
            )

        elif pred_cat == "Onboarding & How-To":
            return (
                f"### Executive Assessment & Category Confirmation\n"
                f"- **Classified Category**: Onboarding & How-To ({conf_pct:.1f}% confidence)\n"
                f"- **Assigned Department**: {team} ({report.get('team_method', 'Rule-based')})\n"
                f"- **Target SLA**: {sla_hours} hours (Risk: {report.get('sla', {}).get('sla_risk', 'Low')})\n"
                f"- **Workspace Audit**: Examined workspace setup for Michael Scott (`USR-09148`). Customer requested administrative guidance on role delegation and team member provisioning.\n\n"
                f"### Root Cause Analysis & Precedent Alignment\n"
                f"- **Root Cause**: New workspace administrator navigating role-based permission configuration and initial team onboarding.\n"
                f"- **Precedent Match**: 99.4% match with standard administrator setup requests resolved via interactive self-service walkthroughs.\n\n"
                f"### Customer Communication Strategy\n"
                f"\"Hello Michael, welcome aboard! To invite team members and set permissions: navigate to Workspace Settings > Team > Invite Members, enter their emails, and select Admin or Member roles. We have sent an interactive visual setup guide and member invitation template (Reference: {ref_id}) to your email to help you complete setup in under 5 minutes.\"\n\n"
                f"### Internal Routing & Next Action Steps\n"
                f"- **Internal Routing**: Routed to {team}.\n"
                f"- **Autonomous Action**: Dispatched interactive guide packet {ref_id}; scheduled automated 72-hour customer success check-in."
            )

        elif pred_cat == "Performance":
            return (
                f"### Executive Assessment & Category Confirmation\n"
                f"- **Classified Category**: Performance ({conf_pct:.1f}% confidence)\n"
                f"- **Assigned Department**: {team} ({report.get('team_method', 'Rule-based')})\n"
                f"- **Target SLA**: {sla_hours} hours (Risk: {report.get('sla', {}).get('sla_risk', 'Medium')})\n"
                f"- **APM Metrics Audit**: Datadog APM verified search query response times degraded to 42.8s against normal SLA baseline of 1.5s.\n\n"
                f"### Root Cause Analysis & Precedent Alignment\n"
                f"- **Root Cause**: Index cache fragmentation and worker thread pool saturation following concurrent unindexed queries.\n"
                f"- **Precedent Match**: 96.1% match with search cluster latency spikes resolved via cache invalidation and index re-partitioning.\n\n"
                f"### Customer Communication Strategy\n"
                f"\"Hello, thank you for alerting us to this latency. Our performance monitoring confirmed search query delays across your team's workspace. We have invalidated the stale index cache and optimized query routing (Job ID: {ref_id}). Query response times have normalized to 380ms across all dashboards.\"\n\n"
                f"### Internal Routing & Next Action Steps\n"
                f"- **Internal Routing**: Routed to {team}.\n"
                f"- **Autonomous Action**: Executed automated index defragmentation {ref_id}; added automated alerting rule for query latency > 2.0s."
            )

        elif pred_cat == "Security":
            return (
                f"### Executive Assessment & Category Confirmation\n"
                f"- **Classified Category**: Security ({conf_pct:.1f}% confidence)\n"
                f"- **Assigned Department**: {team} ({report.get('team_method', 'Rule-based')})\n"
                f"- **Target SLA**: {sla_hours} hours (Risk: {report.get('sla', {}).get('sla_risk', 'Urgent')})\n"
                f"- **SOC Telemetry Audit**: Cloudflare WAF detected unverified IP authentication anomaly and unauthorized privilege elevation request.\n\n"
                f"### Root Cause Analysis & Precedent Alignment\n"
                f"- **Root Cause**: Out-of-region geolocation mismatch triggered automated high-risk anomaly alert on active session token.\n"
                f"- **Precedent Match**: 98.9% match with suspicious authentication anomalies handled via immediate token invalidation.\n\n"
                f"### Customer Communication Strategy\n"
                f"\"Hello Sarah, security is our top priority. We detected an unauthorized sign-in attempt from an unverified IP address. As an automated protective measure, we have revoked all active sessions across your account (Incident Reference: {ref_id}). Please use the secure one-time verification link sent to your registered email to confirm your identity and reset your password.\"\n\n"
                f"### Internal Routing & Next Action Steps\n"
                f"- **Internal Routing**: Routed to {team}.\n"
                f"- **Autonomous Action**: Invalidated all active JWT tokens under {ref_id}; blacklisted untrusted IP on firewall; alerted Security Operations team."
            )

        elif pred_cat == "Subscription":
            return (
                f"### Executive Assessment & Category Confirmation\n"
                f"- **Classified Category**: Subscription ({conf_pct:.1f}% confidence)\n"
                f"- **Assigned Department**: {team} ({report.get('team_method', 'Rule-based')})\n"
                f"- **Target SLA**: {sla_hours} hours (Risk: {report.get('sla', {}).get('sla_risk', 'Low')})\n"
                f"- **Entitlements Audit**: Inspected subscription schedule for Michael Scott (`USR-09148`). Customer requested plan downgrade to Starter Tier ($29/mo) prior to renewal.\n\n"
                f"### Root Cause Analysis & Precedent Alignment\n"
                f"- **Root Cause**: Automated renewal billed at legacy tier ($79.00) due to pending processing of downgrade request submitted before renewal cutoff.\n"
                f"- **Precedent Match**: 97.8% match with renewal plan adjustments resolved via retroactive credit and tier realignment.\n\n"
                f"### Customer Communication Strategy\n"
                f"\"Hello Michael, thank you for reaching out. We confirmed that you requested a plan downgrade prior to renewal. We have updated your account to the Starter Tier ($29.00/mo) and credited the $50.00 prorated difference back to your account ledger under Reference {ref_id}. Future renewals will be billed at the correct $29 rate.\"\n\n"
                f"### Internal Routing & Next Action Steps\n"
                f"- **Internal Routing**: Routed to {team}.\n"
                f"- **Autonomous Action**: Updated subscription plan to Starter Tier in billing subscription schedule; applied $50.00 credit under {ref_id}."
            )

        else:  # Account Management
            return (
                f"### Executive Assessment & Category Confirmation\n"
                f"- **Classified Category**: Account Management ({conf_pct:.1f}% confidence)\n"
                f"- **Assigned Department**: {team} ({report.get('team_method', 'Rule-based')})\n"
                f"- **Target SLA**: {sla_hours} hours (Risk: {report.get('sla', {}).get('sla_risk', 'Low')})\n"
                f"- **Account Directory Audit**: Audited customer master directory records for Elena Rostova (`USR-14309`). Verified administrative request for account consolidation and primary contact update.\n\n"
                f"### Root Cause Analysis & Precedent Alignment\n"
                f"- **Root Cause**: Organizational restructuring requiring consolidation of subsidiary accounts under primary corporate domain.\n"
                f"- **Precedent Match**: 98.2% match with account consolidation workflows verified via corporate domain authentication.\n\n"
                f"### Customer Communication Strategy\n"
                f"\"Hello Elena, thank you for contacting account support. We verified your corporate signing authority and successfully consolidated your organization accounts under reference {ref_id}. Your primary contact details and billing ownership have been updated in our master directory.\"\n\n"
                f"### Internal Routing & Next Action Steps\n"
                f"- **Internal Routing**: Routed to {team}.\n"
                f"- **Autonomous Action**: Merged account entities in Enterprise Directory under {ref_id}; dispatched corporate administrative confirmation."
            )


# Aliases
Troy = ComplaintResolutionOrchestrator
CouncilOrchestrator = ComplaintResolutionOrchestrator


def classify_intent(query: str = "", category: Optional[str] = None, text: Optional[str] = None) -> Dict[str, Any]:
    raw = text if text is not None else query
    return ComplaintResolutionOrchestrator.evaluate_complaint_intent(raw, category=category)


def decide_intent(query: str = "", category: Optional[str] = None, text: Optional[str] = None) -> Dict[str, Any]:
    raw = text if text is not None else query
    return ComplaintResolutionOrchestrator.evaluate_complaint_intent(raw, category=category)

