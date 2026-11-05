"""The kit table, the vendored skills and PROVENANCE.json agree with each other."""

from __future__ import annotations

import re

import pytest

from scripts import sync_kit


@pytest.fixture()
def kit(plugin_package):
    """The kit module as Hermes loads it (a submodule of the plugin package), not a second copy."""
    return plugin_package.kit


def test_kit_has_eight_unique_practices(kit):
    assert len(kit.KIT_NAMES) == 8
    assert len(set(kit.KIT_NAMES)) == len(kit.KIT_NAMES)


def test_every_practice_has_an_indication(kit):
    for practice in kit.KIT:
        assert practice.reach_for_it_when.strip(), practice.name


def test_every_practice_is_vendored(kit):
    present, missing = kit.kit_skill_paths()
    assert missing == []
    assert [practice.name for practice, _ in present] == list(kit.KIT_NAMES)


def test_index_descriptions_fit_the_skill_index(kit):
    for practice in kit.KIT:
        assert practice.index_description.strip(), practice.name
        assert len(practice.index_description) <= kit.INDEX_DESCRIPTION_LIMIT, practice.name


def test_vendored_frontmatter_name_matches_directory(kit):
    present, _ = kit.kit_skill_paths()
    for practice, skill_md in present:
        practice_name = practice.name
        text = skill_md.read_text(encoding="utf-8")
        assert text.startswith("---\n"), f"{practice_name}: no YAML frontmatter"
        frontmatter = text[4:text.index("\n---\n", 4)]
        match = re.search(r"^name:\s*(\S+)\s*$", frontmatter, re.MULTILINE)
        assert match and match.group(1) == practice_name


def test_provenance_matches_files_on_disk():
    assert sync_kit.check() == []


def test_qualified_name_uses_manifest_name(kit):
    manifest_text = (sync_kit.REPO_ROOT / "plugin.yaml").read_text(encoding="utf-8")
    manifest_name = re.search(r"^name:\s*(\S+)\s*$", manifest_text, re.MULTILINE).group(1)
    assert manifest_name == kit.PLUGIN_NAME
    assert kit.qualified_name("breathe") == f"{manifest_name}:breathe"


def test_kit_listing_names_every_practice(kit):
    listing = kit.render_kit()
    for practice_name in kit.KIT_NAMES:
        assert practice_name in listing


def test_kit_listing_points_users_at_slash_commands_not_tool_calls(kit):
    listing = kit.render_kit()
    assert "/agent-almanac-first-aid:breathe" in listing
    assert "skill_view" not in listing
