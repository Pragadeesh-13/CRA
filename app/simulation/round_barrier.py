import asyncio
import math
from typing import Optional, Dict, List, Set, Callable, Any


def normalize_round_number(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (dict, list)):
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        parsed = float(value)
        if not math.isfinite(parsed) or parsed < 0:
            return None
        return int(parsed)
    except (ValueError, TypeError):
        return None


class RoundBarrier:
    def __init__(self):
        self.pending_round_begins: Set[int] = set()
        self.round_begin_waiters: Dict[int, List[asyncio.Event]] = {}

    def normalize_round_number(self, value: Any) -> Optional[int]:
        return normalize_round_number(value)

    async def wait_for_round_begin(
        self,
        round_id_raw: Any,
        on_waiting: Optional[Callable[[int], None]] = None,
    ):
        round_id = normalize_round_number(round_id_raw)
        if round_id is None:
            return

        if round_id in self.pending_round_begins:
            self.pending_round_begins.remove(round_id)
            return

        if on_waiting:
            try:
                on_waiting(round_id)
            except Exception:
                pass

        event = asyncio.Event()
        if round_id not in self.round_begin_waiters:
            self.round_begin_waiters[round_id] = []
        self.round_begin_waiters[round_id].append(event)

        await event.wait()

    def mark_round_begun(self, round_id_raw: Any) -> Dict[str, Any]:
        round_id = normalize_round_number(round_id_raw)
        if round_id is None:
            return {"ok": False, "round": None}

        waiters = self.round_begin_waiters.pop(round_id, None)
        if waiters:
            for ev in waiters:
                ev.set()
        else:
            self.pending_round_begins.add(round_id)

        return {"ok": True, "round": round_id}

    def reset(self):
        self.pending_round_begins.clear()
        for waiters in self.round_begin_waiters.values():
            for ev in waiters:
                ev.set()
        self.round_begin_waiters.clear()
