"""The kit table, the vendored skills and PROVENANCE.json agree with each other."""

from __future__ import annotations

import re

import kit
from scripts import sync_kit


def test_kit_has_eight_unique_practices():
    assert len(kit.KIT_NAMES) == 8
    assert len(set(kit.KIT_NAMES)) == len(kit.KIT_NAMES)


def test_every_practice_has_an_indication():
    for practice in kit.KIT:
        assert practice.reach_for_it_when.strip(), practice.name


def test_every_practice_is_vendored():
    present, missing = kit.kit_skill_paths()
    assert missing == []
    assert [name for name, _ in present] == list(kit.KIT_NAMES)


def test_vendored_frontmatter_name_matches_directory():
    present, _ = kit.kit_skill_paths()
    for practice_name, skill_md in present:
        text = skill_md.read_text(encoding="utf-8")
        assert text.startswith("---\n"), f"{practice_name}: no YAML frontmatter"
        match = re.search(r"^name:\s*(\S+)\s*$", text, re.MULTILINE)
        assert match and match.group(1) == practice_name


def test_provenance_matches_files_on_disk():
    assert sync_kit.check() == []


def test_qualified_name_uses_manifest_name():
    manifest_text = (sync_kit.REPO_ROOT / "plugin.yaml").read_text(encoding="utf-8")
    manifest_name = re.search(r"^name:\s*(\S+)\s*$", manifest_text, re.MULTILINE).group(1)
    assert manifest_name == kit.PLUGIN_NAME
    assert kit.qualified_name("breathe") == f"{manifest_name}:breathe"


def test_kit_listing_names_every_practice():
    listing = kit.render_kit()
    for practice_name in kit.KIT_NAMES:
        assert practice_name in listing
