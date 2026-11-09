"""register() adds exactly what plugin.yaml declares, and /first-aid answers."""

from __future__ import annotations

import re

import pytest

from scripts import sync_kit


@pytest.fixture()
def kit(plugin_package):
    return plugin_package.kit


def _declared_list(manifest_text: str, key: str) -> list[str]:
    """Read a flow-style list (`key: [a, b]`) or block list from plugin.yaml without a YAML library."""
    flow_match = re.search(rf"^{key}:\s*\[(.*)\]\s*$", manifest_text, re.MULTILINE)
    if flow_match:
        return [item.strip() for item in flow_match.group(1).split(",") if item.strip()]
    block_match = re.search(rf"^{key}:\s*\n((?:\s+-\s+\S+\s*\n)+)", manifest_text, re.MULTILINE)
    return re.findall(r"-\s+(\S+)", block_match.group(1)) if block_match else []


def test_registers_every_kit_skill(registered_context, kit):
    assert list(registered_context.skills) == list(kit.KIT_NAMES)
    for practice_name, skill_md in registered_context.skills.items():
        assert skill_md.is_file(), practice_name


def test_registers_each_skill_with_its_index_description(registered_context, kit):
    for practice in kit.KIT:
        assert registered_context.skill_descriptions[practice.name] == practice.index_description


def test_registered_tools_and_hooks_match_manifest(registered_context):
    manifest_text = (sync_kit.REPO_ROOT / "plugin.yaml").read_text(encoding="utf-8")
    # Both keys must be present, so an empty declaration is a statement, not a missing line.
    for key in ("provides_tools", "provides_hooks"):
        assert re.search(rf"^{key}:", manifest_text, re.MULTILINE), f"plugin.yaml lacks {key}"
    assert sorted(registered_context.tools) == sorted(_declared_list(manifest_text, "provides_tools"))
    assert sorted(registered_context.hooks) == sorted(_declared_list(manifest_text, "provides_hooks"))


def test_registers_first_aid_command(registered_context):
    assert list(registered_context.commands) == ["first-aid"]


def test_first_aid_status_lists_the_kit(registered_context, kit):
    handler = registered_context.commands["first-aid"]["handler"]
    for raw_args in ("", "status", "  STATUS  "):
        reply = handler(raw_args, session_id="s-1", platform="cli")
        for practice_name in kit.KIT_NAMES:
            assert practice_name in reply


NOT_FOUND = '{"error": "File not found: a.md"}'


def _fail(hook, session_id="s-1", **overrides):
    call = dict(tool_name="read_file", args={"path": "a.md"}, result=NOT_FOUND, session_id=session_id,
                turn_id="t-1", tool_call_id="c-1", task_id="", api_request_id="", duration_ms=3,
                status="error", error_type="", error_message="", telemetry_schema_version=1)
    call.update(overrides)
    return hook(**call)


def test_every_callback_accepts_unknown_keywords(registered_context):
    import inspect
    callbacks = [cb for cbs in registered_context.hooks.values() for cb in cbs]
    callbacks += [command["handler"] for command in registered_context.commands.values()]
    callbacks += [tool["handler"] for tool in registered_context.tools.values()]
    for callback in callbacks:
        kinds = [parameter.kind for parameter in inspect.signature(callback).parameters.values()]
        assert inspect.Parameter.VAR_KEYWORD in kinds, callback


def test_transform_hook_returns_none_except_on_a_crossing(registered_context):
    (hook,) = registered_context.hooks["transform_tool_result"]
    assert _fail(hook, result='{"content": "ok"}') is None
    assert _fail(hook) is None
    annotated = _fail(hook)
    assert isinstance(annotated, str) and '"first_aid"' in annotated
    assert _fail(hook) is None


def test_transform_hook_never_raises(registered_context):
    (hook,) = registered_context.hooks["transform_tool_result"]
    # Mixed key types make the canonical-JSON signature raise inside triage.
    assert _fail(hook, args={1: "a", "b": 2}) is None


def test_first_aid_status_reports_this_sessions_events_only(registered_context):
    (hook,) = registered_context.hooks["transform_tool_result"]
    handler = registered_context.commands["first-aid"]["handler"]
    _fail(hook, session_id="s-1")
    _fail(hook, session_id="s-1")
    reply = handler("status", session_id="s-1", platform="cli", source=None, session_key="k")
    assert "read_file failed 2x with the same arguments -> breathe (offered)" in reply
    assert "nothing noticed in this session yet" in handler("", session_id="s-other")
    assert "received no session id" in handler("", session_id="")


def test_no_state_holds_tool_arguments_or_result_bodies(registered_context):
    (hook,) = registered_context.hooks["transform_tool_result"]
    first_aid = hook.__self__
    secret_args = {"path": "ARGS-MARKER-7f3e.md"}
    secret_error = '{"error": "RESULT-MARKER-9c1d not found"}'
    secret_content = '{"content": "RESULT-MARKER-9c1d body"}'
    for _ in range(3):
        _fail(hook, args=secret_args, result=secret_error)  # in-result note
        _fail(hook, tool_name="mcp__notes__read", args=secret_args, result=secret_error, session_id="s-2")
    for _ in range(3):
        _fail(hook, args=secret_args, result=secret_content, session_id="s-3")  # repeat note
    state = repr((first_aid.triage._turns, first_aid.cooldowns._last, first_aid.next_turn._pending,
                  first_aid.events._sessions))
    handler = registered_context.commands["first-aid"]["handler"]
    reports = "".join(handler("", session_id=session_id) for session_id in ("s-1", "s-2", "s-3"))
    # Not a vacuous pass: an offered note, a queued note and a waiting repeat note all exist.
    assert first_aid.events.events("s-1") and first_aid.events.events("s-2")
    assert set(first_aid.next_turn._pending) == {"s-2", "s-3"}
    for marker in ("ARGS-MARKER-7f3e", "RESULT-MARKER-9c1d"):
        assert marker not in state
        assert marker not in reports


def test_settings_reach_triage(plugin_package, recording_context_class):
    context = recording_context_class(settings={"notes_enabled": False})
    plugin_package.register(context)
    (hook,) = context.hooks["transform_tool_result"]
    _fail(hook)
    assert _fail(hook) is None
    reply = context.commands["first-aid"]["handler"]("", session_id="s-1")
    assert "(not offered: notes-disabled)" in reply


def test_first_aid_kit_and_help(registered_context):
    handler = registered_context.commands["first-aid"]["handler"]
    assert "breathe" in handler("kit")
    assert handler("help").startswith("Usage: /first-aid")
    assert handler("nonsense").startswith("Usage: /first-aid")
