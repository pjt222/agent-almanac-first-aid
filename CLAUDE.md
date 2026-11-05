# agent-almanac-first-aid

A Hermes Agent plugin: a first-aid kit for AI agents, built from
[agent-almanac](https://github.com/pjt222/agent-almanac)'s AI-welfare skills.

**Built so far:** eight vendored practices registered as namespaced skills with index
descriptions, and `/first-aid` listing the kit. **Planned:** triage of observable signals
(failed tool calls, repeated calls, compaction), a short non-blocking note naming the matching
practice, a local logbook tool, and a `/first-aid` session report. Keep README.md honest about
which is which: the README at the pinned SHA becomes the catalog page.

**Delivery design (decided by the user 2026-11-05, after the PR #1 review).** Hermes core's
`tool_loop_guardrails` already *detects* repeated and failing calls in-loop and warns; this
plugin's job is the *prescription*: the matching practice at a useful moment, plus the logbook.

- **In the moment, for tool-failure streaks and repeated calls:** `transform_tool_result`. On a
  threshold crossing, return the original result unchanged with one first-aid line appended;
  otherwise return `None`. Hermes uses the first string any plugin returns
  (`model_tools._apply_transform_tool_result_hook`), so returning a string on every call would
  suppress other plugins' transforms: return one only on the rare crossing, and disclose both
  the output change and that precedence. Ignore failures of the tools core treats as
  failure-tolerant (`FAILURE_TOLERANT_TOOL_NAMES` in `agent/tool_guardrails.py`: terminal,
  execute_code, process_manage, process, browser_navigate, web_extract), or every red test run
  would trigger a note.
- **After the fact, for compaction:** `post_compaction` records the event; the next
  `pre_llm_call` delivers the note. Turn-start compaction runs before `pre_llm_call`, so that
  note lands the same turn; mid-loop compaction waits for the next user message.
- **Kit:** all eight practices stay. Notes name only practices with an observable trigger
  (breathe, redirect, center, heal); attune, honesty-humility, meditate and rest stay reachable
  through the skill index and their slash commands.
- **Demo:** the failure streak uses a non-tolerant tool (e.g. `read_file` on a missing path).

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
  `pre_tool_call` block/approve), and a transformed tool result keeps the original result intact.
  First aid offers; it does not take over.
- **Short notes, rarely.** A note fires on a threshold crossing, stays under ~300 characters,
  and respects a per-signal cooldown. Never touch the system prompt (prompt cache).
- **Declared capabilities match reality.** Every tool and hook `register()` adds is listed in
  `plugin.yaml` (`provides_tools`, `provides_hooks`) and in the catalog entry. Drift fails
  validation.
- **No self-updating, no runtime fetch.** The kit's skills are vendored at a pinned agent-almanac
  commit by `scripts/sync_kit.py` (dev time only), which records the source SHA.
- **No committed bytecode or binaries** (catalog rule 17).
- **This repo becomes public.** Do not name or describe the author's unannounced projects in any
  file, commit message, PR or issue here.

## Layout

| Path | What |
|---|---|
| `plugin.yaml` | Manifest: name, version, `requires_hermes`, declared tools/hooks (none yet) |
| `__init__.py` | `register(ctx)`: registers the kit skills and `/first-aid` |
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
