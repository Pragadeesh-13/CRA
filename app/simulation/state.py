import threading
from typing import Optional, Dict, Any, List


class SimulationState:
    def __init__(self):
        self._lock = threading.Lock()
        self._council_running: bool = False
        self._latest_story: Optional[Dict[str, Any]] = None
        self._history: List[Dict[str, Any]] = []

    @property
    def resolution_running(self) -> bool:
        with self._lock:
            return self._council_running

    @resolution_running.setter
    def resolution_running(self, val: bool):
        with self._lock:
            self._council_running = bool(val)

    def set_ticket(self, ticket: Optional[Dict[str, Any]]):
        self.set_story(ticket)

    def get_ticket(self) -> Optional[Dict[str, Any]]:
        return self.get_story()

    def acquire_lock(self) -> bool:
        with self._lock:
            if self._council_running:
                return False
            self._council_running = True
            return True

    def acquire(self) -> bool:
        return self.acquire_lock()

    def release_lock(self):
        with self._lock:
            self._council_running = False

    def release(self):
        self.release_lock()

    def is_running(self) -> bool:
        with self._lock:
            return self._council_running

    def set_latest_story(self, story: Optional[Dict[str, Any]]):
        with self._lock:
            self._latest_story = story
            if story:
                self._history.append(story)

    def set_story(self, story: Optional[Dict[str, Any]]):
        self.set_latest_story(story)

    def get_latest_story(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._latest_story

    def get_story(self) -> Optional[Dict[str, Any]]:
        return self.get_latest_story()

    def reset(self):
        with self._lock:
            self._council_running = False
            self._latest_story = None
            self._history.clear()
