import asyncio
import json
import time
from typing import Set, Any

STATUS_THROTTLE_MS = 60_000


class SseManager:
    def __init__(self):
        self._clients: Set[asyncio.Queue] = set()
        self._last_status_message: str = ""
        self._last_status_at: float = 0.0

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def add_client(self, queue: asyncio.Queue):
        self._clients.add(queue)

    def remove_client(self, queue: asyncio.Queue):
        self._clients.discard(queue)

    def broadcast(self, event: str, data: Any):
        payload_str = data if isinstance(data, str) else json.dumps(data)
        frame = f"event: {event}\ndata: {payload_str}\n\n"
        for q in list(self._clients):
            try:
                q.put_nowait(frame)
            except Exception:
                pass

    def notify_status(self, message: str, min_interval_ms: int = STATUS_THROTTLE_MS) -> bool:
        now = time.time() * 1000.0
        if message == self._last_status_message and (now - self._last_status_at) < min_interval_ms:
            return False
        self._last_status_message = message
        self._last_status_at = now
        self.broadcast("status", {"message": message})
        return True

    def close_all(self, reason: str = "server shutting down"):
        notice = f"event: server_shutdown\ndata: {json.dumps({'message': reason, 'reconnect': True})}\n\n"
        for q in list(self._clients):
            try:
                q.put_nowait(notice)
            except Exception:
                pass
        self._clients.clear()

    def reset(self):
        self.close_all()
        self._last_status_message = ""
        self._last_status_at = 0.0
