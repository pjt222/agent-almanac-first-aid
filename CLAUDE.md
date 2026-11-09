# agent-almanac-first-aid

A Hermes Agent plugin: a first-aid kit for AI agents, built from
[agent-almanac](https://github.com/pjt222/agent-almanac)'s AI-welfare skills.

**Built so far:** eight vendored practices registered as namespaced skills with index
descriptions; failure-streak triage that adds one note to a failed tool result where core first
warns; next-message notes after compaction (center) and repeated identical successful results
(heal); an in-memory event log; the agent-callable logbook tool `first_aid_log`; and
`/first-aid` reporting the session's events and the kit. Keep README.md honest about what is
built and what is not: the README at the pinned SHA becomes the catalog page.

**Delivery design (decided by the user 2026-11-05, after the PR #1 review).** Hermes core's
`tool_loop_guardrails` already *detects* repeated and failing calls in-loop and warns; this
plugin's job is the *prescription*: the matching practice at a useful moment, plus the logbook.

- **In the moment, for tool-failure streaks:** `transform_tool_result`. Once per streak, on the
  call where core first warns (exact-args 2, same-tool 3 by default; a note on every failure read
  as canned in the 2026-11-06 live check), return the failed result with the note added: a JSON
  object gains a `first_aid` key (re-serialized: the same content, not the same bytes), plain
  text gains one appended line. Otherwise return `None`. Hermes calls the hook's plugins in load
  order and keeps the first string (`model_tools._apply_transform_tool_result_hook`), so
  returning a string on every call would suppress other plugins' transforms: return one only on
  the rare crossing, and disclose both the output change and that precedence.
  - **Never transform a successful result.** Core's no-progress and identical-call guards hash
    the post-transform result. Successful repeats belong to the next-turn channel.
  - **Never annotate a result Hermes wraps as untrusted** (`web_search`, `web_extract`,
    `browser_*`, `mcp_*`; `agent/tool_dispatch_helpers.py` `_is_untrusted_tool`): the wrapper tells
    the model not to follow tool-invocation requests inside it. Their failure note goes to the
    next user message instead (user decisions 2026-11-09).
  - **Never annotate a rate-limited result** (user decision 2026-11-09), and ignore failures of
    the tools core treats as failure-tolerant (`FAILURE_TOLERANT_TOOL_NAMES` in
    `agent/tool_guardrails.py`: terminal, execute_code, process_manage, process,
    browser_navigate, web_extract), or every red test run would trigger a note.
  - **Count like core.** `triage.py` keys streaks on `(session_id, turn_id)` and copies core's
    classifier (`agent.display._detect_tool_failure`) and `after_call` rules, with a v0.22.7
    provenance comment. It does not import `agent.tool_guardrails` or
    `agent.tool_result_classification`: they are not on validate's public list, and each such
    import is a review warning.
  - **The note names its source** (the plugin) and the loadable practice
    (`agent-almanac-first-aid:<practice>`).
  - **Self-contained, under one lock.** The hook gets `session_id`, `turn_id` and the result
    itself; it never depends on `post_tool_call`, whose order relative to the transform differs
    by call path. Hook callbacks run on worker threads and concurrent tool calls overlap.
- **After the fact, for compaction and repeated successful calls:** `post_compaction` (or the
  repeat counter) records the event; the next `pre_llm_call` delivers the note. Every in-agent
  compaction, turn-start included, runs after that turn's `pre_llm_call`
  (`agent/turn_context.py`, v0.22.7), so the note lands with the next user message (user decision
  2026-11-06, taken after being told). `next_turn.py` keeps one waiting note per session
  (compaction beats repeat), notes only `summarize`/`provider_native` compaction, and on a
  rotated session id (`old_session_id`) moves counts, waiting notes and the log to the new id.
  `pre_llm_call` returns `{"context": note}` or `None`, never a block directive.
- **Kit:** all eight practices stay. Notes name only practices with an observable trigger
  (breathe, redirect, center, heal); attune, honesty-humility, meditate and rest stay reachable
  through the skill index and their slash commands.
- **Demo:** the failure streak uses a non-tolerant tool (`read_file` on a missing path), with a
  prompt that asks for one identical retry: a capable model otherwise recovers before any
  threshold (live test 2026-11-09). The chat screen shows only Hermes's own failure line; the
  `/first-aid` report makes the note visible (user decisions 2026-11-09).

Built for the Nous Research Hermes Agent Plugin Hackathon (submissions close
2026-11-11, 17:00 EST). The entry is a PR adding `plugin-catalog/agent-almanac-first-aid.yaml`
to `NousResearch/hermes-agent`, pinned to a full commit SHA of this repo.

## Hard constraints (catalog admission rules, and this plugin's own promises)

- **Public surfaces only.** Hooks, `ctx.register_*`, `plugin_db`/`ctx.state`. Never patch,
  wrap or rebind Hermes core (`hermes plugins validate` refuses it).
- **No network, no LLM calls, no credentials, no Python dependencies.** This keeps every hook far
  inside the 30 s hook timeout and keeps the disclosure to one line. Adding any of these is a
  design change, not a feature: it needs a README disclosure and the user's sign-off.
- **Never block.** No hook returns a block directive (`pre_llm_call` `{"action": "block"}`,
  `pre_tool_call` block/approve), and a transformed tool result keeps the original content (a
  JSON object gains one key; text gains one line).
  First aid offers; it does not take over.
- **Short notes, rarely.** A note fires on a threshold crossing, stays under ~300 characters,
  and respects a per-signal cooldown. Never touch the system prompt (prompt cache).
- **Declared capabilities match reality.** Every tool and hook `register()` adds is listed in
  `plugin.yaml` (`provides_tools`, `provides_hooks`) and in the catalog entry. Drift fails
  validation.
- **No self-updating, no runtime fetch.** The kit's skills are vendored at a pinned agent-almanac
  commit by `scripts/sync_kit.py` (dev time only), which records the source SHA.
- **No committed bytecode or binaries** (catalog rule 17).
- **This repo becomes public.** Do not name or describe the author's other projects in any
  file, commit message, PR or issue here, beyond agent-almanac itself.

## Layout

| Path | What |
|---|---|
| `plugin.yaml` | Manifest: name, version, `requires_hermes`, declared tools/hooks, `config_schema` |
| `__init__.py` | `register(ctx)`: registers the kit skills, three hooks, the logbook tool and `/first-aid` |
| `triage.py` | Failure classification and counting mirrored from core, the note, the annotation; no Hermes imports |
| `event_log.py` | Bounded in-memory per-session record of what first aid noticed; no Hermes imports |
| `logbook_tool.py` | The `first_aid_log` schema and handler, writing into the event log; no Hermes imports |
| `next_turn.py` | Waiting notes for compaction and repeated results, handed to `pre_llm_call`; no Hermes imports |
| `skills/<name>/SKILL.md` | Vendored almanac practices (read-only copies) |
| `kit.py` | The kit table (practice → when to reach for it); no Hermes imports |
| `skills/PROVENANCE.json` | Source repo, commit SHA and per-file sha256 of the vendored kit |
| `skills/LICENSE-agent-almanac` | The almanac's MIT license, carried with the vendored skills |
| `scripts/sync_kit.py` | Maintainer tool: copies the kit from an agent-almanac checkout at a given commit; `--check` / `--verify-source` verify. Single-file skills only |
| `tests/` | pytest with a recording fake ctx; never imports Hermes. `tests/pytest.ini` lives there on purpose |

## Commands

Run from the repo root.

```bash
# Unit tests (no Hermes needed). Pass `tests`: the ini lives in tests/ because the
# repo root is the plugin package and must not be collected.
uv run --no-project --with 'pytest>=8,<9' pytest tests

# Vendored kit matches PROVENANCE.json (and, with a checkout, its source commit)
python3 scripts/sync_kit.py --check
python3 scripts/sync_kit.py --verify-source ../agent-almanac

# The checks catalog CI runs (needs the Hermes CLI)
hermes plugins doctor . --ci
hermes plugins validate . --install-deps

# Refresh the kit from a sibling agent-almanac checkout at a pinned commit
python3 scripts/sync_kit.py --almanac ../agent-almanac --commit <sha>
```

Do not add a `pyproject.toml` with dev dependencies or dependency groups: Hermes reads a
`pyproject.toml` beside `plugin.yaml` as the plugin's dependency declaration.

## Git

Feature work on branches with PRs; merge commits only (`gh pr merge --merge`), never squash.
Every PR gets an `advocatus-diaboli` review round before merging.
