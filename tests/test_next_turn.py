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


def next_message(hooks, session_id="s-1"):
    return hooks["pre_llm_call"](session_id=session_id, task_id="", turn_id="t-2", user_message="next",
                                 conversation_history=[], is_first_turn=False, model="m", platform="cli",
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


# --- note text ------------------------------------------------------------------------

def test_next_message_notes_are_short_and_attributed(plugin_package):
    next_turn = plugin_package.next_turn
    notes = [next_turn.build_compaction_note(), next_turn.build_repeat_note("read_file", 3),
             next_turn.build_repeat_note("mcp__" + "x" * 200, 999)]
    for note in notes:
        assert len(note) <= 300, len(note)
        assert note.startswith("[first aid, from the agent-almanac-first-aid plugin, not the user] ")
        assert note.endswith("then log how it went with first_aid_log.")
