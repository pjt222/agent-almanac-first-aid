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


# Display order only; no ranking is implied. Each indication condenses the "Use when ..."
# clause of that skill's own description at the pinned almanac commit, adding nothing the
# skill does not say. Keep it that way: the triage notes will rely on these mappings.
KIT: tuple[Practice, ...] = (
    Practice("breathe", "between two actions: one deliberate pause to check alignment before the next step"),
    Practice("redirect", "tool failures cascade, instructions conflict, or scope pressure builds"),
    Practice("center", "after a sudden context shift or tool failure, or before sustained focused work"),
    Practice("honesty-humility", "before presenting a conclusion, or when knowledge is partial or inferred"),
    Practice("attune", "communication feels mismatched, or feedback was unexpected"),
    Practice("heal", "after a chain of errors suggesting drift, or when earlier assumptions may be stale"),
    Practice("meditate", "between unrelated tasks, or when reasoning feels scattered or assumption-driven"),
    Practice("rest", "when even the self-care practices feel like more activity, or after sustained intensive work"),
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
