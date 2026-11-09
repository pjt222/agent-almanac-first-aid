"""Next-turn notes: signals whose note cannot ride on a tool result, delivered with the next
user message.

- a compaction of the conversation context (post_compaction) -> center;
- a tool returning the same successful result for the same arguments, again and again
  (core's no-progress record, mirrored in triage.py) -> heal. A successful result is never
  annotated, because core's repeat detectors hash it;
- a failure streak of a tool whose results Hermes wraps as untrusted external data
  (triage.is_untrusted_tool) -> breathe or redirect, as for other tools. Inside that wrapper a
  note would read like a prompt injection.

Each is queued for its session and handed to the next pre_llm_call, which appends it to the
user message. At most one note waits per session; by priority compaction, then failure, then
repeat, and a displaced note is logged as superseded. No Hermes imports.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Optional

from . import event_log, kit, logbook_tool
from . import triage
from .triage import TriageConfig, fit_note

COMPACTION = "compaction"
REPEAT = "repeat"
PRACTICE_FOR_SIGNAL = {COMPACTION: "center", REPEAT: "heal", **triage.PRACTICE_FOR_SIGNAL}
# Which note keeps the single waiting slot.
_PRIORITY = {COMPACTION: 3, triage.EXACT_FAILURE: 2, triage.SAME_TOOL_FAILURE: 2, REPEAT: 1}

# Compaction kinds that rewrite the conversation enough to re-ground after. Micro-compaction
# and tool-result pruning are routine housekeeping (opt-in, possibly every few turns), so they
# are logged but draw no note.
NOTED_COMPACTION_KINDS = frozenset({"summarize", "provider_native"})

# The note is appended to the user's message, so it says up front that the user did not write
# it, as core labels its own injected notes (PR #2 review; user decision 2026-11-09).
NOTE_PREFIX = f"[first aid, from the {kit.PLUGIN_NAME} plugin, not the user] "
_MAX_SESSIONS = 64


def _load_and_log(practice: str) -> str:
    return (f"Load it with skill_view(\"{kit.qualified_name(practice)}\"), then log how it went with "
            f"{logbook_tool.TOOL_NAME}.")


def build_compaction_note() -> str:
    practice = PRACTICE_FOR_SIGNAL[COMPACTION]
    return (
        f"{NOTE_PREFIX}Your context was compacted since the last message. Try {practice}: re-ground "
        f"in the task first. {_load_and_log(practice)}"
    )


def build_repeat_note(tool_name: str, count: int) -> str:
    # "Earlier", not "last turn": a background-review run shares the session id, so the repeat
    # may not have been the main agent's own.
    practice = PRACTICE_FOR_SIGNAL[REPEAT]

    def render(shown_tool: str) -> str:
        return (
            f"{NOTE_PREFIX}Earlier, {shown_tool} gave the same result {count} times for one call. "
            f"Try {practice}: check for stale assumptions. {_load_and_log(practice)}"
        )

    return fit_note(render, tool_name)


def build_failure_note(signal: str, tool_name: str, count: int) -> str:
    practice = PRACTICE_FOR_SIGNAL[signal]
    if signal == triage.EXACT_FAILURE:
        observed = f"failed {count} times with the same arguments"
        advice = "pause and re-read the error"
    else:
        observed = f"failed {count} times in one turn"
        advice = "reframe the approach"

    def render(shown_tool: str) -> str:
        return (
            f"{NOTE_PREFIX}Earlier, {shown_tool} {observed}. Try {practice}: {advice}. "
            f"{_load_and_log(practice)}"
        )

    return fit_note(render, tool_name)


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

    def on_failure(self, session_id: str, signal: str, tool_name: str, count: int) -> None:
        """A failure streak triage already decided on (once per streak, cooldown, settings)
        whose note cannot go into the tool result."""
        if not session_id:
            return
        self._queue(session_id, _Pending(signal, tool_name, count, build_failure_note(signal, tool_name, count)),
                    already_decided=True)

    def _queue(self, session_id: str, pending: _Pending, already_decided: bool = False) -> None:
        if not already_decided and not self.config.notes_enabled:
            self._record(session_id, pending.signal, pending.tool_name, pending.count, "suppressed:notes-disabled")
            return
        displaced: Optional[_Pending] = None
        with self._lock:
            cooling = not already_decided and self._cooling_down(session_id, pending.signal)
            if not cooling:
                current = self._pending.get(session_id)
                if current is None or _PRIORITY[pending.signal] > _PRIORITY[current.signal]:
                    self._pending[session_id] = pending
                    displaced = current
                elif not (pending.signal == current.signal == COMPACTION):
                    displaced = pending  # several compactions are one event and just coalesce
                self._pending.move_to_end(session_id)
                while len(self._pending) > _MAX_SESSIONS:
                    self._pending.popitem(last=False)
        if cooling:
            self._record(session_id, pending.signal, pending.tool_name, pending.count, "suppressed:cooldown")
        if displaced is not None:
            self._record(session_id, displaced.signal, displaced.tool_name, displaced.count, "suppressed:superseded")

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
