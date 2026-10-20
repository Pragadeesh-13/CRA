import time
from typing import Dict, Any, Optional
from app.simulation.state import SimulationState
from app.simulation.round_barrier import RoundBarrier
from app.simulation.troy import Troy, CouncilOrchestrator
from app.http.sse_manager import SseManager
from app.providers.base_driver import BaseLlmDriver

DEFAULT_SESSION_ID = "__default__"
DEFAULT_MAX_SESSIONS = 200


class SessionBundle:
    def __init__(self, sse_manager: SseManager, state: SimulationState, barrier: RoundBarrier, orchestrator: CouncilOrchestrator):
        self.sse_manager = sse_manager
        self.state = state
        self.barrier = barrier
        self.orchestrator = orchestrator
        self.last_used_at = time.time()


class SessionRegistry:
    def __init__(self, config: Any, driver: BaseLlmDriver, max_sessions: int = DEFAULT_MAX_SESSIONS):
        self.config = config
        self.driver = driver
        self.max_sessions = max_sessions
        self._sessions: Dict[str, SessionBundle] = {}

        # Initialize default shared session
        self._default = self._create_bundle()
        self._sessions[DEFAULT_SESSION_ID] = self._default

    def _create_bundle(self) -> SessionBundle:
        sse_manager = SseManager()
        state = SimulationState()
        barrier = RoundBarrier()
        orchestrator = CouncilOrchestrator(
            config=self.config,
            driver=self.driver,
            sse_manager=sse_manager,
            state=state,
            barrier=barrier,
        )
        return SessionBundle(sse_manager, state, barrier, orchestrator)

    @property
    def default_session(self) -> SessionBundle:
        return self._default

    def resolve(self, session_user: Optional[Dict[str, Any]] = None) -> SessionBundle:
        if not session_user:
            return self._default

        user_id = str(session_user.get("id") or session_user.get("uid") or session_user.get("username") or DEFAULT_SESSION_ID)
        if user_id == DEFAULT_SESSION_ID:
            return self._default

        if user_id in self._sessions:
            bundle = self._sessions[user_id]
            bundle.last_used_at = time.time()
            return bundle

        # Evict oldest if limit reached
        if len(self._sessions) >= self.max_sessions:
            oldest_key = min(
                (k for k in self._sessions if k != DEFAULT_SESSION_ID),
                key=lambda k: self._sessions[k].last_used_at,
                default=None,
            )
            if oldest_key:
                old = self._sessions.pop(oldest_key)
                old.sse_manager.close_all("Session expired")

        new_bundle = self._create_bundle()
        self._sessions[user_id] = new_bundle
        return new_bundle

    def close_all(self):
        for bundle in self._sessions.values():
            bundle.sse_manager.close_all()
        self._sessions.clear()
        self._default = self._create_bundle()
        self._sessions[DEFAULT_SESSION_ID] = self._default
