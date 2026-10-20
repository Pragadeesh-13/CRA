# Dataset Details

## File Information

| Property | Value |
|---|---|
| **Filename** | `complaints-deduplicated.csv` |
| **Rows** | 277,373 |
| **Columns** | 36 |
| **Source** | Deduplicated ~18% sample of `train-00000-of-00001.parquet` (1.5M rows) |
| **PII** | Stripped — `customer_name` and `customer_email` removed |

---

## Uniqueness Verification

| Check | Result |
|---|---|
| Full duplicate rows | ✅ 0 |
| Duplicate `ticket_id` | ✅ 0 |
| Duplicate `issue_description` | ✅ 0 — every complaint text is unique |
| `ticket_id` range | 1 → 1,499,989 |

---

## Columns

| Column | Type | Nulls | Description |
|---|---|---|---|
| `ticket_id` | int64 | 0 | Unique ticket identifier |
| `created_at` | str | 0 | Timestamp when the ticket was created |
| `issue_description` | str | 0 | Free-text complaint from the user |
| `category` | str | 0 | Complaint category (11 classes) |
| `issue_type_id` | str | 0 | Granular issue type slug |
| `product` | str | 0 | Product the complaint relates to |
| `channel` | str | 0 | Submission channel (Email, Chat, Phone, etc.) |
| `region` | str | 0 | Geographic region of the customer |
| `language` | str | 0 | Language of the customer |
| `customer_age` | int64 | 0 | Age of the customer |
| `customer_gender` | str | 0 | Gender of the customer |
| `subscription_type` | str | 0 | Free / Basic / Premium |
| `customer_tenure_months` | int64 | 0 | How long the customer has been subscribed |
| `previous_tickets` | int64 | 0 | Number of tickets previously raised |
| `customer_segment` | str | 0 | Individual / Corporate / Enterprise |
| `priority` | str | 0 | Low / Medium / High / Urgent |
| `issue_complexity_score` | int64 | 0 | Numeric complexity rating of the issue |
| `assigned_team` | str | 0 | Team assigned to handle the ticket |
| `assigned_to` | str | 0 | Individual agent assigned |
| `status` | str | 0 | Current ticket status (7 states) |
| `escalated` | str | ~28,597 | Whether the ticket was escalated (Yes/No) |
| `escalation_reason` | str | ~202,042 | Reason for escalation (null = not escalated) |
| `reopen_count` | int64 | 0 | Number of times the ticket was reopened |
| `first_response_time_hours` | float64 | 0 | Hours until first agent response |
| `resolution_time_hours` | float64 | ~28,597 | Hours taken to resolve |
| `paused_hours` | float64 | 0 | Hours the ticket was on hold/paused |
| `resolution_wallclock_hours` | float64 | ~28,597 | Wall-clock hours from open to resolution |
| `first_resolved_at` | str | ~28,597 | Timestamp of first resolution |
| `resolved_at` | str | ~28,597 | Timestamp of final resolution |
| `closed_at` | str | ~19,239 | Timestamp when ticket was closed |
| `resolved_by` | str | ~28,597 | Agent who resolved the ticket |
| `resolution_notes` | str | ~18,086 | Agent's notes on how the issue was resolved |
| `sla_target_hours` | int64 | 0 | SLA deadline in hours |
| `sla_breached` | str | 0 | Whether SLA was breached (Yes/No) |
| `sla_breach_margin` | float64 | ~28,597 | Hours remaining or over SLA |
| `customer_satisfaction_score` | float64 | ~28,811 | Post-resolution CSAT rating |

> **Note:** Nulls on resolution/closure columns are expected — they correspond to open or cancelled tickets that have not yet been resolved.

---

## Category Distribution

| Category | Count |
|---|---|
| Account Management | 27,467 |
| Billing | 25,900 |
| Onboarding & How-To | 25,455 |
| Data Sync | 25,392 |
| Authentication & Access | 25,196 |
| Security | 25,069 |
| Integrations & API | 24,972 |
| Subscription | 24,857 |
| Bugs & Errors | 24,549 |
| Performance | 24,532 |
| Refunds | 23,984 |

> **Well-balanced** — no significant class imbalance. Suitable for training classifiers without resampling.

---

## Assigned Team Distribution

| Team | Count |
|---|---|
| Tier 1 Support | 99,824 |
| Engineering | 69,853 |
| Billing | 44,359 |
| Tier 2 Support | 41,667 |
| Security | 21,670 |

---

## Ticket Status Distribution

| Status | Count |
|---|---|
| Closed | 247,623 |
| Open | 12,849 |
| Cancelled | 10,511 |
| Pending | 2,972 |
| On-Hold | 2,120 |
| Resolved | 1,153 |
| New | 145 |

---

## Product Distribution

| Product | Count (approx.) |
|---|---|
| Desktop Client | ~46,000 |
| API Platform | ~46,000 |
| Web Portal | ~46,000 |
| Payment Gateway | ~46,000 |
| Analytics Suite | ~46,000 |
| Mobile App | ~46,000 |

---

## Channel Distribution

| Channel | Count (approx.) |
|---|---|
| Email | ~94,000 |
| Chat | ~83,000 |
| Self-Service | ~42,000 |
| Phone | ~39,000 |
| Social Media | ~19,000 |

---

## Pipeline Mapping

| Project Stage | Columns Used |
|---|---|
| **User complaint input** | `issue_description`, `product`, `channel`, `priority` |
| **Predict category** | `category` (target), `issue_description` (feature) |
| **Recommend a team** | `assigned_team` (target), `category`, `priority`, `issue_complexity_score` |
| **Simulated agent action** | `resolution_notes`, `status`, `escalated`, `assigned_to` |
| **Ticket status / result** | `status`, `sla_breached`, `sla_breach_margin`, `customer_satisfaction_score` |

---

## Recommended Usage

- **Primary training dataset** — use this CSV over the full parquet for development
- **Suggested split:** 80% train / 10% validation / 10% test (stratified by `category`)
- **Live demo tickets:** filter `status == 'Open'` (~12,849 rows) for simulated agent actions
- **Scale-up path:** revert to the 1.5M-row parquet for production model training if needed
