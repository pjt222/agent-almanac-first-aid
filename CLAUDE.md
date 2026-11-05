# agent-almanac-first-aid

A Hermes Agent plugin: a first-aid kit for AI agents. It watches a session for
objective signs of strain (a streak of failed tool calls, the same call repeated
in a loop, context compaction), offers the matching practice from
[agent-almanac](https://github.com/pjt222/agent-almanac)'s AI-welfare skills
as a short, non-blocking note, and keeps a local logbook. `/first-aid` shows the
session's signals, notes and logbook.

Built for the Nous Research Hermes Agent Plugin Hackathon (submissions close
2026-11-11, 17:00 EST). The entry is a PR adding `plugin-catalog/agent-almanac-first-aid.yaml`
to `NousResearch/hermes-agent`, pinned to a full commit SHA of this repo.

## Hard constraints (catalog admission rules, and this plugin's own promises)

- **Public surfaces only.** Hooks, `ctx.register_*`, `plugin_db`/`ctx.state`. Never patch,
  wrap or rebind Hermes core (`hermes plugins validate` refuses it).
- **No network, no LLM calls, no credentials, no Python dependencies.** This keeps every hook far
  inside the 30 s hook timeout and keeps the disclosure to one line. Adding any of these is a
  design change, not a feature: it needs a README disclosure and the user's sign-off.
- **Never block a turn.** `pre_llm_call` returns context or `None`, never `{"action": "block"}`.
  First aid offers; it does not take over.
- **Short notes, rarely.** A note fires on a threshold crossing, stays under ~300 characters,
  and respects a per-signal cooldown. Prompt-cache friendliness matters: injected context is
  appended to the user message, never to the system prompt.
- **Declared capabilities match reality.** Every tool and hook `register()` adds is listed in
  `plugin.yaml` (`provides_tools`, `provides_hooks`) and in the catalog entry. Drift fails
  validation.
- **No self-updating, no runtime fetch.** The kit's skills are vendored at a pinned agent-almanac
  commit by `scripts/sync_kit.py` (dev time only), which records the source SHA.
- **No committed bytecode or binaries** (catalog rule 16).
- **This repo becomes public.** Do not name or describe the author's unannounced projects in any
  file, commit message, PR or issue here.

## Layout

| Path | What |
|---|---|
| `plugin.yaml` | Manifest: name, version, `requires_hermes`, declared tools/hooks, `config_schema` |
| `__init__.py` | `register(ctx)`: wires skills, hooks, tool, slash command |
| `skills/<name>/SKILL.md` | Vendored almanac practices (read-only copies) |
| `kit.py` | The kit table (practice → when to reach for it); no Hermes imports |
| `skills/PROVENANCE.json` | Source repo, commit SHA and per-file sha256 of the vendored kit |
| `skills/LICENSE-agent-almanac` | The almanac's MIT license, carried with the vendored skills |
| `scripts/sync_kit.py` | Copies the kit from an agent-almanac checkout at a given commit; `--check` verifies |
| `tests/` | pytest with a recording fake ctx; never imports Hermes. `tests/pytest.ini` lives there on purpose |

## Commands

Run from the repo root.

```bash
# Unit tests (no Hermes needed). Pass `tests`: the ini lives in tests/ because the
# repo root is the plugin package and must not be collected.
uv run --no-project --with 'pytest>=8,<9' pytest tests

# Vendored kit matches PROVENANCE.json
python3 scripts/sync_kit.py --check

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
