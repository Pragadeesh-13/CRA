# Product Variant: Admin-Configured CRA

## Goal

Let a SaaS administrator connect support and product systems, map customer and transaction fields, and configure what Troy may resolve.

## First-Run Flow

1. Admin selects supported integrations and authorizes scoped access.
2. Admin maps customer, subscription, invoice, and ticket identifiers.
3. Admin sets resolution policies, refund limits, approval requirements, and escalation rules.
4. Troy runs connection and dry-run checks before live actions are enabled.

## Shared Runtime Requirements

Keep this variant on the same CRA core as the automated-onboarding experiment: tenant-scoped integration adapters, least-privilege tools, idempotent actions, audit records, and human review for actions outside configured policy.

## Success Criteria

An admin can configure one SaaS tenant without code changes, verify field mappings with sample records, and enable one safe workflow such as confirmed duplicate-charge remediation.
