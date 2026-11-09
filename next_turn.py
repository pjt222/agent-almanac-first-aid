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
from collections import OrderedDict
from dataclasses import dataclass
from typing import Optional

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
_MAX_SESSIONS = 256


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
    """Per-session pending notes, under one lock; events are recorded outside it.

    Every note that enters the queue is logged as queued, and then exactly once more: offered,
    superseded (displaced by a higher-priority note), slot-taken (refused because a note of
    equal or higher priority was waiting), cooldown (the same kind reached the agent since) or
    evicted. Several compactions before one message coalesce into one note.
    """

    def __init__(self, config: TriageConfig, events: event_log.EventLog, cooldowns: triage.Cooldowns) -> None:
        self.config = config
        self._events = events
        self._cooldowns = cooldowns
        self._lock = threading.Lock()
        self._pending: OrderedDict[str, _Pending] = OrderedDict()

    # -- queueing --

    def on_compaction(self, session_id: str, kind: str) -> None:
        if not session_id:
            return
        detail = kind or "unknown"
        if detail not in NOTED_COMPACTION_KINDS:
            # For a compaction event, the log's tool_name field holds the compaction kind.
            self._record(session_id, _Pending(COMPACTION, detail, 0, ""), "suppressed:minor-compaction")
            return
        self._queue(session_id, _Pending(COMPACTION, detail, 0, build_compaction_note()))

    def on_repeat(self, session_id: str, tool_name: str, count: int) -> None:
        if not session_id:
            return
        self._queue(session_id, _Pending(REPEAT, tool_name, count, build_repeat_note(tool_name, count)))

    def on_failure(self, session_id: str, signal: str, tool_name: str, count: int) -> None:
        """A failure streak triage decided on (once per streak, cooldown, settings) whose
        note cannot go into the tool result, because Hermes wraps that tool's results."""
        if not session_id:
            return
        self._queue(session_id, _Pending(signal, tool_name, count, build_failure_note(signal, tool_name, count)))

    def _queue(self, session_id: str, pending: _Pending) -> None:
        if not self.config.notes_enabled:
            self._record(session_id, pending, "suppressed:notes-disabled")
            return
        if self._cooldowns.active(session_id, pending.signal):
            self._record(session_id, pending, "suppressed:cooldown")
            return
        outcomes: list[tuple[_Pending, str]] = []
        with self._lock:
            current = self._pending.get(session_id)
            if current is not None and self._cooldowns.active(session_id, current.signal):
                # It would be dropped at delivery anyway; it must not keep a live note out.
                del self._pending[session_id]
                outcomes.append((current, "suppressed:cooldown"))
                current = None
            if current is not None and pending.signal == current.signal == COMPACTION:
                pass  # several compactions before one message are one event
            elif current is None or _PRIORITY[pending.signal] > _PRIORITY[current.signal]:
                self._pending[session_id] = pending
                outcomes.append((pending, triage.QUEUED_OUTCOME))
                if current is not None:
                    outcomes.append((current, "suppressed:superseded"))
            else:
                outcomes.append((pending, "suppressed:slot-taken"))
            self._pending.move_to_end(session_id)
            evicted = []
            while len(self._pending) > _MAX_SESSIONS:
                evicted.append(self._pending.popitem(last=False))
        for note, outcome in outcomes:
            self._record(session_id, note, outcome)
        for evicted_session, note in evicted:
            self._record(evicted_session, note, "suppressed:evicted")

    # -- delivery --

    def take(self, session_id: str) -> Optional[str]:
        """The note for the next user message of this session, if one is waiting. A note of a
        kind that reached the agent meanwhile (in a tool result) is dropped, not delivered."""
        if not session_id:
            return None
        with self._lock:
            pending = self._pending.pop(session_id, None)
        if pending is None:
            return None
        if not self._cooldowns.try_stamp(session_id, pending.signal):
            self._record(session_id, pending, "suppressed:cooldown")
            return None
        self._record(session_id, pending, "offered")
        return pending.note

    def migrate(self, old_session_id: str, new_session_id: str) -> None:
        """Move a waiting note to the session's new id; if one waits there too, priority decides."""
        if not old_session_id or not new_session_id or old_session_id == new_session_id:
            return
        with self._lock:
            moved = self._pending.pop(old_session_id, None)
        if moved is not None:
            self._queue_moved(new_session_id, moved)

    def _queue_moved(self, session_id: str, pending: _Pending) -> None:
        # Already logged as queued under the old id, whose events migrate with it.
        outcomes: list[tuple[_Pending, str]] = []
        with self._lock:
            current = self._pending.get(session_id)
            if current is not None and pending.signal == current.signal == COMPACTION:
                pass  # one compaction event, as in _queue
            elif current is None or _PRIORITY[pending.signal] > _PRIORITY[current.signal]:
                self._pending[session_id] = pending
                if current is not None:
                    outcomes.append((current, "suppressed:superseded"))
            else:
                outcomes.append((pending, "suppressed:slot-taken"))
        for note, outcome in outcomes:
            self._record(session_id, note, outcome)

    # -- helpers --

    def _record(self, session_id: str, note: _Pending, outcome: str) -> None:
        self._events.record(session_id, signal=note.signal, tool_name=note.tool_name, count=note.count,
                            practice=PRACTICE_FOR_SIGNAL[note.signal], outcome=outcome)
