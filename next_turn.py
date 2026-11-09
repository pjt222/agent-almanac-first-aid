"""Next-turn notes: signals noticed after the fact, delivered with the next user message.

Two signals have no failed tool result to carry a note:
- a compaction of the conversation context (post_compaction) -> center;
- a tool returning the same successful result for the same arguments, again and again
  (core's no-progress record, mirrored in triage.py) -> heal. A successful result is never
  annotated, because core's repeat detectors hash it.

Each is queued for its session and handed to the next pre_llm_call, which appends it to the
user message. At most one note waits per session; a compaction note takes the slot over a
repeat note. No Hermes imports.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Optional

from . import event_log, kit, logbook_tool
from .triage import TriageConfig

COMPACTION = "compaction"
REPEAT = "repeat"
PRACTICE_FOR_SIGNAL = {COMPACTION: "center", REPEAT: "heal"}

# Compaction kinds that rewrite the conversation enough to re-ground after. Micro-compaction
# and tool-result pruning are routine housekeeping (opt-in, possibly every few turns), so they
# are logged but draw no note.
NOTED_COMPACTION_KINDS = frozenset({"summarize", "provider_native"})

NOTE_PREFIX = "[first aid] "
_TOOL_NAME_MAX_CHARS = 35
_MAX_SESSIONS = 64


def build_compaction_note() -> str:
    practice = PRACTICE_FOR_SIGNAL[COMPACTION]
    return (
        f"{NOTE_PREFIX}Your conversation context was compacted since the last message. The "
        f"{kit.PLUGIN_NAME} plugin suggests the {practice} practice: re-ground in the task before "
        f"continuing. Load it with skill_view(\"{kit.qualified_name(practice)}\"), then log how it went "
        f"with {logbook_tool.TOOL_NAME}."
    )


def build_repeat_note(tool_name: str, count: int) -> str:
    practice = PRACTICE_FOR_SIGNAL[REPEAT]
    shown_tool = tool_name if len(tool_name) <= _TOOL_NAME_MAX_CHARS else tool_name[:_TOOL_NAME_MAX_CHARS - 3] + "..."
    return (
        f"{NOTE_PREFIX}Last turn, {shown_tool} returned the same result {count} times for one call. "
        f"The {kit.PLUGIN_NAME} plugin suggests the {practice} practice: check for stale assumptions. "
        f"Load it with skill_view(\"{kit.qualified_name(practice)}\"), then log how it went with "
        f"{logbook_tool.TOOL_NAME}."
    )


@dataclass(frozen=True)
class _Pending:
    signal: str
    tool_name: str
    count: int
    note: str


class NextTurnNotes:
    """Per-session pending notes, under one lock; events are recorded outside it."""

    def __init__(self, config: TriageConfig, events: event_log.EventLog,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.config = config
        self._events = events
        self._clock = clock
        self._lock = threading.Lock()
        self._pending: OrderedDict[str, _Pending] = OrderedDict()
        self._last_offered: OrderedDict[str, dict[str, float]] = OrderedDict()

    # -- queueing --

    def on_compaction(self, session_id: str, kind: str) -> None:
        if not session_id:
            return
        detail = kind or "unknown"
        if detail not in NOTED_COMPACTION_KINDS:
            # For a compaction event, the log's tool_name field holds the compaction kind.
            self._record(session_id, COMPACTION, detail, 0, "suppressed:minor-compaction")
            return
        self._queue(session_id, _Pending(COMPACTION, detail, 0, build_compaction_note()))

    def on_repeat(self, session_id: str, tool_name: str, count: int) -> None:
        if not session_id:
            return
        self._queue(session_id, _Pending(REPEAT, tool_name, count, build_repeat_note(tool_name, count)))

    def _queue(self, session_id: str, pending: _Pending) -> None:
        if not self.config.notes_enabled:
            self._record(session_id, pending.signal, pending.tool_name, pending.count, "suppressed:notes-disabled")
            return
        with self._lock:
            cooling = self._cooling_down(session_id, pending.signal)
            if not cooling:
                current = self._pending.get(session_id)
                if current is None or (pending.signal == COMPACTION and current.signal != COMPACTION):
                    self._pending[session_id] = pending
                self._pending.move_to_end(session_id)
                while len(self._pending) > _MAX_SESSIONS:
                    self._pending.popitem(last=False)
        if cooling:
            self._record(session_id, pending.signal, pending.tool_name, pending.count, "suppressed:cooldown")

    # -- delivery --

    def take(self, session_id: str) -> Optional[str]:
        """The note for the next user message of this session, if one is waiting."""
        if not session_id:
            return None
        with self._lock:
            pending = self._pending.pop(session_id, None)
            if pending is None:
                return None
            offered = self._last_offered.setdefault(session_id, {})
            offered[pending.signal] = self._clock()
            self._last_offered.move_to_end(session_id)
            while len(self._last_offered) > _MAX_SESSIONS:
                self._last_offered.popitem(last=False)
        self._record(session_id, pending.signal, pending.tool_name, pending.count, "offered")
        return pending.note

    def migrate(self, old_session_id: str, new_session_id: str) -> None:
        if not old_session_id or not new_session_id or old_session_id == new_session_id:
            return
        with self._lock:
            pending = self._pending.pop(old_session_id, None)
            if pending is not None:
                self._pending.setdefault(new_session_id, pending)
            old_offered = self._last_offered.pop(old_session_id, None)
            if old_offered:
                merged = self._last_offered.setdefault(new_session_id, {})
                for signal, offered_at in old_offered.items():
                    merged[signal] = max(offered_at, merged.get(signal, offered_at))

    # -- helpers --

    def _cooling_down(self, session_id: str, signal: str) -> bool:
        last = self._last_offered.get(session_id, {}).get(signal)
        return last is not None and self._clock() - last < self.config.cooldown_seconds

    def _record(self, session_id: str, signal: str, tool_name: str, count: int, outcome: str) -> None:
        self._events.record(session_id, signal=signal, tool_name=tool_name, count=count,
                            practice=PRACTICE_FOR_SIGNAL[signal], outcome=outcome)
