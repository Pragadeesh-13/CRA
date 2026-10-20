import re
from typing import Callable, List, Optional


class ThoughtBuffer:
    """
    A robust streaming parser that separates reasoning/thought blocks from public speech.
    
    Handles:
    - Chunk-split tags: e.g. ["<", "th", "ought>", "Thinking...", "</", "thought>"]
    - Case-insensitivity: <thought>, <Thought>, <THINK>, <think>
    - Attributes inside tags: <thought stage="1">
    - Transition junk after thought closure:
        - Whitespace / newlines
        - Markdown dividers (****, ---, ___)
        - Speech headers: (Public Speech), [Public Speech], **Public Speech:**, Public Speech:
    - Real-time streaming:
        - Reasoning chunks emitted to on_reasoning_delta without closing tags
        - Public speech chunks emitted to on_delta without thought tags or transition junk
    - Models with no thought tags: zero delay, instant emission
    """

    STATE_START = 0
    STATE_IN_THOUGHT = 1
    STATE_TRANSITION = 2
    STATE_PUBLIC = 3

    OPEN_TAG_RE = re.compile(r"^<(?:thought|think)\b[^>]*>", re.IGNORECASE)
    CLOSE_TAG_RE = re.compile(r"<\/(?:thought|think)>", re.IGNORECASE)

    DIVIDER_RE = re.compile(r"^(?:\*{3,}|-{3,}|_{3,})\s*")
    SPEECH_HEADER_RE = re.compile(
        r"^(?:(?:\*{1,2}|\[|\()?\s*(?:Public\s+)?(?:Speech|Statement|Verdict|Decision)\s*(?:\([^)]*\))?\s*(?:\*{1,2}|\]|\))?\s*:?\s*)",
        re.IGNORECASE,
    )

    def __init__(
        self,
        threshold: int = 60,
        on_delta: Optional[Callable[..., None]] = None,
        on_reasoning_delta: Optional[Callable[..., None]] = None,
        tag_names: Optional[List[str]] = None,
    ):
        self.threshold = threshold
        self.on_delta = on_delta
        self.on_reasoning_delta = on_reasoning_delta
        self.tag_names = tag_names or ["thought", "think"]

        self.full_raw = ""
        self.pending_buffer = ""
        self.public_buffer = ""
        self.reasoning_buffer = ""
        self.state = self.STATE_START

    def feed(self, delta: str) -> str:
        if not delta:
            return self.public_buffer

        self.full_raw += str(delta)
        self.pending_buffer += str(delta)

        self._process_buffer()
        return self.public_buffer

    def feed_reasoning(self, delta: str):
        if not delta:
            return
        self._emit_reasoning(delta)

    def flush(self) -> str:
        if self.state == self.STATE_START:
            if self.pending_buffer:
                lstripped = self.pending_buffer.lstrip()
                if not lstripped.startswith("<"):
                    self._emit_public(lstripped)
        elif self.state == self.STATE_IN_THOUGHT:
            # Unclosed thought block: flush remainder to reasoning, not public
            if self.pending_buffer:
                clean_pending = re.sub(r"<\/?(?:thought|think)\b[^>]*>", "", self.pending_buffer, flags=re.IGNORECASE)
                if clean_pending:
                    self._emit_reasoning(clean_pending)
                self.pending_buffer = ""
        elif self.state == self.STATE_TRANSITION:
            clean_pending = self.pending_buffer.strip()
            clean_pending = self.DIVIDER_RE.sub("", clean_pending)
            clean_pending = self.SPEECH_HEADER_RE.sub("", clean_pending).strip()
            if clean_pending:
                self._emit_public(clean_pending)
            self.pending_buffer = ""
        elif self.state == self.STATE_PUBLIC:
            if self.pending_buffer:
                cleaned = re.sub(r"<\/?(?:thought|think)\b[^>]*>", "", self.pending_buffer, flags=re.IGNORECASE)
                self._emit_public(cleaned)
                self.pending_buffer = ""

        return self.public_buffer

    def reset(self):
        self.full_raw = ""
        self.pending_buffer = ""
        self.public_buffer = ""
        self.reasoning_buffer = ""
        self.state = self.STATE_START

    def _process_buffer(self):
        while self.pending_buffer:
            if self.state == self.STATE_START:
                lstripped = self.pending_buffer.lstrip()
                if not lstripped:
                    if len(self.pending_buffer) > self.threshold:
                        self.state = self.STATE_PUBLIC
                        self._emit_public(self.pending_buffer)
                        self.pending_buffer = ""
                    break

                if lstripped.startswith("<"):
                    if ">" in lstripped:
                        tag_match = self.OPEN_TAG_RE.match(lstripped)
                        if tag_match:
                            self.state = self.STATE_IN_THOUGHT
                            tag_end = tag_match.end()
                            ws_len = len(self.pending_buffer) - len(lstripped)
                            self.pending_buffer = self.pending_buffer[ws_len + tag_end :]
                            continue
                        else:
                            self.state = self.STATE_PUBLIC
                            self._emit_public(self.pending_buffer)
                            self.pending_buffer = ""
                            break
                    else:
                        if len(lstripped) > 40:
                            self.state = self.STATE_PUBLIC
                            self._emit_public(self.pending_buffer)
                            self.pending_buffer = ""
                            break
                        break
                else:
                    self.state = self.STATE_PUBLIC
                    self._emit_public(self.pending_buffer)
                    self.pending_buffer = ""
                    break

            elif self.state == self.STATE_IN_THOUGHT:
                close_match = self.CLOSE_TAG_RE.search(self.pending_buffer)
                if close_match:
                    pre_close = self.pending_buffer[: close_match.start()]
                    if pre_close:
                        self._emit_reasoning(pre_close)
                    self.pending_buffer = self.pending_buffer[close_match.end() :]
                    self.state = self.STATE_TRANSITION
                    continue
                else:
                    last_lt = self.pending_buffer.rfind("<")
                    if last_lt != -1 and len(self.pending_buffer) - last_lt <= 12:
                        potential_tag = self.pending_buffer[last_lt:]
                        if self._is_potential_close_tag_prefix(potential_tag):
                            safe_part = self.pending_buffer[:last_lt]
                            if safe_part:
                                self._emit_reasoning(safe_part)
                                self.pending_buffer = potential_tag
                            break

                    self._emit_reasoning(self.pending_buffer)
                    self.pending_buffer = ""
                    break

            elif self.state == self.STATE_TRANSITION:
                stripped = self.pending_buffer.lstrip()
                if not stripped:
                    break

                div_match = self.DIVIDER_RE.match(stripped)
                if div_match:
                    self.pending_buffer = stripped[div_match.end() :]
                    continue

                header_match = self.SPEECH_HEADER_RE.match(stripped)
                if header_match:
                    self.pending_buffer = stripped[header_match.end() :]
                    continue

                if stripped[0] in ("*", "-", "_", "(", "["):
                    has_closing = (")" in stripped) or ("]" in stripped) or ("\n" in stripped) or (len(stripped) > 30)
                    if not has_closing:
                        break

                self.state = self.STATE_PUBLIC
                self._emit_public(self.pending_buffer.lstrip())
                self.pending_buffer = ""
                break

            elif self.state == self.STATE_PUBLIC:
                if "<" in self.pending_buffer and (
                    "thought" in self.pending_buffer.lower() or "think" in self.pending_buffer.lower()
                ):
                    cleaned = re.sub(r"<\/?(?:thought|think)\b[^>]*>", "", self.pending_buffer, flags=re.IGNORECASE)
                    self._emit_public(cleaned)
                else:
                    self._emit_public(self.pending_buffer)
                self.pending_buffer = ""
                break

    @staticmethod
    def _is_potential_close_tag_prefix(s: str) -> bool:
        s_lower = s.lower()
        if re.match(r"^<\/?(?:t(?:h(?:o(?:u(?:g(?:h(?:t)?)?)?)?)?|i(?:n(?:k)?)?)?)?$", s_lower):
            return True
        return False

    def _emit_public(self, chunk: str):
        if not chunk:
            return
        self.public_buffer += chunk
        if self.on_delta:
            try:
                self.on_delta(self.public_buffer, chunk)
            except TypeError:
                self.on_delta(chunk)

    def _emit_reasoning(self, chunk: str):
        if not chunk:
            return
        self.reasoning_buffer += chunk
        if self.on_reasoning_delta:
            try:
                self.on_reasoning_delta(self.reasoning_buffer, chunk)
            except TypeError:
                self.on_reasoning_delta(chunk)
