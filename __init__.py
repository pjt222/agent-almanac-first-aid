"""agent-almanac-first-aid: registration.

Skeleton build: bundles the kit's skills and the /first-aid command. Triage hooks,
nudges and the logbook tool arrive in later versions; plugin.yaml declares exactly
what register() adds, so the two stay in step.
"""

from __future__ import annotations

import logging

from . import kit

logger = logging.getLogger(__name__)

_USAGE = "Usage: /first-aid [status|kit|help]"


def _handle_first_aid(raw_args: str, **kwargs) -> str:
    """Slash command handler. Returns the text to show; never prints."""
    subcommand = (raw_args or "").strip().lower()
    if subcommand in ("", "status"):
        return (
            "First aid: triage is not active in this build yet; the kit is ready.\n\n"
            + kit.render_kit()
        )
    if subcommand == "kit":
        return kit.render_kit()
    return _USAGE


def register(ctx) -> None:
    """Called once by Hermes at plugin load."""
    present, missing = kit.kit_skill_paths()
    for practice, skill_md in present:
        # Without a description the <available_skills> row is a bare name; the agent has
        # nothing to match a situation against.
        ctx.register_skill(practice.name, skill_md, description=practice.index_description)
    if missing:
        logger.warning("%s: kit skills missing on disk: %s", kit.PLUGIN_NAME, ", ".join(missing))

    ctx.register_command(
        "first-aid",
        handler=_handle_first_aid,
        description="List the first-aid kit and when to reach for each practice",
        args_hint="[status|kit|help]",
    )
