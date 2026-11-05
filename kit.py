"""The first-aid kit: which agent-almanac practices ship, and when to reach for each.

This module is the single source of truth for the kit's contents. It has no Hermes
imports, so the sync script and the tests can load it on their own.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PLUGIN_NAME = "agent-almanac-first-aid"
SKILLS_DIR = Path(__file__).resolve().parent / "skills"


@dataclass(frozen=True)
class Practice:
    """One almanac skill in the kit, with the situation it is the first response to."""

    name: str
    reach_for_it_when: str


# Ordered from the lightest intervention to the fullest. The indications paraphrase
# each skill's own "Use when ..." description at the pinned almanac commit.
KIT: tuple[Practice, ...] = (
    Practice("breathe", "a step just failed: one deliberate pause before the next action"),
    Practice("redirect", "the planned approach stopped working: failures cascade or the same call repeats"),
    Practice("center", "context shifted under you: after compaction or a sudden change of task"),
    Practice("honesty-humility", "before reporting a result after a troubled stretch"),
    Practice("attune", "communication with the person feels mismatched, or feedback surprised you"),
    Practice("heal", "errors keep chaining and earlier assumptions may have gone stale"),
    Practice("meditate", "moving between unrelated tasks, or reasoning feels scattered"),
    Practice("rest", "every fix feels like more activity, and stopping is the better move"),
)

KIT_NAMES: tuple[str, ...] = tuple(practice.name for practice in KIT)


def qualified_name(practice_name: str) -> str:
    """The name skill_view() resolves for a bundled practice, e.g. 'agent-almanac-first-aid:breathe'."""
    return f"{PLUGIN_NAME}:{practice_name}"


def kit_skill_paths(skills_dir: Path = SKILLS_DIR) -> tuple[list[tuple[str, Path]], list[str]]:
    """Return ((name, SKILL.md path) for every kit practice present, names missing on disk), in kit order."""
    present: list[tuple[str, Path]] = []
    missing: list[str] = []
    for practice in KIT:
        skill_md = skills_dir / practice.name / "SKILL.md"
        if skill_md.is_file():
            present.append((practice.name, skill_md))
        else:
            missing.append(practice.name)
    return present, missing


def render_kit() -> str:
    """Plain-text listing of the kit for the /first-aid command."""
    lines = ["First-aid kit (agent-almanac practices):"]
    for practice in KIT:
        lines.append(f"  {practice.name:<17} {practice.reach_for_it_when}")
    lines.append("")
    lines.append(f"Load one with skill_view(\"{qualified_name('breathe')}\").")
    return "\n".join(lines)
