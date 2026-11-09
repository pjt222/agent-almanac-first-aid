"""Triage: notice a tool-failure streak where Hermes core counts one, and offer a practice.

Hermes core already detects repeated and failing tool calls and appends its own
"[Tool loop warning ...]". This module counts the same way, so that on the call where
core's warning first appears, first aid can add one short line naming the matching
kit practice. It never blocks, never changes a successful result, and keeps the
original failed result intact (a JSON object gains one "first_aid" key; text gains one
appended line).

No Hermes imports. The classification helpers and constants below are copied from
Hermes v0.22.7 (b200f5bc60); each block names what it mirrors. They are copied, not
imported, because agent.tool_result_classification and agent.tool_guardrails are not
on the public-import list that `hermes plugins validate` checks, and a private import
draws a review warning. tests/compare_with_core.py checks the copy against core's own
functions in a Hermes checkout, call by call on random sequences; run it whenever Hermes
changes these modules.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional

from . import kit, logbook_tool

# --- Mirror of agent/tool_guardrails.py (v0.22.7) ---------------------------------------

# FAILURE_TOLERANT_TOOL_NAMES: failures of these are normal work (a red test run, an empty
# grep). Core still warns for them; first aid stays quiet, or every red test run would
# draw a note.
FAILURE_TOLERANT_TOOL_NAMES = frozenset({
    "terminal", "execute_code", "process_manage", "process", "browser_navigate", "web_extract",
})

# PROGRESS_RESET_TOOL_NAMES: a success of one of these is progress for every failing
# signature still counted this turn, and clears the same-tool counts.
PROGRESS_RESET_TOOL_NAMES = frozenset({
    "write_file", "patch", "terminal", "execute_code", "browser_click", "browser_type", "browser_press",
    "browser_navigate", "process_manage", "process", "delegate_task", "send_message", "cronjob",
    "cronjob_manage", "todo", "todo_list", "memory", "skill_manage",
})

# IDEMPOTENT_TOOL_NAMES / MUTATING_TOOL_NAMES: the tools core's no-progress detector watches
# for byte-identical successful repeats (ToolCallGuardrailController._is_idempotent).
IDEMPOTENT_TOOL_NAMES = frozenset({
    "read_file", "search_files", "web_search", "web_extract", "session_search", "skill_view", "skills_list",
    "browser_snapshot", "browser_console", "browser_get_images", "mcp_filesystem_read_file",
    "mcp_filesystem_read_text_file", "mcp_filesystem_read_multiple_files", "mcp_filesystem_list_directory",
    "mcp_filesystem_list_directory_with_sizes", "mcp_filesystem_directory_tree", "mcp_filesystem_get_file_info",
    "mcp_filesystem_search_files",
})
MUTATING_TOOL_NAMES = frozenset({
    "terminal", "execute_code", "write_file", "patch", "todo_list", "memory", "skill_manage",
    "browser_click", "browser_type", "browser_press", "browser_scroll", "browser_navigate",
    "send_message", "cronjob_manage", "delegate_task", "process_manage",
})

# ToolCallGuardrailConfig defaults: exact_failure_warn_after, same_tool_failure_warn_after.
DEFAULT_EXACT_FAILURE_THRESHOLD = 2
DEFAULT_SAME_TOOL_FAILURE_THRESHOLD = 3
# STALL_GUARD_IDENTICAL_CALL_THRESHOLD. Core already warns in the turn at the 2nd identical
# successful result (no_progress_warn_after); first aid's next-turn note waits for the 3rd.
DEFAULT_REPEAT_THRESHOLD = 3


def is_idempotent(tool_name: str) -> bool:
    """ToolCallGuardrailController._is_idempotent with the default tool sets."""
    return tool_name not in MUTATING_TOOL_NAMES and tool_name in IDEMPOTENT_TOOL_NAMES


def result_hash(result: str) -> str:
    """_result_hash for an idempotent tool: sha256 of canonical JSON, or of the raw text.
    (Core's execute_code metadata stripping never applies: execute_code is not idempotent.)"""
    parsed = _safe_json_loads(result or "")
    if parsed is None:
        text = result or ""
    else:
        text = json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()

# --- Mirror of agent/tool_result_classification.py (v0.22.7) ----------------------------

FILE_MUTATING_TOOL_NAMES = frozenset({"write_file", "patch"})
GUARDRAIL_REFUSAL_KEY = "guardrail_refusal"
_FAILURE_MARKER_RE = re.compile(
    r'"(?:error|failed)"(?!\s*:\s*(?:null|false|0|""|\[\]|\{\})\s*(?:[,}\]]|$))', re.IGNORECASE)
_FAILED_STATUSES = frozenset({"failed", "error"})
_RATE_LIMIT_RE = re.compile(r"rate[\s_-]?limit|too many requests|\b429\b", re.IGNORECASE)
_RETRY_AFTER_KEYS = ("retry_after", "retryAfter", "retry_after_seconds", "retry_after_ms")


def _safe_json_loads(text: Any) -> Any:
    """utils.safe_json_loads: parse JSON, None on any parse error."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def is_guardrail_refusal(result: Any) -> bool:
    """is_guardrail_refusal: a harness refusal of a redundant call. Not a failure."""
    data = result
    if isinstance(result, str):
        try:
            data = json.loads(result.strip())
        except Exception:
            return False
    return isinstance(data, dict) and data.get(GUARDRAIL_REFUSAL_KEY) is True


def has_failure_marker(result: str) -> bool:
    """has_failure_marker: a non-empty quoted "error"/"failed" starting in the first 500 chars,
    or a body starting with "Error"."""
    head = result[:600]
    return any(m.start() < 500 for m in _FAILURE_MARKER_RE.finditer(head)) or result.startswith("Error")


def is_rate_limited(result: Any) -> bool:
    """is_rate_limited: a FAILED result saying the backend throttled the call."""
    data = result
    if isinstance(result, str):
        try:
            data = json.loads(result.strip())
        except ValueError:
            return bool(_RATE_LIMIT_RE.search(result[:500]))
    if not isinstance(data, dict):
        return False
    if any(data.get(k) for k in _RETRY_AFTER_KEYS) or 429 in (data.get("status"), data.get("status_code"), data.get("code")):
        return True
    error = data.get("error")
    text = error.get("message") or error.get("code") if isinstance(error, dict) else error
    return isinstance(text, str) and bool(_RATE_LIMIT_RE.search(text))


def shape_failure_error(data: dict) -> Optional[str]:
    """shape_failure_error: a status of failed/error, or a results batch where every item errored."""
    if str(data.get("status", "")).lower() in _FAILED_STATUSES:
        return str(data.get("status"))
    items = data.get("results")
    if not isinstance(items, list) or not items or not all(isinstance(i, dict) for i in items):
        return None
    errors = [i.get("error") for i in items]
    if not all(errors):
        return None
    first = errors[0]
    return str(first.get("message") or first) if isinstance(first, dict) else str(first)


def file_mutation_result_landed(tool_name: str, result: Any) -> bool:
    """file_mutation_result_landed: a write_file/patch result proving the write landed."""
    if tool_name not in FILE_MUTATING_TOOL_NAMES or not isinstance(result, str):
        return False
    try:
        data = json.loads(result.strip())
    except Exception:
        return False
    if not isinstance(data, dict) or data.get("error"):
        return False
    if tool_name == "write_file":
        return "bytes_written" in data
    if tool_name == "patch":
        return data.get("success") is True
    return False


# --- Mirror of agent/tool_dispatch_helpers.py (v0.22.7) ---------------------------------

# _UNTRUSTED_TOOL_NAMES / _UNTRUSTED_TOOL_PREFIXES: results of these tools reach the model inside
# an <untrusted_tool_result> block that says not to follow tool-invocation requests found in it.
# A first-aid note there would sit inside that block and read like a prompt injection, so for
# these tools the note waits for the next user message instead (user decision 2026-11-09).
UNTRUSTED_TOOL_NAMES = frozenset({"web_extract", "web_search"})
UNTRUSTED_TOOL_PREFIXES = ("browser_", "mcp_")


def is_untrusted_tool(tool_name: str) -> bool:
    """_is_untrusted_tool: Hermes wraps this tool's results as untrusted external data."""
    return bool(tool_name) and (tool_name in UNTRUSTED_TOOL_NAMES or tool_name.startswith(UNTRUSTED_TOOL_PREFIXES))


# --- Mirror of agent/display.py _detect_tool_failure (v0.22.7) --------------------------

def detect_failure(tool_name: str, result: Any) -> bool:
    """The verdict of agent.display._detect_tool_failure, without its display suffix.

    This is the `failed` the executor hands core's guardrail, so counting on it keeps
    first aid's streaks in step with core's. (Core's memory-store-full branch is left
    out: it returns True exactly when the generic dict branch below does.)
    """
    if result is None or file_mutation_result_landed(tool_name, result):
        return False
    data = result if isinstance(result, dict) else _safe_json_loads(result)
    if is_guardrail_refusal(data):
        return False
    if isinstance(data, dict) and data.get("user_summary"):
        return True
    if tool_name == "terminal":
        exit_code = data.get("exit_code") if isinstance(data, dict) else None
        return not (exit_code is None or exit_code == 0)
    if isinstance(data, dict):
        failed = data.get("success") is False
        err = data.get("error") or data.get("message")
        if err and (failed or "error" in data):
            return True
        return bool(shape_failure_error(data))
    return isinstance(result, str) and has_failure_marker(result)


def args_hash(args: Any) -> str:
    """ToolCallSignature.from_call's args hash: sha256 of sorted compact JSON of the args."""
    if not isinstance(args, Mapping):
        args = {}
    canonical = json.dumps(args, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8", "surrogatepass")).hexdigest()


# --- First aid's own layer --------------------------------------------------------------

EXACT_FAILURE = "exact_failure"
SAME_TOOL_FAILURE = "same_tool_failure"

# One practice per signal, and only practices with an observable trigger.
PRACTICE_FOR_SIGNAL = {
    EXACT_FAILURE: "breathe",
    SAME_TOOL_FAILURE: "redirect",
}

NOTE_KEY = "first_aid"
TEXT_NOTE_PREFIX = "\n\n[first aid] "
NOTE_MAX_CHARS = 300

# The logbook tool's own failures are never annotated with a note about themselves.
OWN_TOOL_NAMES: frozenset[str] = frozenset({logbook_tool.TOOL_NAME})

_MAX_TURNS = 512  # shared by all sessions; a busy gateway must not evict a live turn
_MAX_SESSIONS = 256
_MAX_COOLDOWN_SESSIONS = 4096  # a few floats each; a forgotten stamp would let a note repeat early

Signature = tuple[str, str]


def _shorten_middle(name: str, length: int) -> str:
    """Cut name to length with "..." in the middle, keeping both ends (an MCP tool's server
    prefix and its action suffix are what identify it)."""
    if len(name) <= length:
        return name
    keep = max(length - 3, 2)
    head = (keep + 1) // 2
    return name[:head] + "..." + name[len(name) - (keep - head):]


def fit_note(render: Callable[[str], str], tool_name: str, limit: int = NOTE_MAX_CHARS) -> str:
    """Render a note naming tool_name, shortening the name until the note fits within limit."""
    note = render(tool_name)
    if len(note) <= limit:
        return note
    target = len(tool_name) - (len(note) - limit)
    if target >= 11:
        note = render(_shorten_middle(tool_name, target))
        if len(note) <= limit:
            return note
    note = render(_shorten_middle(tool_name, 11))
    return note if len(note) <= limit else note[:limit - 3] + "..."


def build_note(signal: str, tool_name: str, count: int) -> str:
    """The one line first aid adds: who says it, what was observed, which practice, how to load
    it, and where to log the outcome (user decision 2026-11-09: without that, the live test's
    agent changed approach but never logged)."""
    practice = PRACTICE_FOR_SIGNAL[signal]
    if signal == EXACT_FAILURE:
        observed = "{tool} failed " + f"{count} times with the same arguments."
        advice = "pause once and re-read the error before the next step"
    else:
        observed = "{tool} failed " + f"{count} times this turn."
        advice = "reframe the approach instead of varying the call"

    def render(shown_tool: str) -> str:
        return (
            f"{observed.format(tool=shown_tool)} The {kit.PLUGIN_NAME} plugin suggests the {practice} "
            f"practice: {advice}. Load it with skill_view(\"{kit.qualified_name(practice)}\"), then log "
            f"how it went with {logbook_tool.TOOL_NAME}."
        )

    return fit_note(render, tool_name)


def annotate(tool_name: str, result: str, note: str) -> Optional[str]:
    """Return the failed result with the note added, or None when it cannot be added safely.

    A JSON object gains a "first_aid" key; plain text gains one appended line; other JSON
    (a list, a scalar) is left alone. The self-check makes sure core still reads the
    annotated result exactly as it read the original: still failed, and rate-limited
    if and only if the original was.
    """
    data = _safe_json_loads(result)
    if isinstance(data, dict):
        if NOTE_KEY in data:
            return None
        data[NOTE_KEY] = note
        transformed = json.dumps(data, ensure_ascii=False)
    elif data is None:
        transformed = result + TEXT_NOTE_PREFIX + note
    else:
        return None
    if not detect_failure(tool_name, transformed):
        return None
    if is_rate_limited(transformed) != is_rate_limited(result):
        return None
    return transformed


@dataclass(frozen=True)
class TriageConfig:
    notes_enabled: bool = True
    exact_failure_threshold: int = DEFAULT_EXACT_FAILURE_THRESHOLD
    same_tool_failure_threshold: int = DEFAULT_SAME_TOOL_FAILURE_THRESHOLD
    cooldown_seconds: int = 300
    repeat_threshold: int = DEFAULT_REPEAT_THRESHOLD

    @classmethod
    def from_lookup(cls, lookup: Callable[[str, Any], Any]) -> "TriageConfig":
        """Build from a get_config-style lookup. Hermes does not apply the schema defaults,
        so each default is repeated here; a value of the wrong type or below 1 falls back."""
        defaults = cls()

        def positive_int(key: str, default: int) -> int:
            value = lookup(key, default)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                return default
            return value

        notes_enabled = lookup("notes_enabled", defaults.notes_enabled)
        return cls(
            notes_enabled=notes_enabled if isinstance(notes_enabled, bool) else defaults.notes_enabled,
            exact_failure_threshold=positive_int("exact_failure_threshold", defaults.exact_failure_threshold),
            same_tool_failure_threshold=positive_int("same_tool_failure_threshold", defaults.same_tool_failure_threshold),
            cooldown_seconds=positive_int("cooldown_seconds", defaults.cooldown_seconds),
            repeat_threshold=positive_int("repeat_threshold", defaults.repeat_threshold),
        )


@dataclass(frozen=True)
class Crossing:
    """A threshold crossing first aid decided on: the note was offered, or why it was not."""

    signal: str
    tool_name: str
    count: int
    practice: str
    outcome: str  # "offered", "queued:next-message" or "suppressed:<reason>"
    transformed_result: Optional[str] = None


@dataclass
class _TurnState:
    """Core's per-turn counters (after_call), plus which streaks first aid has decided on."""

    exact: dict[Signature, int] = field(default_factory=dict)
    same: dict[str, int] = field(default_factory=dict)
    progress: set[Signature] = field(default_factory=set)
    decided_exact: set[Signature] = field(default_factory=set)
    decided_same: set[str] = field(default_factory=set)
    noted_tools: set[str] = field(default_factory=set)
    # Core's no-progress record: signature -> (result hash, repeat count); not strictly
    # consecutive, as in core.
    no_progress: dict[Signature, tuple[str, int]] = field(default_factory=dict)
    repeat_reported: set[Signature] = field(default_factory=set)


RepeatCallback = Callable[[str, str, int], None]  # (session_id, tool_name, count)
QUEUED_OUTCOME = "queued:next-message"


class Cooldowns:
    """When each kind of note last reached the agent, per session, shared by the in-result and
    the next-message channel. A note counts only once it is delivered (user decision
    2026-11-09), so a waiting note that is displaced never holds the cooldown. Its own lock is
    always taken last and calls nothing, so it cannot deadlock with the callers' locks."""

    def __init__(self, cooldown_seconds: int, clock: Callable[[], float] = time.monotonic) -> None:
        self._seconds = cooldown_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._last: OrderedDict[str, dict[str, float]] = OrderedDict()

    def active(self, session_id: str, signal: str) -> bool:
        with self._lock:
            last = self._last.get(session_id, {}).get(signal)
            return last is not None and self._clock() - last < self._seconds

    def stamp(self, session_id: str, signal: str) -> None:
        with self._lock:
            self._stamp_locked(session_id, signal)

    def try_stamp(self, session_id: str, signal: str) -> bool:
        """Stamp and return True unless that kind is cooling down; one step, so two
        threads cannot both deliver a note of one kind."""
        with self._lock:
            last = self._last.get(session_id, {}).get(signal)
            if last is not None and self._clock() - last < self._seconds:
                return False
            self._stamp_locked(session_id, signal)
            return True

    def _stamp_locked(self, session_id: str, signal: str) -> None:
        self._last.setdefault(session_id, {})[signal] = self._clock()
        self._last.move_to_end(session_id)
        while len(self._last) > _MAX_COOLDOWN_SESSIONS:
            self._last.popitem(last=False)

    def migrate(self, old_session_id: str, new_session_id: str) -> None:
        if not old_session_id or not new_session_id or old_session_id == new_session_id:
            return
        with self._lock:
            old = self._last.pop(old_session_id, None)
            if old:
                merged = self._last.setdefault(new_session_id, {})
                for signal, stamped_at in old.items():
                    merged[signal] = max(stamped_at, merged.get(signal, stamped_at))


class Triage:
    """Counts failures per (session_id, turn_id) the way core does, and decides on notes.

    Hermes runs every hook callback on its own worker thread, so all state changes and
    the crossing decision happen under one lock.
    """

    def __init__(self, config: TriageConfig = TriageConfig(), clock: Callable[[], float] = time.monotonic,
                 on_repeat: Optional[RepeatCallback] = None, cooldowns: Optional[Cooldowns] = None) -> None:
        self.config = config
        self._on_repeat = on_repeat
        self.cooldowns = cooldowns if cooldowns is not None else Cooldowns(config.cooldown_seconds, clock)
        self._lock = threading.Lock()
        self._turns: OrderedDict[tuple[str, str], _TurnState] = OrderedDict()

    def observe(self, *, tool_name: str, args: Any, result: Any, session_id: str, turn_id: str,
                tool_call_id: str = "") -> Optional[Crossing]:
        """Count one tool result. Returns a Crossing on the first warning of a failure streak,
        else None; a QUEUED_OUTCOME crossing is for the caller to hand to the next-message
        channel. A run of identical successful results reaching repeat_threshold is reported
        to on_repeat instead, once per signature per turn, after the lock is released."""
        if not session_id or not turn_id or "#" in (tool_call_id or ""):
            # No key to count under (execute_code RPC, MCP transport), or one entry of a
            # connector batch, which core counts as a single call.
            return None
        if tool_name in OWN_TOOL_NAMES:
            return None
        failed = detect_failure(tool_name, result)
        signature = (tool_name, args_hash(args))
        throttled = failed and is_rate_limited(result)
        landed = not failed and file_mutation_result_landed(tool_name, result)

        crossing: Optional[Crossing] = None
        repeat_count = 0
        with self._lock:
            turn = self._turn_state(session_id, turn_id)
            if failed:
                crossing = self._count_failure(turn, tool_name, signature, throttled, result, session_id)
            else:
                repeat_count = self._count_success(turn, tool_name, signature, landed, result)
        # The callback runs after the lock is released, so the two modules' locks never nest.
        if repeat_count and self._on_repeat is not None:
            self._on_repeat(session_id, tool_name, repeat_count)
        return crossing

    def migrate_session(self, old_session_id: str, new_session_id: str) -> None:
        """Carry counts over when compaction rotates the session id (compression.in_place:
        false). Core's counter lives on the agent and keeps counting. The shared Cooldowns is
        migrated by its owner."""
        if not old_session_id or not new_session_id or old_session_id == new_session_id:
            return
        with self._lock:
            for key in [key for key in self._turns if key[0] == old_session_id]:
                state = self._turns.pop(key)
                self._turns.setdefault((new_session_id, key[1]), state)

    # -- counting (mirrors ToolCallGuardrailController.after_call) --

    def _turn_state(self, session_id: str, turn_id: str) -> _TurnState:
        key = (session_id, turn_id)
        turn = self._turns.get(key)
        if turn is None:
            turn = self._turns[key] = _TurnState()
            while len(self._turns) > _MAX_TURNS:
                self._turns.popitem(last=False)
        else:
            self._turns.move_to_end(key)
        return turn

    def _count_success(self, turn: _TurnState, tool_name: str, signature: Signature, landed: bool,
                       result: Any) -> int:
        """Core's success branch. Returns the repeat count to report, or 0."""
        turn.exact.pop(signature, None)
        turn.decided_exact.discard(signature)
        turn.same.pop(tool_name, None)
        turn.decided_same.discard(tool_name)
        turn.noted_tools.discard(tool_name)
        if tool_name in PROGRESS_RESET_TOOL_NAMES or landed:
            turn.progress.update(turn.exact)
            turn.same.clear()
            turn.decided_same.clear()
            turn.noted_tools.clear()
        if not is_idempotent(tool_name) or not isinstance(result, str):
            turn.no_progress.pop(signature, None)
            return 0
        digest = result_hash(result)
        previous = turn.no_progress.get(signature)
        repeat_count = previous[1] + 1 if previous is not None and previous[0] == digest else 1
        turn.no_progress[signature] = (digest, repeat_count)
        if repeat_count >= self.config.repeat_threshold and signature not in turn.repeat_reported:
            turn.repeat_reported.add(signature)
            return repeat_count
        return 0

    def _count_failure(self, turn: _TurnState, tool_name: str, signature: Signature, throttled: bool,
                       result: Any, session_id: str) -> Optional[Crossing]:
        if signature in turn.progress:
            # A mutation landed since this call last failed: the retry starts a new streak.
            turn.progress.discard(signature)
            turn.exact.pop(signature, None)
            turn.decided_exact.discard(signature)
        if not throttled:
            turn.exact[signature] = turn.exact.get(signature, 0) + 1
        exact_count = turn.exact.get(signature, 0)
        same_count = turn.same[tool_name] = turn.same.get(tool_name, 0) + 1
        turn.no_progress.pop(signature, None)

        # Core's decision: exact-args warning first, then same-tool warning.
        if not throttled and exact_count >= self.config.exact_failure_threshold:
            signal, count, decided, key = EXACT_FAILURE, exact_count, turn.decided_exact, signature
        elif same_count >= self.config.same_tool_failure_threshold:
            signal, count, decided, key = SAME_TOOL_FAILURE, same_count, turn.decided_same, tool_name
        else:
            return None
        if tool_name in FAILURE_TOLERANT_TOOL_NAMES or key in decided:
            return None
        decided.add(key)
        if tool_name in turn.noted_tools:
            return None  # one note per tool per streak
        return self._decide(turn, signal, tool_name, count, throttled, result, session_id)

    # -- the note --

    def _decide(self, turn: _TurnState, signal: str, tool_name: str, count: int, throttled: bool,
                result: Any, session_id: str) -> Crossing:
        practice = PRACTICE_FOR_SIGNAL[signal]

        def crossing(outcome: str, transformed: Optional[str] = None) -> Crossing:
            return Crossing(signal, tool_name, count, practice, outcome, transformed)

        if throttled:
            # A throttled retry is the right move, and an annotated result would restart
            # core's identical-call count on a run its exact-args block does not bound.
            return crossing("suppressed:rate-limited")
        if not self.config.notes_enabled:
            return crossing("suppressed:notes-disabled")
        if self.cooldowns.active(session_id, signal):
            return crossing("suppressed:cooldown")
        if is_untrusted_tool(tool_name):
            # Not inside the untrusted-data wrapper: the note waits for the next user message.
            # The cooldown is stamped only if and when it is delivered.
            turn.noted_tools.add(tool_name)
            return crossing(QUEUED_OUTCOME)
        if not isinstance(result, str):
            return crossing("suppressed:unsupported-result")
        transformed = annotate(tool_name, result, build_note(signal, tool_name, count))
        if transformed is None:
            return crossing("suppressed:unsupported-result")
        if not self.cooldowns.try_stamp(session_id, signal):
            return crossing("suppressed:cooldown")  # the other channel delivered one meanwhile
        turn.noted_tools.add(tool_name)
        return crossing("offered", transformed)
