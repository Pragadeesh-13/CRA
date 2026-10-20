from typing import List, Dict, Tuple


def build_title_prompt(text: str) -> Tuple[List[Dict[str, str]], int]:
    policy_text = text.strip() if isinstance(text, str) else ""
    messages = [
        {
            "role": "system",
            "content": (
                "You are a concise policy labeler. You create short policy names. "
                "Output only a 2-3 word title for the policy. Title case, no punctuation, no quotes."
            ),
        },
        {
            "role": "user",
            "content": f"Policy details:\n{policy_text}\n\nTitle:",
        },
    ]
    return messages, 4096


def build_summary_prompt(text: str) -> Tuple[List[Dict[str, str]], int]:
    policy_text = text.strip() if isinstance(text, str) else ""
    messages = [
        {
            "role": "system",
            "content": (
                "You are a policy summarizer. Summarize the policy in 1-2 clear sentences. "
                "The first sentence must begin with 'The policy is about' and describe the core intent. "
                "If a second sentence is needed, add scope or key obligations. "
                "No bullets, no quotes, no preamble beyond the required opening."
            ),
        },
        {
            "role": "user",
            "content": f"Policy:\n{policy_text}\n\nSummary:",
        },
    ]
    return messages, 4096
