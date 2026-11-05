# agent-almanac-first-aid

A first-aid kit for AI agents running in [Hermes Agent](https://github.com/NousResearch/hermes-agent).

> **Status:** in development for the Hermes Agent Plugin Hackathon. Not yet installable.

Agents under strain show it in ways a plugin can see: tool calls that keep failing, the same call
repeated in a loop, a context that had to be compacted. This plugin watches for those signs and,
when one appears, offers the matching practice from
[agent-almanac](https://github.com/pjt222/agent-almanac)'s AI-welfare skills in a short note. The
agent decides whether to use it. A logbook records what happened, and `/first-aid` shows it.

## Planned v1

- **Kit:** eight almanac practices, bundled as namespaced skills (`breathe`, `center`, `redirect`,
  `heal`, `rest`, `honesty-humility`, `attune`, `meditate`).
- **Triage:** per-session signals from tool results and compaction events.
- **First response:** one short, non-blocking note when a signal crosses its threshold, with a
  cooldown.
- **Logbook:** a tool the agent uses to record the practice it applied and the outcome.
- **`/first-aid`:** the session's signals, notes and logbook.

Everything runs locally: no network calls, no LLM calls, no credentials.

## License

MIT. The bundled skills come from agent-almanac (MIT); see `skills/PROVENANCE.yaml` once the kit
lands.
