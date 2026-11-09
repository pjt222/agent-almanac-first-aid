"""The first_aid_log tool records what the agent applied, and /first-aid shows it."""

from __future__ import annotations

import json

import pytest


@pytest.fixture()
def logbook(plugin_package):
    return plugin_package.logbook_tool


@pytest.fixture()
def tool(registered_context, logbook):
    return registered_context.tools[logbook.TOOL_NAME]


def test_tool_is_registered_with_an_openai_style_schema(tool, logbook, plugin_package):
    schema = tool["schema"]
    assert tool["toolset"] == logbook.TOOLSET
    assert schema["name"] == logbook.TOOL_NAME
    assert schema["parameters"]["type"] == "object"
    assert schema["parameters"]["required"] == ["practice", "outcome"]
    assert schema["parameters"]["properties"]["practice"]["enum"] == list(plugin_package.kit.KIT_NAMES)


def test_triage_skips_the_logbook_tool(logbook, plugin_package):
    assert logbook.TOOL_NAME in plugin_package.triage.OWN_TOOL_NAMES


def test_a_valid_entry_is_stored_and_reported(registered_context, tool):
    reply = tool["handler"]({"practice": "breathe", "outcome": "helped", "note": "Re-read the error;\nwrong folder."},
                            session_id="s-1", task_id="t", user_task="never stored")
    assert json.loads(reply) == {"success": True, "stored": True, "practice": "breathe", "outcome": "helped"}
    report = registered_context.commands["first-aid"]["handler"]("", session_id="s-1")
    assert "logbook: applied breathe -> helped: Re-read the error; wrong folder." in report
    assert "never stored" not in report


def test_core_reads_the_success_payload_as_a_success(tool, plugin_package):
    reply = tool["handler"]({"practice": "redirect", "outcome": "no_change"}, session_id="s-1")
    assert plugin_package.triage.detect_failure("first_aid_log", reply) is False


@pytest.mark.parametrize("args", [
    {"practice": "nap", "outcome": "helped"},
    {"practice": "breathe", "outcome": "failed"},
    {"practice": "breathe", "outcome": "helped", "note": 7},
    "breathe",
])
def test_bad_input_is_an_error_core_counts_as_a_failure(tool, plugin_package, args):
    reply = tool["handler"](args, session_id="s-1")
    assert "error" in json.loads(reply)
    assert plugin_package.triage.detect_failure("first_aid_log", reply) is True


def _events(registered_context, session_id):
    report = registered_context.commands["first-aid"]["handler"]("", session_id=session_id)
    return [line for line in report.splitlines() if "logbook:" in line]


def test_note_length_is_capped(tool, logbook, plugin_package, recording_context_class):
    context = recording_context_class()
    plugin_package.register(context)
    handler = context.tools[logbook.TOOL_NAME]["handler"]
    handler({"practice": "center", "outcome": "partly", "note": "x" * 500}, session_id="s-1")
    (line,) = _events(context, "s-1")
    note = line.split("partly: ", 1)[1]
    assert len(note) == logbook.NOTE_MAX_CHARS and note.endswith("...")


def test_without_a_session_id_nothing_is_stored(tool):
    reply = json.loads(tool["handler"]({"practice": "rest", "outcome": "helped"}, session_id=None))
    assert reply["success"] is True and reply["stored"] is False
