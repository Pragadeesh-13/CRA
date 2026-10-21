"""
Compatibility wrapper redirecting to ticket_prompts.py.
All policy logic has been migrated to Complaint Ticket resolution.
"""

from app.prompts.ticket_prompts import (
    build_ticket_title_prompt,
    build_ticket_summary_prompt,
    build_title_prompt,
    build_summary_prompt,
)

__all__ = [
    "build_ticket_title_prompt",
    "build_ticket_summary_prompt",
    "build_title_prompt",
    "build_summary_prompt",
]
