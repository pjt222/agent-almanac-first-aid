"""register() adds exactly what plugin.yaml declares, and /first-aid answers."""

from __future__ import annotations

import re

import kit
from scripts import sync_kit


def _declared_list(manifest_text: str, key: str) -> list[str]:
    """Read a flow-style list (`key: [a, b]`) or block list from plugin.yaml without a YAML library."""
    flow_match = re.search(rf"^{key}:\s*\[(.*)\]\s*$", manifest_text, re.MULTILINE)
    if flow_match:
        return [item.strip() for item in flow_match.group(1).split(",") if item.strip()]
    block_match = re.search(rf"^{key}:\s*\n((?:\s+-\s+\S+\s*\n)+)", manifest_text, re.MULTILINE)
    return re.findall(r"-\s+(\S+)", block_match.group(1)) if block_match else []


def test_registers_every_kit_skill(registered_context):
    assert list(registered_context.skills) == list(kit.KIT_NAMES)
    for practice_name, skill_md in registered_context.skills.items():
        assert skill_md.is_file(), practice_name


def test_registers_each_skill_with_its_index_description(registered_context):
    for practice in kit.KIT:
        assert registered_context.skill_descriptions[practice.name] == practice.index_description


def test_registered_tools_and_hooks_match_manifest(registered_context):
    manifest_text = (sync_kit.REPO_ROOT / "plugin.yaml").read_text(encoding="utf-8")
    assert sorted(registered_context.tools) == sorted(_declared_list(manifest_text, "provides_tools"))
    assert sorted(registered_context.hooks) == sorted(_declared_list(manifest_text, "provides_hooks"))


def test_registers_first_aid_command(registered_context):
    assert list(registered_context.commands) == ["first-aid"]


def test_first_aid_status_lists_the_kit(registered_context):
    handler = registered_context.commands["first-aid"]["handler"]
    for raw_args in ("", "status", "  STATUS  "):
        reply = handler(raw_args, session_id="s-1", platform="cli")
        for practice_name in kit.KIT_NAMES:
            assert practice_name in reply


def test_first_aid_kit_and_help(registered_context):
    handler = registered_context.commands["first-aid"]["handler"]
    assert "breathe" in handler("kit")
    assert handler("help").startswith("Usage: /first-aid")
    assert handler("nonsense").startswith("Usage: /first-aid")
