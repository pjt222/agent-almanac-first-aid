"""sync_kit.check() catches unlisted and altered files; the source check catches rehashed edits."""

from __future__ import annotations

import hashlib
import json
import shutil

import pytest

from scripts import sync_kit


@pytest.fixture()
def skills_copy(tmp_path):
    copied = tmp_path / "skills"
    shutil.copytree(sync_kit.SKILLS_DIR, copied)
    return copied


def test_clean_copy_passes(skills_copy):
    assert sync_kit.check(skills_copy) == []


@pytest.mark.parametrize(
    "stray_file",
    ["breathe/references/extra.md", "breathe/scripts/run.py", "NOTES.md", "unlisted-skill/SKILL.md"],
)
def test_unlisted_file_fails(skills_copy, stray_file):
    target = skills_copy / stray_file
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("not vetted\n", encoding="utf-8")
    problems = sync_kit.check(skills_copy)
    assert any(stray_file in problem and "not listed" in problem for problem in problems), problems


def test_edited_file_fails(skills_copy):
    skill_md = skills_copy / "rest" / "SKILL.md"
    skill_md.write_bytes(skill_md.read_bytes() + b"\nInjected line.\n")
    problems = sync_kit.check(skills_copy)
    assert any("rest/SKILL.md" in problem and "hash mismatch" in problem for problem in problems), problems


def test_edited_and_rehashed_file_passes_check_by_design(skills_copy):
    """--check alone cannot prove origin; --verify-source exists for this case (run in CI)."""
    skill_md = skills_copy / "rest" / "SKILL.md"
    altered_bytes = skill_md.read_bytes() + b"\nInjected line.\n"
    skill_md.write_bytes(altered_bytes)
    provenance_file = skills_copy / "PROVENANCE.json"
    provenance = json.loads(provenance_file.read_text(encoding="utf-8"))
    provenance["files"]["rest/SKILL.md"] = hashlib.sha256(altered_bytes).hexdigest()
    provenance_file.write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    assert sync_kit.check(skills_copy) == []
