import threading
from copy import deepcopy
from typing import Optional, Dict, Any, List


class SimulationState:
    def __init__(self):
        self._lock = threading.Lock()
        self._council_running: bool = False
        self._latest_story: Optional[Dict[str, Any]] = None
        self._latest_resolution: Optional[Dict[str, Any]] = None
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
            if story is not self._latest_story:
                self._latest_resolution = None
                if story:
                    self._history.append(story)
            self._latest_story = story

    def set_story(self, story: Optional[Dict[str, Any]]):
        self.set_latest_story(story)

    def get_latest_story(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._latest_story

    def get_story(self) -> Optional[Dict[str, Any]]:
        return self.get_latest_story()

    def complete_resolution(self, story: Dict[str, Any], resolution: Dict[str, Any]):
        with self._lock:
            if story is self._latest_story:
                self._latest_resolution = deepcopy(resolution)

    def resolution_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return deepcopy({"story": self._latest_story, "resolution": self._latest_resolution})

    def ticket_history(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(ticket) for ticket in self._history]

    def reset(self):
        with self._lock:
            self._council_running = False
            self._latest_story = None
            self._latest_resolution = None
            self._history.clear()
