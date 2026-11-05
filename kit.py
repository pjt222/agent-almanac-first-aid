"""The first-aid kit: which agent-almanac practices ship, and when to reach for each.

This module is the single source of truth for the kit's contents. It has no Hermes
imports, so the sync script and the tests can load it on their own.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PLUGIN_NAME = "agent-almanac-first-aid"
SKILLS_DIR = Path(__file__).resolve().parent / "skills"

# Hermes clips each <available_skills> row to this many characters
# (SKILL_PROMPT_DESC_LIMIT in agent/skill_utils.py, v0.22.7). The almanac descriptions
# open with a long definition, so clipping them would drop every "Use when" clause;
# the kit registers its own short descriptions instead.
INDEX_DESCRIPTION_LIMIT = 60


@dataclass(frozen=True)
class Practice:
    """One almanac skill in the kit, with the situation it is the first response to."""

    name: str
    reach_for_it_when: str
    index_description: str


# Display order only; no ranking is implied. Each indication condenses the "Use when ..."
# clause of that skill's own description at the pinned almanac commit, adding nothing the
# skill does not say. Keep it that way: the triage notes will rely on these mappings.
KIT: tuple[Practice, ...] = (
    Practice("breathe", "between two actions: one deliberate pause to check alignment before the next step",
             "First aid: one deliberate pause between two actions"),
    Practice("redirect", "tool failures cascade, instructions conflict, or scope pressure builds",
             "First aid: reframe when tool failures cascade"),
    Practice("center", "after a sudden context shift or tool failure, or before sustained focused work",
             "First aid: re-ground after a context shift or failure"),
    Practice("honesty-humility", "before presenting a conclusion, or when knowledge is partial or inferred",
             "First aid: calibrate confidence before a conclusion"),
    Practice("attune", "communication feels mismatched, or feedback was unexpected",
             "First aid: adapt when communication feels mismatched"),
    Practice("heal", "after a chain of errors suggesting drift, or when earlier assumptions may be stale",
             "First aid: full check after a chain of errors"),
    Practice("meditate", "between unrelated tasks, or when reasoning feels scattered or assumption-driven",
             "First aid: clear context noise between unrelated tasks"),
    Practice("rest", "when even the self-care practices feel like more activity, or after sustained intensive work",
             "First aid: stop on purpose when more activity won't help"),
)

KIT_NAMES: tuple[str, ...] = tuple(practice.name for practice in KIT)


def qualified_name(practice_name: str) -> str:
    """The name skill_view() resolves for a bundled practice, e.g. 'agent-almanac-first-aid:breathe'."""
    return f"{PLUGIN_NAME}:{practice_name}"


def kit_skill_paths(skills_dir: Path = SKILLS_DIR) -> tuple[list[tuple[Practice, Path]], list[str]]:
    """Return ((practice, SKILL.md path) for every kit practice present, names missing on disk), in kit order."""
    present: list[tuple[Practice, Path]] = []
    missing: list[str] = []
    for practice in KIT:
        skill_md = skills_dir / practice.name / "SKILL.md"
        if skill_md.is_file():
            present.append((practice, skill_md))
        else:
            missing.append(practice.name)
    return present, missing


def render_kit() -> str:
    """Plain-text listing of the kit for the /first-aid command."""
    lines = ["First-aid kit (agent-almanac practices):"]
    for practice in KIT:
        lines.append(f"  {practice.name:<17} {practice.reach_for_it_when}")
    lines.append("")
    # Hermes turns every plugin skill into a slash command named after its qualified name.
    lines.append(f"Hand one to the agent with /{qualified_name('<practice>')}, e.g. /{qualified_name('breathe')}")
    return "\n".join(lines)
