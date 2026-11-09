"""agent-almanac-first-aid: registration.

Bundles the kit's skills, watches tool results for failure streaks (transform_tool_result),
gives the agent a logbook tool (first_aid_log), and answers /first-aid with this session's
event log and the kit. plugin.yaml declares
exactly what register() adds, so the two stay in step.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from . import event_log, kit, logbook_tool, triage

logger = logging.getLogger(__name__)

_USAGE = "Usage: /first-aid [status|kit|help]"

_SIGNAL_TEXT = {
    triage.EXACT_FAILURE: "{tool} failed {count}x with the same arguments",
    triage.SAME_TOOL_FAILURE: "{tool} failed {count}x in a row",
}


class FirstAid:
    """The plugin's runtime state: one triage counter and one event log per process."""

    def __init__(self, config: triage.TriageConfig) -> None:
        self.triage = triage.Triage(config)
        self.events = event_log.EventLog()

    def on_transform_tool_result(self, tool_name: str = "", args: Any = None, result: Any = None,
                                 session_id: str = "", turn_id: str = "", tool_call_id: str = "",
                                 **kwargs: Any) -> Optional[str]:
        """Return the annotated result on the first warning of a failure streak, else None.

        Hermes uses the first string any plugin returns, so this returns None on every
        call except that rare crossing.
        """
        try:
            crossing = self.triage.observe(tool_name=tool_name, args=args, result=result,
                                           session_id=session_id, turn_id=turn_id, tool_call_id=tool_call_id)
            if crossing is None:
                return None
            self.events.record(session_id, signal=crossing.signal, tool_name=crossing.tool_name,
                               count=crossing.count, practice=crossing.practice, outcome=crossing.outcome)
            return crossing.transformed_result
        except Exception:  # first aid must never break a tool call
            logger.debug("%s: transform_tool_result failed", kit.PLUGIN_NAME, exc_info=True)
            return None

    def render_session_report(self, session_id: str) -> str:
        if not session_id:
            return "First aid: this command received no session id, so it cannot show this session's events."
        events = self.events.events(session_id)
        if not events:
            return "First aid: nothing noticed in this session yet."
        lines = [f"First aid, this session ({len(events)} event{'s' if len(events) != 1 else ''}):"]
        for event in events:
            if event.signal == logbook_tool.LOGBOOK_SIGNAL:
                note = f": {event.note}" if event.note else ""
                lines.append(f"  {event.time_utc}  logbook: applied {event.practice} -> "
                             f"{event.outcome.replace('_', ' ')}{note}")
                continue
            observed = _SIGNAL_TEXT.get(event.signal, "{tool}: " + event.signal).format(
                tool=event.tool_name, count=event.count)
            outcome = event.outcome.replace("suppressed:", "not offered: ")
            lines.append(f"  {event.time_utc}  {observed} -> {event.practice} ({outcome})")
        return "\n".join(lines)

    def handle_command(self, raw_args: str, session_id: str = "", **kwargs: Any) -> str:
        """Slash command handler. Returns the text to show; never prints."""
        subcommand = (raw_args or "").strip().lower()
        if subcommand in ("", "status"):
            return self.render_session_report(session_id or "") + "\n\n" + kit.render_kit()
        if subcommand == "kit":
            return kit.render_kit()
        return _USAGE


def _read_config(ctx) -> triage.TriageConfig:
    get_config = getattr(ctx, "get_config", None)
    if get_config is None:
        return triage.TriageConfig()

    def lookup(key: str, default: Any) -> Any:
        try:
            return get_config(key, default)
        except Exception:
            logger.warning("%s: could not read setting %s; using %r", kit.PLUGIN_NAME, key, default)
            return default

    return triage.TriageConfig.from_lookup(lookup)


def register(ctx) -> None:
    """Called once by Hermes at plugin load."""
    present, missing = kit.kit_skill_paths()
    for practice, skill_md in present:
        # Without a description the <available_skills> row is a bare name; the agent has
        # nothing to match a situation against.
        ctx.register_skill(practice.name, skill_md, description=practice.index_description)
    if missing:
        logger.warning("%s: kit skills missing on disk: %s", kit.PLUGIN_NAME, ", ".join(missing))

    first_aid = FirstAid(_read_config(ctx))
    ctx.register_hook("transform_tool_result", first_aid.on_transform_tool_result)
    ctx.register_tool(
        name=logbook_tool.TOOL_NAME,
        toolset=logbook_tool.TOOLSET,
        schema=logbook_tool.SCHEMA,
        handler=logbook_tool.make_handler(first_aid.events),
        description=logbook_tool.SCHEMA["description"],
        emoji="\N{ADHESIVE BANDAGE}",
    )
    ctx.register_command(
        "first-aid",
        handler=first_aid.handle_command,
        description="Show what first aid noticed in this session, and the kit",
        args_hint="[status|kit|help]",
    )
