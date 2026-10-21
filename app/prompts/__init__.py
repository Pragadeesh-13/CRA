from app.prompts.ticket_prompts import (
    build_ticket_title_prompt,
    build_ticket_summary_prompt,
    build_title_prompt,
    build_summary_prompt,
)

DEFAULT_MAX_TOKENS: int = 4096

__all__ = [
    "DEFAULT_MAX_TOKENS",
    "build_ticket_title_prompt",
    "build_ticket_summary_prompt",
    "build_title_prompt",
    "build_summary_prompt",
]
