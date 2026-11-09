"""The logbook tool: the agent records which practice it applied and how it went.

Entries go into the same in-memory event log as the triage notes, so /first-aid shows
the whole sequence: what first aid noticed, what it offered, what the agent did about it.
No Hermes imports.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from . import event_log, kit

TOOL_NAME = "first_aid_log"
TOOLSET = "first_aid"
LOGBOOK_SIGNAL = "logbook"
OUTCOMES = ("helped", "partly", "no_change")
NOTE_MAX_CHARS = 160

SCHEMA: dict[str, Any] = {
    "name": TOOL_NAME,
    "description": (
        "Record a first-aid practice you applied in this session and how it went, for example after "
        "a [first aid] note on a failed tool result. The entry appears in the user's /first-aid report."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "practice": {
                "type": "string",
                "enum": list(kit.KIT_NAMES),
                "description": "The kit practice you applied.",
            },
            "outcome": {
                "type": "string",
                "enum": list(OUTCOMES),
                "description": "What changed after applying it.",
            },
            "note": {
                "type": "string",
                "description": "Optional: one short sentence on what you changed. No file contents or tool output.",
            },
        },
        "required": ["practice", "outcome"],
        "additionalProperties": False,
    },
}


def _error(message: str) -> str:
    return json.dumps({"error": message})


def make_handler(events: event_log.EventLog) -> Callable[..., str]:
    """Build the tool handler bound to one event log."""

    def handle_first_aid_log(args: Any, session_id: Any = None, **_ignored: Any) -> str:
        # Only the declared arguments are read; other dispatch kwargs (user_task, ...) are never stored.
        if not isinstance(args, dict):
            return _error("first_aid_log expects an object with 'practice' and 'outcome'.")
        practice = args.get("practice")
        outcome = args.get("outcome")
        if practice not in kit.KIT_NAMES:
            return _error(f"Unknown practice {practice!r}. Use one of: {', '.join(kit.KIT_NAMES)}.")
        if outcome not in OUTCOMES:
            return _error(f"Unknown outcome {outcome!r}. Use one of: {', '.join(OUTCOMES)}.")
        note = args.get("note") or ""
        if not isinstance(note, str):
            return _error("'note' must be a string.")
        note = " ".join(note.split())
        if len(note) > NOTE_MAX_CHARS:
            note = note[:NOTE_MAX_CHARS - 3] + "..."
        if not session_id:
            return json.dumps({"success": True, "stored": False,
                               "reason": "No session id reached the tool, so the entry was not stored."})
        events.record(str(session_id), signal=LOGBOOK_SIGNAL, tool_name="", count=0, practice=practice,
                      outcome=outcome, note=note)
        return json.dumps({"success": True, "stored": True, "practice": practice, "outcome": outcome})

    return handle_first_aid_log
