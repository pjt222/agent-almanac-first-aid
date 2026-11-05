# agent-almanac-first-aid

A first-aid kit for AI agents running in [Hermes Agent](https://github.com/NousResearch/hermes-agent).

> **Status:** in development for the Hermes Agent Plugin Hackathon. This build bundles the kit and
> the `/first-aid` command; automatic triage is not built yet.

The kit is eight practices from [agent-almanac](https://github.com/pjt222/agent-almanac)'s
AI-welfare skills. The aim of the finished plugin is to notice moments a plugin can observe, such
as a run of failed tool calls, the same call repeated, or a context that had to be compacted, and
offer the matching practice in a short note. The agent decides whether to use it.

## What this build does

- **Kit.** Registers eight practices as namespaced skills: `breathe`, `redirect`, `center`,
  `honesty-humility`, `attune`, `heal`, `meditate`, `rest`. Each shows in the agent's skill index
  with a one-line "First aid: …" description, e.g. `agent-almanac-first-aid:breathe`.
- **Slash commands.** Hermes makes each practice a command, so you can hand one to the agent
  directly: `/agent-almanac-first-aid:breathe`.
- **`/first-aid`.** Lists the kit and when to reach for each practice.

## Planned

- **Triage:** per-session signals from tool results and compaction events.
- **First response:** one short, non-blocking note when a signal crosses its threshold, with a
  cooldown.
- **Logbook:** a tool the agent uses to record the practice it applied and the outcome.
- **`/first-aid` report:** the session's signals, notes and logbook.

## What it touches

No network calls, no LLM calls, no credentials, no Python dependencies, no background processes.
This build reads only its own files and writes nothing.

`scripts/sync_kit.py` is a maintainer tool, run by hand to copy the kit from agent-almanac at a
pinned commit. The plugin never imports or runs it, and nothing is fetched or updated at runtime.

## License

MIT. The bundled skills are copied from agent-almanac (MIT) at the commit recorded in
`skills/PROVENANCE.json`, with its license in `skills/LICENSE-agent-almanac`.
