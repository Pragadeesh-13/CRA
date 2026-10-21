from typing import List, Dict, Tuple


def build_title_prompt(text: str) -> Tuple[List[Dict[str, str]], int]:
    complaint_text = text.strip() if isinstance(text, str) else ""
    messages = [
        {
            "role": "system",
            "content": (
                "You are a concise customer complaint ticket labeler. "
                "Output only a 2-4 word title summarizing the complaint topic. Title case, no punctuation, no quotes."
            ),
        },
        {
            "role": "user",
            "content": f"Customer complaint:\n{complaint_text}\n\nTitle:",
        },
    ]
    return messages, 4096


def build_summary_prompt(text: str) -> Tuple[List[Dict[str, str]], int]:
    complaint_text = text.strip() if isinstance(text, str) else ""
    messages = [
        {
            "role": "system",
            "content": (
                "You are a complaint ticket summarizer. Summarize the customer issue in 1-2 clear, objective sentences. "
                "No bullet points, no quotes, no conversational filler."
            ),
        },
        {
            "role": "user",
            "content": f"Complaint:\n{complaint_text}\n\nSummary:",
        },
    ]
    return messages, 4096
