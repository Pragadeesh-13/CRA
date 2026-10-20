"""
Complaint Resolution Orchestrator.

Base orchestrator for the Complaint Resolution Agent Troy.
Legacy agentic council logic and placeholder scripts have been removed.
The reasoning and output processing reference logic is maintained in app.reference.
"""

import asyncio
import datetime
import logging
import re
from typing import List, Dict, Any, Optional

from app.utils.sanitizer import sanitize_model_output
from app.simulation.state import SimulationState
from app.simulation.round_barrier import RoundBarrier
from app.http.sse_manager import SseManager
from app.providers.base_driver import BaseLlmDriver
from app.prompts.policy_prompts import build_title_prompt, build_summary_prompt
from app.reference.reasoning_and_output_reference import execute_agent_with_reasoning

logger = logging.getLogger("simulation:orchestrator")


class ComplaintResolutionOrchestrator:
    """
    Orchestrator managing session state and LLM execution for the Complaint Resolution Agent.
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

    def _active_driver(self) -> BaseLlmDriver:
        return self._run_driver or self.driver

    async def _complete(
        self, messages: List[Dict[str, str]], max_tokens: int = 4096, driver: Optional[BaseLlmDriver] = None
    ) -> str:
        d = driver or self._active_driver()
        return await d.complete(messages, max_tokens)

    async def summarise_policy(self, query: str, driver: Optional[BaseLlmDriver] = None) -> Optional[Dict[str, Any]]:
        text = (query or "").strip()
        if not text:
            return None

        active = driver or self._active_driver()
        short_title = await self._summarize_title(text, active)
        self.sse.broadcast("topic", {"title": short_title or "Complaint"})

        summary = await self._summarize_text(text, active)

        story = {
            "title": short_title or summary or "Complaint",
            "source": "Complaint Resolution",
            "url": "",
            "publishedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "description": text,
            "summary": summary,
        }
        self.set_story(story)
        self.sse.broadcast("feed", story)
        return story

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
        Pipeline entry point.
        Ready for implementing Troy's complaint resolution agent logic.
        """
        self.sse.broadcast(
            "council_start",
            {
                "story": {"title": story.get("title", ""), "url": story.get("url", "")},
                "agents": [{"name": "Troy", "state": "Complaint Agent"}],
            },
        )
        self.sse.broadcast("round_start", {"round": 0, "label": "Resolution", "count": 1, "order": ["Troy"]})
        self.sse.broadcast("system", {"round": 0, "message": f"Processing: {story.get('title', 'Complaint')}"})

        # Troy complaint resolution logic will be built here
        # For now, emits lifecycle completion
        self.sse.broadcast(
            "council_end",
            {
                "round": 0,
                "summary": f"Complaint '{story.get('title', 'Complaint')}' registered for resolution.",
                "impacts": {"positive": ["Registered"], "negative": []},
            },
        )

    async def _agent_speak(
        self,
        agent: Dict[str, Any],
        messages: List[Dict[str, str]],
        max_tokens: int,
        round_num: int = 0,
        label: str = "Resolution",
    ) -> str:
        """
        Executes an agent call using the reference reasoning and output streaming handler.
        """
        use_stream = getattr(self.config, "llm_stream", True) is not False
        show_reasoning = getattr(self.config, "show_reasoning", False)

        result = await execute_agent_with_reasoning(
            driver=self._active_driver(),
            sse_manager=self.sse,
            agent_name=agent.get("name", "Troy"),
            role_or_state=agent.get("state", "Complaint Agent"),
            messages=messages,
            max_tokens=max_tokens,
            round_num=round_num,
            label=label,
            show_reasoning=show_reasoning,
            use_stream=use_stream,
        )
        return result.get("message", "")

    async def _summarize_title(self, text: str, driver: BaseLlmDriver) -> str:
        messages, max_tokens = build_title_prompt(text)
        try:
            res = await driver.complete(messages, max_tokens)
            cleaned = sanitize_model_output(res)
            words = [w for w in re.sub(r"[^\w\s]", " ", cleaned).strip().split() if w]
            if words:
                candidate = " ".join(words[:3])
                if not re.match(r"^policy$", candidate, re.IGNORECASE):
                    return candidate
        except Exception as e:
            logger.error(f"Title extraction failed: {e}")
        return self._title_fallback(text)

    def _title_fallback(self, text: str) -> str:
        cleaned = re.sub(r"[#*_>`~\[\]\(\)\{\}\"'\-]", " ", text or "")
        stop_words = {"the", "a", "an", "and", "or", "of", "to", "in", "for", "on", "with", "by", "as", "is", "are", "be"}
        words = [w.capitalize() for w in cleaned.split() if w.lower() not in stop_words]
        return " ".join(words[:3]) if words else "Complaint Issue"

    async def _summarize_text(self, text: str, driver: BaseLlmDriver) -> str:
        messages, max_tokens = build_summary_prompt(text)
        try:
            res = await driver.complete(messages, max_tokens)
            cleaned = sanitize_model_output(res)
            if cleaned and cleaned != "Policy summary unavailable.":
                sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", cleaned) if s.strip()]
                if len(sentences) >= 2:
                    return f"{sentences[0]} {sentences[1]}"
                if sentences:
                    return sentences[0]
        except Exception as e:
            logger.error(f"Summary text failed: {e}")

        cleaned_text = (text or "").strip()
        return f"Complaint regarding {cleaned_text}."


# Backward compatibility alias
CouncilOrchestrator = ComplaintResolutionOrchestrator
