"""The event log: first aid's own in-memory record of what it noticed and offered.

Per session, a bounded list of events (time, signal, tool name, count, practice,
outcome, and for logbook entries the agent's short note). It never stores tool
arguments or result bodies. It lives in memory only,
so it resets when Hermes restarts or reloads plugins. No Hermes imports.
"""

from __future__ import annotations

import threading
from collections import OrderedDict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

MAX_SESSIONS = 64
MAX_EVENTS_PER_SESSION = 200


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Event:
    time_utc: str
    signal: str
    tool_name: str
    count: int
    practice: str
    outcome: str
    note: str = ""


class EventLog:
    """Thread-safe, bounded per-session event lists; the least recently active session is dropped first."""

    def __init__(self, max_sessions: int = MAX_SESSIONS, max_events: int = MAX_EVENTS_PER_SESSION,
                 now: Callable[[], datetime] = _utc_now) -> None:
        self._max_sessions = max_sessions
        self._max_events = max_events
        self._now = now
        self._lock = threading.Lock()
        self._sessions: OrderedDict[str, deque[Event]] = OrderedDict()

    def record(self, session_id: str, *, signal: str, tool_name: str, count: int, practice: str,
               outcome: str, note: str = "") -> Optional[Event]:
        if not session_id:
            return None
        event = Event(self._now().strftime("%Y-%m-%dT%H:%M:%SZ"), signal, tool_name, count, practice, outcome,
                      note)
        with self._lock:
            events = self._sessions.get(session_id)
            if events is None:
                events = self._sessions[session_id] = deque(maxlen=self._max_events)
                while len(self._sessions) > self._max_sessions:
                    self._sessions.popitem(last=False)
            else:
                self._sessions.move_to_end(session_id)
            events.append(event)
        return event

    def events(self, session_id: str) -> list[Event]:
        with self._lock:
            return list(self._sessions.get(session_id, ()))

    def migrate(self, old_session_id: str, new_session_id: str) -> None:
        """Move a session's events to its new id when compaction rotates the session."""
        if not old_session_id or not new_session_id or old_session_id == new_session_id:
            return
        with self._lock:
            old_events = self._sessions.pop(old_session_id, None)
            if not old_events:
                return
            merged: deque[Event] = deque(old_events, maxlen=self._max_events)
            merged.extend(self._sessions.pop(new_session_id, ()))
            self._sessions[new_session_id] = merged

    def most_recent_session(self) -> Optional[str]:
        with self._lock:
            return next(reversed(self._sessions), None)
