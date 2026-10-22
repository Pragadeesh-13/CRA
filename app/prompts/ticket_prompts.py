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


def build_ticket_title_and_summary_prompt(text: str) -> Tuple[List[Dict[str, str]], int]:
    complaint_text = text.strip() if isinstance(text, str) else ""
    messages = [
        {
            "role": "system",
            "content": (
                "You are an expert customer complaint triage agent. "
                "Analyze the incoming complaint ticket and output a JSON object with exactly two keys:\n"
                '1. "title": A concise, professional Title (3-6 words in Title Case describing the core complaint, e.g. "Duplicate Renewal Charge Dispute", "2FA SMS Delivery Timeout", "German VAT Invoice Request", "Android PDF Export Crash").\n'
                '2. "summary": A clear, objective 1-2 sentence executive summary of the customer complaint (focusing on what happened, key entities/amounts, and the requested resolution).\n'
                "Do not include internal thinking tags or conversational preambles. Output ONLY valid raw JSON."
            ),
        },
        {
            "role": "user",
            "content": f"Customer Complaint Ticket:\n\"\"\"\n{complaint_text}\n\"\"\"\n\nJSON output:",
        },
    ]
    return messages, 400


# Aliases for backward compatibility
build_title_prompt = build_ticket_title_prompt
build_summary_prompt = build_ticket_summary_prompt
build_title_and_summary_prompt = build_ticket_title_and_summary_prompt
