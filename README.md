# agent-almanac-first-aid

A first-aid kit for AI agents running in [Hermes Agent](https://github.com/NousResearch/hermes-agent).

> **Status:** in development for the Hermes Agent Plugin Hackathon.

The kit is eight practices from [agent-almanac](https://github.com/pjt222/agent-almanac)'s
AI-welfare skills. Hermes core already detects tool loops and warns about them. This plugin adds
the prescription: when a tool keeps failing, the failed result carries one short line naming the
matching practice and how to load it. The agent decides whether to use it.

## What this build does

- **Kit.** Registers eight practices as namespaced skills: `breathe`, `redirect`, `center`,
  `honesty-humility`, `attune`, `heal`, `meditate`, `rest`. Each shows in the agent's skill index
  with a one-line "First aid: …" description, e.g. `agent-almanac-first-aid:breathe`.
- **Slash commands.** Hermes makes each practice a command, so you can hand one to the agent
  directly: `/agent-almanac-first-aid:breathe`.
- **Failure-streak notes.** First aid counts failed tool results the way Hermes core's loop
  guardrail does. On the call where core first warns, it adds one line to that failed result
  (for calls that run one after another; see the parallel-call caveat below):

  | Signal | Default threshold (core's) | Practice |
  |---|---|---|
  | The same tool fails with identical arguments | 2nd failure | `breathe` |
  | One tool fails several times in one turn, with any arguments | 3rd failure | `redirect` |

  A JSON error gains a `first_aid` key, for example:

  ```json
  {"error": "File not found: notes.md", "first_aid": "read_file failed 2 times with the same arguments. The agent-almanac-first-aid plugin suggests the breathe practice: pause once and re-read the error before the next step. Load it with skill_view(\"agent-almanac-first-aid:breathe\"), then log how it went with first_aid_log."}
  ```

  A plain-text error gains the same sentence on a new line starting with `[first aid]`.
- **Next-message notes.** Some notes cannot ride on a tool result, so they are added to the next
  user message instead (`pre_llm_call`), opening with a label that says they come from this
  plugin, not from the user:

  | Signal | Default threshold | Practice |
  |---|---|---|
  | The conversation context was compacted (summarized) | any compaction that rewrites it | `center` |
  | A tool returned the same successful result for the same call | 3rd time in one turn | `heal` |
  | A failure streak of a tool Hermes wraps as untrusted (`web_search`, `web_extract`, `browser_*`, `mcp_*`) | as for other tools | `breathe` or `redirect` |

  Successful results themselves are never changed: core's repeat detection hashes them. Results
  of untrusted tools are not changed either: Hermes wraps them in a block that tells the model
  not to follow requests found inside it, so a note there would read like a prompt injection.
  A run made of one message (`hermes chat -q`, cron) has no next message, so these notes never
  arrive there.
- **Logbook tool.** `first_aid_log` lets the agent record the practice it applied and how it
  went (`helped`, `partly`, `no_change`), with an optional short note.
- **`/first-aid`.** Shows this session's events in order: what first aid noticed (time, tool,
  count, practice, and whether the note was offered) and what the agent logged. Then the kit.
  `/first-aid kit` shows the kit alone.

### When it stays quiet

Short notes, rarely. No note is added:

- more than once per streak, or for a second signal of the same tool in one streak;
- within 300 seconds of the same kind of note in the same session (configurable);
- for tools whose failures are normal work, the ones core treats as failure-tolerant:
  `terminal`, `execute_code`, `process`, `process_manage`, `browser_navigate`, `web_extract`;
- for results core classifies as rate-limited, where a retry after the wait is the right move;
- for results it cannot annotate cleanly: JSON that is not an object, an object that already has
  a `first_aid` key, non-text results, or a note that would change how core classifies the result;
- for routine compaction (micro-compaction, tool-result pruning), which is logged only;
- for more than one next-message note at a time: one waits per session, by priority compaction,
  then failure, then repeat; a displaced note is logged as superseded.

Offered and queued notes are recorded in the event log, and so are notes held back by the
cooldown, the rate-limit rule, the settings, an unannotatable result or a superseding note,
with the reason.

## Settings

Optional, in the profile's `config.yaml`:

```yaml
plugins:
  entries:
    agent-almanac-first-aid:
      settings:
        notes_enabled: true              # false: keep the event log and /first-aid, add no notes
        exact_failure_threshold: 2       # identical-argument failures before a note
        same_tool_failure_threshold: 3   # failures of one tool in one turn before a note
        cooldown_seconds: 300            # per session, per kind of note
        repeat_threshold: 3              # identical successful results for one call in a turn
```

The thresholds default to core's `tool_loop_guardrails` defaults. First aid cannot read that
section, so if you change core's thresholds, set these to match.

## What it touches

- **It changes failed tool results, rarely.** On a threshold crossing it returns the failed
  result with the note added: a JSON object is re-serialized with one extra key, text gets one
  appended line. The original content stays intact. Successful results are never changed, and
  nothing is ever blocked.
- **It reads every tool result.** The hook sees each tool result and its arguments, in memory, to
  classify the result and fingerprint the call (a sha256 of the arguments). It stores neither.
  The logbook tool reads only its own arguments.
- **First string wins.** Hermes calls `transform_tool_result` plugins in load order and keeps the
  first string returned. First aid returns one only on a crossing; on that call it can override,
  or be overridden by, any other plugin that transforms the same result, for example the bundled
  `security-guidance` plugin on a failed `write_file` or `patch`. That contention is rare.
- **Effect on core's stall notice and hard stops.** A first-aid note restarts core's
  identical-call count, so in a streak of identical failures its stall notice appears from the
  5th call instead of the 3rd; core's loop warnings are unchanged. With hard stops on (by
  default in messaging-gateway and cron runs, off in the CLI), core halts right after the 5th
  identical failure. With a note, the agent gets one more model request instead: if its 6th call
  is identical, core's exact-arguments block refuses it and the turn halts; if it changes the
  call, the turn goes on, and core still halts after 8 failures of the same tool. Rate-limited
  results are never annotated.
- **It adds to the next user message, rarely.** A next-message note is appended to the user's
  message as context, never to the system prompt, so the prompt cache stays intact. Like any
  such context it stays in the transcript and is sent again with later requests until a
  compaction rewrites that message. After a compaction inside the agent's turn, the note arrives
  with the next user message, not in the same turn. Hermes routes that bypass `pre_llm_call`
  context, such as the Codex app-server route, may not deliver it.
- **Background-review runs count as the session.** Hermes's background-review runs share the
  user session's id, so their tool calls are counted with it: a streak there uses up that
  session's cooldown, shows in `/first-aid` as if the main agent had it, and a repeat or an
  untrusted tool's failure there can queue a note for the user's next message.
- **A best-effort mirror.** The failure classifier and the counting rules are copied from Hermes
  v0.22.7, not imported (those modules are not a public plugin API). Failures that never reach
  `transform_tool_result`, such as timeouts, executor exceptions and tool-search bridge calls, are
  counted by core but not by first aid. For registry tools first aid sees the arguments after
  Hermes coerces their types, while core fingerprints them before, so calls differing only as
  `"50"` and `50` can count as one streak here and as two in core. When the agent issues several
  tool calls at once and Hermes runs them in parallel, the hook sees the results in the order
  they finish, while core counts them in call order, so a note can land on a different call than
  core's warning, or on a call where core does not warn.
- **Memory only.** The event log holds time, signal, tool name, count, practice and outcome per
  session, plus the logbook tool's short note (at most 160 characters, written by the agent).
  It never holds tool arguments or result bodies. It is bounded (200 events per session, 256
  sessions), writes nothing to disk, and resets when Hermes restarts or reloads plugins. If a
  compaction gives the session a new id (`compression.in_place: false`), counts, waiting notes
  and the log move to it. With
  `dashboard.turn_isolation` on (Desktop/TUI, off by default), the agent turns run in another
  process than slash commands, so `/first-aid` cannot see the events.
- **Context.** The kit adds eight rows to the agent's skill index (descriptions under 60
  characters each) and nine slash commands. A note is at most 300 characters, once per streak or
  event. Following one costs more: loading a practice adds its text (5.7 to 17.5 KB) to the
  conversation, and the agent may re-read what it is working on. In the live compaction test the
  turn after `/compress` grew the context from about 18.6K to 26K tokens. Notes also follow a
  manual `/compress` (user decision 2026-11-09).
  The `first_aid_log` tool is a plugin tool, so Hermes's tool search (on by default) defers it.
  Measured with Hermes v0.22.7's own functions: where tool search is already on, as in a freshly
  created profile (Hermes defers several of its own tools), the tool adds one line of about 95
  characters to the deferred-tool listing, and its schema (815 characters of JSON) is fetched
  only when the agent asks for it. In a profile with nothing else deferred, the tool alone
  switches tool search on, which costs about 2,300 characters per request for the bridge tools
  and the listing. With `tool_search.enabled: off`, the 815-character schema is sent with every
  request instead. The toolset `first_aid` is on by default; `hermes tools` can switch it off.
- No network calls, no LLM calls, no credentials, no Python dependencies, no background processes.

`scripts/sync_kit.py` is a maintainer tool, run by hand to copy the kit from agent-almanac at a
pinned commit. The plugin never imports or runs it, and nothing is fetched or updated at runtime.

## License

MIT. The bundled skills are copied from agent-almanac (MIT) at the commit recorded in
`skills/PROVENANCE.json`, with its license in `skills/LICENSE-agent-almanac`.
