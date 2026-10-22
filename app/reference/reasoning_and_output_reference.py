"""
Reference Architecture: LLM Reasoning & Output Processing.

This module serves as the permanent reference implementation for how reasoning 
(chain-of-thought) and model output are streamed, parsed, sanitized, and dispatched 
to clients (via SSE).

Use this as the architectural reference when building the real Complaint Resolution Agent Troy.

Key Concepts:
1. Streaming with Live Reasoning:
   - Modern models (e.g., DeepSeek-R1, Qwen-2.5/3.5, etc.) stream thinking tokens either:
     a) Via dedicated API parameters (e.g., reasoning_content deltas).
     b) Inside <think>...</think> or <thought>...</thought> tags in the main content stream.
   - ThoughtBuffer partitions raw tokens into:
     - `reasoning_buffer` -> broadcast via 'agent_reasoning_delta'
     - `public_buffer`    -> broadcast via 'agent_delta'
2. Non-Streaming Fallback:
   - When streaming is disabled, the complete response is inspected with regex to extract
     <think>...</think> blocks.
3. Sanitization & Final Payload:
   - The public output is sanitized (removing residual internal tokens/tags).
   - Broadcasts the finalized 'agent' event containing both `message` and `reasoning`.
"""

import logging
import re
from typing import List, Dict, Any, Optional

from app.utils.sanitizer import sanitize_model_output
from app.utils.thought_buffer import ThoughtBuffer
from app.providers.base_driver import BaseLlmDriver
from app.http.sse_manager import SseManager

logger = logging.getLogger("reference:reasoning")


async def execute_agent_with_reasoning(
    driver: BaseLlmDriver,
    sse_manager: SseManager,
    agent_name: str,
    role_or_state: str,
    messages: List[Dict[str, str]],
    max_tokens: int = 4096,
    round_num: int = 0,
    label: str = "Resolution",
    show_reasoning: bool = True,
    use_stream: bool = True,
    extra_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    """
    REFERENCE DEMO LOGIC:
    Executes an LLM call while handling real-time reasoning and content streaming.

    Returns:
        Dict with {"message": cleaned_output, "reasoning": reasoning_text}
    """
    payload_base = {
        "agent": agent_name,
        "state": role_or_state,
        "round": round_num,
        "label": label,
        **(extra_payload or {}),
    }

    # Signal the UI that this agent has started generating
    sse_manager.broadcast("agent_start", {**payload_base, "show_reasoning": show_reasoning})

    if use_stream:
        # Buffer that parses thinking/thought tags and reasoning tokens on the fly
        tb = ThoughtBuffer(
            on_delta=lambda full, chunk: sse_manager.broadcast(
                "agent_delta", {**payload_base, "delta": chunk}
            ),
            on_reasoning_delta=lambda full, chunk: (
                sse_manager.broadcast("agent_reasoning_delta", {**payload_base, "delta": chunk})
                if show_reasoning
                else None
            ),
        )

        def on_delta(delta: str):
            tb.feed(delta)

        def on_reasoning_delta(r_delta: str):
            tb.feed_reasoning(r_delta)

        try:
            await driver.stream(
                messages,
                max_tokens,
                on_delta=on_delta,
                on_reasoning_delta=on_reasoning_delta,
            )
            tb.flush()
            cleaned_message = sanitize_model_output(tb.public_buffer)
            full_reasoning = tb.reasoning_buffer.strip()

            # Broadcast final agent resolution payload
            sse_manager.broadcast(
                "agent",
                {
                    **payload_base,
                    "message": cleaned_message,
                    "reasoning": full_reasoning if show_reasoning else "",
                },
            )
            return {"message": cleaned_message, "reasoning": full_reasoning}

        except Exception as e:
            logger.error(f"Streaming execution failed for agent '{agent_name}': {e}")
            fallback = "Signal lost. Unable to reach model provider."
            sse_manager.broadcast("agent", {**payload_base, "message": fallback, "reasoning": ""})
            return {"message": fallback, "reasoning": ""}

    else:
        # Non-streaming fallback
        try:
            raw_response = await driver.complete(messages, max_tokens)
            cleaned_message = sanitize_model_output(raw_response)

            thought_match = re.search(
                r"<(?:thought|think)\b[^>]*>([\s\S]*?)<\/(?:thought|think)>",
                raw_response,
                re.IGNORECASE,
            )
            extracted_reasoning = thought_match.group(1).strip() if thought_match else ""

            sse_manager.broadcast(
                "agent",
                {
                    **payload_base,
                    "message": cleaned_message,
                    "reasoning": extracted_reasoning if show_reasoning else "",
                },
            )
            return {"message": cleaned_message, "reasoning": extracted_reasoning}

        except Exception as e:
            logger.error(f"Completion execution failed for agent '{agent_name}': {e}")
            fallback = "Signal lost. Unable to reach model provider."
            sse_manager.broadcast("agent", {**payload_base, "message": fallback, "reasoning": ""})
            return {"message": fallback, "reasoning": ""}
