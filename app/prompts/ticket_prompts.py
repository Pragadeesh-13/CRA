"""
Prompt builders for Customer Complaint Ticket processing.
Strictly tailored for Complaint Resolution Agent (CRA) Troy.
"""

from typing import List, Dict, Tuple


def build_ticket_title_prompt(text: str) -> Tuple[List[Dict[str, str]], int]:
    complaint_text = text.strip() if isinstance(text, str) else ""
    messages = [
        {
            "role": "system",
            "content": (
                "You are an expert customer service ticket classifier. "
                "Output only a 2 to 4 word concise title summarizing the core complaint ticket issue. "
                "Use Title Case. No punctuation, no quotes, no filler words."
            ),
        },
        {
            "role": "user",
            "content": f"Customer complaint ticket:\n{complaint_text}\n\nTicket Title:",
        },
    ]
    return messages, 50


def build_ticket_summary_prompt(text: str) -> Tuple[List[Dict[str, str]], int]:
    complaint_text = text.strip() if isinstance(text, str) else ""
    messages = [
        {
            "role": "system",
            "content": (
                "You are a customer complaint ticket summarizer. "
                "Summarize the customer's issue in 1-2 direct, objective sentences. "
                "Focus on what went wrong and what the customer is requesting. "
                "Start directly with the customer issue or action needed. "
                "No quotes, no bullets, no conversational filler."
            ),
        },
        {
            "role": "user",
            "content": f"Complaint Ticket:\n{complaint_text}\n\nSummary:",
        },
    ]
    return messages, 150


# Aliases for backward compatibility
build_title_prompt = build_ticket_title_prompt
build_summary_prompt = build_ticket_summary_prompt
