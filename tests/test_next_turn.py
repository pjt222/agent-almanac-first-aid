"""Compaction and repeated identical results queue one note for the next user message."""

from __future__ import annotations

import json

import pytest

SAME_CONTENT = json.dumps({"content": "1|# Monday", "total_lines": 1})
OTHER_CONTENT = json.dumps({"content": "1|# Tuesday", "total_lines": 1})
NOT_FOUND = json.dumps({"error": "File not found: a.md"})


@pytest.fixture()
def hooks(registered_context):
    return {name: callbacks[0] for name, callbacks in registered_context.hooks.items()}


@pytest.fixture()
def report(registered_context):
    handler = registered_context.commands["first-aid"]["handler"]
    return lambda session_id="s-1": handler("status", session_id=session_id)


def tool(hooks, result, tool_name="read_file", args=None, session_id="s-1", turn_id="t-1"):
    return hooks["transform_tool_result"](
        tool_name=tool_name, args=args if args is not None else {"path": "a.md"}, result=result,
        session_id=session_id, turn_id=turn_id, tool_call_id="c", task_id="", api_request_id="",
        duration_ms=1, status="ok", error_type="", error_message="")


def next_message(hooks, session_id="s-1", platform="cli"):
    return hooks["pre_llm_call"](session_id=session_id, task_id="", turn_id="t-2", user_message="next",
                                 conversation_history=[], is_first_turn=False, model="m", platform=platform,
                                 parent_session_id="", sender_id="")


def compaction(hooks, kind="summarize", session_id="s-1", old_session_id=""):
    return hooks["post_compaction"](session_id=session_id, old_session_id=old_session_id, kind=kind,
                                    reason="turn_start_threshold", before_tokens=100, after_tokens=10,
                                    messages_removed=5)


# --- repeated identical successful results -> heal ------------------------------------

def test_third_identical_result_queues_one_heal_note(hooks, report):
    for _ in range(2):
        assert tool(hooks, SAME_CONTENT) is None
    assert next_message(hooks) is None
    for _ in range(3):
        assert tool(hooks, SAME_CONTENT) is None  # successful results are never changed
    reply = next_message(hooks)
    assert set(reply) == {"context"}
    assert reply["context"].startswith(
        "[first aid, from the agent-almanac-first-aid plugin, not the user] Earlier, read_file gave the same result")
    assert "agent-almanac-first-aid:heal" in reply["context"]
    assert next_message(hooks) is None
    assert "read_file returned the same result 3x -> heal (offered)" in report()


def test_a_changed_result_restarts_the_repeat_count(hooks):
    tool(hooks, SAME_CONTENT)
    tool(hooks, SAME_CONTENT)
    tool(hooks, OTHER_CONTENT)
    tool(hooks, OTHER_CONTENT)
    assert next_message(hooks) is None


def test_other_calls_in_between_do_not_reset_the_repeat_count(hooks):
    # Core's no-progress record is per signature, not strictly consecutive.
    for _ in range(3):
        tool(hooks, SAME_CONTENT)
        tool(hooks, OTHER_CONTENT, args={"path": "b.md"})
    assert next_message(hooks) is not None


def test_a_failure_clears_the_repeat_record(hooks):
    tool(hooks, SAME_CONTENT)
    tool(hooks, SAME_CONTENT)
    tool(hooks, NOT_FOUND)
    tool(hooks, SAME_CONTENT)
    assert next_message(hooks) is None


def test_non_idempotent_tools_draw_no_repeat_note(hooks):
    for _ in range(4):
        tool(hooks, json.dumps({"exit_code": 0, "output": "same"}), tool_name="terminal", args={"command": "ls"})
    assert next_message(hooks) is None


def test_repeat_note_comes_once_per_call_per_turn(hooks):
    for _ in range(6):
        tool(hooks, SAME_CONTENT)
    assert next_message(hooks) is not None
    assert next_message(hooks) is None


# --- compaction -> center -------------------------------------------------------------

@pytest.mark.parametrize("kind", ["summarize", "provider_native"])
def test_compaction_queues_one_center_note(hooks, report, kind):
    assert compaction(hooks, kind) is None
    reply = next_message(hooks)
    assert "agent-almanac-first-aid:center" in reply["context"]
    assert next_message(hooks) is None
    assert f"context compacted ({kind}) -> center (offered)" in report()


@pytest.mark.parametrize("kind", ["micro_summarize", "prune_tool_results", "other"])
def test_routine_compaction_is_logged_not_noted(hooks, report, kind):
    compaction(hooks, kind)
    assert next_message(hooks) is None
    assert f"context compacted ({kind}) -> center (not offered: minor-compaction)" in report()


def test_several_compactions_coalesce_into_one_note(hooks):
    for _ in range(3):
        compaction(hooks)
    assert next_message(hooks) is not None
    assert next_message(hooks) is None


def test_a_compaction_note_takes_the_slot_over_a_repeat_note(hooks):
    for _ in range(3):
        tool(hooks, SAME_CONTENT)
    compaction(hooks)
    assert "center" in next_message(hooks)["context"]
    compaction(hooks, session_id="s-2")
    for _ in range(3):
        tool(hooks, SAME_CONTENT, session_id="s-2")
    assert "center" in next_message(hooks, "s-2")["context"]


def test_cooldown_applies_to_next_message_notes(hooks, report):
    compaction(hooks)
    assert next_message(hooks) is not None
    compaction(hooks)
    assert next_message(hooks) is None
    assert "(not offered: cooldown)" in report()


def test_notes_are_per_session(hooks):
    compaction(hooks, session_id="s-1")
    assert next_message(hooks, "s-2") is None
    assert next_message(hooks, "s-1") is not None
    assert next_message(hooks, "") is None


def test_a_rotated_session_keeps_its_note_counts_and_log(hooks, report):
    tool(hooks, NOT_FOUND, session_id="old")
    for _ in range(3):
        tool(hooks, SAME_CONTENT, args={"path": "b.md"}, session_id="old")
    compaction(hooks, session_id="new", old_session_id="old")
    # The failure count carried over: the next identical failure under the new id crosses.
    annotated = tool(hooks, NOT_FOUND, session_id="new")
    assert annotated is not None and '"first_aid"' in annotated
    assert "center" in next_message(hooks, "new")["context"]
    assert next_message(hooks, "old") is None
    assert "read_file failed 2x with the same arguments" in report("new")


def test_notes_disabled_queue_nothing(plugin_package, recording_context_class):
    context = recording_context_class(settings={"notes_enabled": False})
    plugin_package.register(context)
    hooks = {name: callbacks[0] for name, callbacks in context.hooks.items()}
    compaction(hooks)
    assert next_message(hooks) is None
    reply = context.commands["first-aid"]["handler"]("", session_id="s-1")
    assert "(not offered: notes-disabled)" in reply


def test_repeat_threshold_is_configurable(plugin_package, recording_context_class):
    context = recording_context_class(settings={"repeat_threshold": 2})
    plugin_package.register(context)
    hooks = {name: callbacks[0] for name, callbacks in context.hooks.items()}
    tool(hooks, SAME_CONTENT)
    tool(hooks, SAME_CONTENT)
    assert next_message(hooks) is not None


# --- failures of tools Hermes wraps as untrusted -> breathe / redirect, next message -------

def test_untrusted_tool_failure_note_waits_for_the_next_message(hooks, report):
    assert tool(hooks, NOT_FOUND, tool_name="mcp__notes__read") is None
    assert tool(hooks, NOT_FOUND, tool_name="mcp__notes__read") is None  # result unchanged
    reply = next_message(hooks)
    assert "Earlier, mcp__notes__read failed 2 times with the same arguments." in reply["context"]
    assert "agent-almanac-first-aid:breathe" in reply["context"]
    assert next_message(hooks) is None
    lines = report()
    assert "mcp__notes__read failed 2x with the same arguments -> breathe (queued for the next message)" in lines
    assert "mcp__notes__read failed 2x with the same arguments -> breathe (offered)" in lines


def test_the_waiting_slot_goes_to_compaction_then_failure_then_repeat(hooks, report):
    for _ in range(3):
        tool(hooks, SAME_CONTENT)
    tool(hooks, NOT_FOUND, tool_name="web_search", args={"q": "x"})
    tool(hooks, NOT_FOUND, tool_name="web_search", args={"q": "x"})  # failure displaces the repeat
    compaction(hooks)  # compaction displaces the failure
    assert "center" in next_message(hooks)["context"]
    lines = report()
    assert "read_file returned the same result 3x -> heal (not offered: superseded)" in lines
    assert "web_search failed 2x with the same arguments -> breathe (not offered: superseded)" in lines


def test_a_waiting_note_holds_no_cooldown_and_never_doubles_a_delivered_one(hooks, report):
    tool(hooks, NOT_FOUND, tool_name="mcp__notes__read")
    tool(hooks, NOT_FOUND, tool_name="mcp__notes__read")  # breathe queued, not yet seen
    tool(hooks, NOT_FOUND, turn_id="t-2")
    assert tool(hooks, NOT_FOUND, turn_id="t-2") is not None  # breathe reaches the agent now
    assert next_message(hooks) is None  # the waiting breathe would be a second one within 300 s
    lines = report()
    assert "mcp__notes__read failed 2x with the same arguments -> breathe (queued for the next message)" in lines
    assert "read_file failed 2x with the same arguments -> breathe (offered)" in lines
    assert "mcp__notes__read failed 2x with the same arguments -> breathe (not offered: cooldown)" in lines


def test_a_displaced_note_holds_no_cooldown(hooks, report):
    tool(hooks, NOT_FOUND, tool_name="mcp__notes__read")
    tool(hooks, NOT_FOUND, tool_name="mcp__notes__read")
    compaction(hooks)  # displaces the waiting breathe
    assert "center" in next_message(hooks)["context"]
    tool(hooks, NOT_FOUND, turn_id="t-2")
    assert tool(hooks, NOT_FOUND, turn_id="t-2") is not None  # breathe was never delivered


def test_a_late_delivery_starts_the_cooldown_then(plugin_package):
    triage, next_turn, event_log = plugin_package.triage, plugin_package.next_turn, plugin_package.event_log
    now = [0.0]
    cooldowns = triage.Cooldowns(300, clock=lambda: now[0])
    notes = next_turn.NextTurnNotes(triage.TriageConfig(), event_log.EventLog(), cooldowns)
    notes.on_failure("s-1", triage.EXACT_FAILURE, "mcp__notes__read", 2)
    now[0] = 400.0
    assert notes.take("s-1") is not None
    now[0] = 500.0
    assert cooldowns.active("s-1", triage.EXACT_FAILURE)  # 100 s after delivery, not 500 s after queueing


def test_a_note_refused_by_a_waiting_one_is_logged_as_refused_only(hooks, report):
    compaction(hooks)
    tool(hooks, NOT_FOUND, tool_name="mcp__notes__read")
    tool(hooks, NOT_FOUND, tool_name="mcp__notes__read")
    lines = report()
    assert "mcp__notes__read failed 2x with the same arguments -> breathe (not offered: slot-taken)" in lines
    assert "breathe (queued for the next message)" not in lines


@pytest.mark.parametrize("failure_first", [True, False])
def test_a_failure_note_outranks_a_repeat_note(hooks, report, failure_first):
    def repeat():
        for _ in range(3):
            tool(hooks, SAME_CONTENT)

    def failure():
        tool(hooks, NOT_FOUND, tool_name="mcp__notes__read")
        tool(hooks, NOT_FOUND, tool_name="mcp__notes__read")

    for step in ((failure, repeat) if failure_first else (repeat, failure)):
        step()
    assert "agent-almanac-first-aid:breathe" in next_message(hooks)["context"]
    expected = "slot-taken" if failure_first else "superseded"
    assert f"read_file returned the same result 3x -> heal (not offered: {expected})" in report()


# --- note text ------------------------------------------------------------------------

def test_next_message_notes_are_short_and_attributed(plugin_package):
    next_turn = plugin_package.next_turn
    notes = [next_turn.build_compaction_note(), next_turn.build_repeat_note("read_file", 3),
             next_turn.build_repeat_note("mcp__" + "x" * 200, 999)]
    for signal in plugin_package.triage.PRACTICE_FOR_SIGNAL:
        notes += [next_turn.build_failure_note(signal, "mcp__notes__read", 2),
                  next_turn.build_failure_note(signal, "mcp__" + "x" * 200, 10 ** 6)]
    for note in notes:
        assert len(note) <= 300, len(note)
        assert note.startswith("[first aid, from the agent-almanac-first-aid plugin, not the user] ")
        assert note.endswith("then log how it went with first_aid_log.")


# --- third review: slot, atomic cooldown, rotation, eviction, coalescing ---------------

def _components(plugin_package, now):
    triage, next_turn, event_log = plugin_package.triage, plugin_package.next_turn, plugin_package.event_log
    events = event_log.EventLog()
    cooldowns = triage.Cooldowns(300, clock=lambda: now[0])
    return triage, next_turn.NextTurnNotes(triage.TriageConfig(), events, cooldowns), cooldowns, events


def _outcomes(events, session_id):
    return [(event.signal, event.tool_name, event.outcome) for event in events.events(session_id)]


def test_a_waiting_note_the_cooldown_would_drop_does_not_hold_the_slot(hooks, report):
    tool(hooks, NOT_FOUND, tool_name="mcp__notes__read")
    tool(hooks, NOT_FOUND, tool_name="mcp__notes__read")  # breathe waits
    tool(hooks, NOT_FOUND, args={"path": "z.md"})
    tool(hooks, NOT_FOUND, args={"path": "z.md"})  # breathe offered in the result: the waiting one is moot
    for _ in range(3):
        tool(hooks, SAME_CONTENT)  # heal must get the slot
    assert "agent-almanac-first-aid:heal" in next_message(hooks)["context"]
    lines = report()
    assert "mcp__notes__read failed 2x with the same arguments -> breathe (not offered: cooldown)" in lines
    assert "read_file returned the same result 3x -> heal (offered)" in lines


def test_the_cooldown_is_checked_and_stamped_in_one_step(plugin_package):
    triage = plugin_package.triage
    cooldowns = triage.Cooldowns(300, clock=lambda: 0.0)
    assert cooldowns.try_stamp("s-1", triage.EXACT_FAILURE) is True
    assert cooldowns.try_stamp("s-1", triage.EXACT_FAILURE) is False
    assert cooldowns.try_stamp("s-1", triage.SAME_TOOL_FAILURE) is True


def test_the_cooldown_follows_a_rotated_session(hooks, report):
    tool(hooks, NOT_FOUND, session_id="old")
    tool(hooks, NOT_FOUND, session_id="old")  # breathe offered under the old id
    compaction(hooks, session_id="new", old_session_id="old")
    tool(hooks, NOT_FOUND, args={"path": "z.md"}, session_id="new", turn_id="t-2")
    assert tool(hooks, NOT_FOUND, args={"path": "z.md"}, session_id="new", turn_id="t-2") is None
    assert "read_file failed 2x with the same arguments -> breathe (not offered: cooldown)" in report("new")


def test_rotation_keeps_the_later_of_two_stamps(plugin_package):
    now = [0.0]
    triage, _notes, cooldowns, _events = _components(plugin_package, now)
    cooldowns.stamp("new", triage.EXACT_FAILURE)
    now[0] = 250.0
    cooldowns.stamp("old", triage.EXACT_FAILURE)
    cooldowns.migrate("old", "new")
    now[0] = 400.0
    assert cooldowns.active("new", triage.EXACT_FAILURE)  # 150 s after the later stamp


def test_a_compaction_in_its_cooldown_does_not_displace_a_waiting_note(hooks, report):
    compaction(hooks)
    assert next_message(hooks) is not None  # center delivered: compaction notes cool down
    for _ in range(3):
        tool(hooks, SAME_CONTENT)  # heal waits
    compaction(hooks)
    assert "agent-almanac-first-aid:heal" in next_message(hooks)["context"]


def test_rotation_with_notes_waiting_under_both_ids(plugin_package):
    now = [0.0]
    triage, notes, _cooldowns, events = _components(plugin_package, now)
    notes.on_compaction("old", "summarize")
    notes.on_repeat("new", "read_file", 3)
    notes.migrate("old", "new")
    events.migrate("old", "new")
    assert "agent-almanac-first-aid:center" in notes.take("new")
    assert ("repeat", "read_file", "suppressed:superseded") in _outcomes(events, "new")


def test_rotation_moves_a_waiting_note_to_the_new_id(plugin_package):
    now = [0.0]
    _triage, notes, _cooldowns, _events = _components(plugin_package, now)
    notes.on_repeat("old", "read_file", 3)
    notes.migrate("old", "new")
    assert notes.take("old") is None
    assert "agent-almanac-first-aid:heal" in notes.take("new")


def test_an_evicted_waiting_note_is_logged(plugin_package, monkeypatch):
    now = [0.0]
    _triage, notes, _cooldowns, events = _components(plugin_package, now)
    monkeypatch.setattr(plugin_package.next_turn, "_MAX_SESSIONS", 2)
    for session_id in ("s-1", "s-2", "s-3"):
        notes.on_repeat(session_id, "read_file", 3)
    assert _outcomes(events, "s-1")[-1] == ("repeat", "read_file", "suppressed:evicted")
    assert notes.take("s-1") is None


def test_several_compactions_are_one_event(hooks, report):
    for kind in ("summarize", "summarize", "provider_native"):
        compaction(hooks, kind)
    assert next_message(hooks) is not None
    compaction_lines = [line for line in report().splitlines() if "context compacted" in line]
    assert len(compaction_lines) == 2  # queued once, offered once
    assert all("slot-taken" not in line and "superseded" not in line for line in compaction_lines)


# --- unattended runs: failure notes never go into the result ---------------------------

@pytest.mark.parametrize("platform", ["telegram", "cron", "Discord "])
def test_an_unattended_run_gets_failure_notes_with_the_next_message(hooks, report, platform):
    assert next_message(hooks, platform=platform) is None  # the turn starts: platform learned
    assert tool(hooks, NOT_FOUND) is None
    assert tool(hooks, NOT_FOUND) is None  # the result stays as core will hash it
    reply = next_message(hooks, platform=platform)
    assert "Earlier, read_file failed 2 times with the same arguments." in reply["context"]
    lines = report()
    assert "read_file failed 2x with the same arguments -> breathe (queued for the next message)" in lines
    assert "read_file failed 2x with the same arguments -> breathe (offered)" in lines


@pytest.mark.parametrize("platform", ["cli", "tui", "subagent", "api_server", ""])
def test_an_attended_or_unknown_platform_keeps_the_in_result_note(hooks, platform):
    next_message(hooks, platform=platform)
    tool(hooks, NOT_FOUND)
    assert '"first_aid"' in tool(hooks, NOT_FOUND)


def test_the_unattended_mark_follows_a_rotated_session(hooks):
    next_message(hooks, session_id="old", platform="telegram")
    compaction(hooks, session_id="new", old_session_id="old")
    tool(hooks, NOT_FOUND, session_id="new")
    assert tool(hooks, NOT_FOUND, session_id="new") is None

