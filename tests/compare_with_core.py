"""Maintainer tool: compare triage.py's copy of Hermes core with core's own functions.

triage.py copies Hermes's failure classifier and loop-guardrail counting instead of importing
them (they are not a public plugin API). This script imports the real ones from a Hermes
checkout and checks the copy against them: constants, the classifier on a corpus, and random
sequences of tool calls where every counter must match core's after every call and every
first-aid note must land on a call where core warns. Run it whenever Hermes changes
agent/tool_guardrails.py, agent/tool_result_classification.py, agent/display.py or
agent/tool_dispatch_helpers.py.

Scope: it checks the copied classifier and counters against core. The plugin's own note layer
(once per streak, the cooldown, the next-message slot, the note text), which has no
counterpart in core, is covered by the unit tests instead.

The plugin never imports or runs this file, and pytest does not collect it (the unit tests
never import Hermes; this harness has to). Run it from the repo root with the Python
environment Hermes itself uses, for example:

    PYTHONPATH=~/.hermes/hermes-agent ~/.hermes/.../venv/bin/python -B \\
        tests/compare_with_core.py --hermes ~/.hermes/hermes-agent

Core initializes a Hermes home on first use, so the harness points HERMES_HOME at a temporary
directory and removes it afterwards; it writes nothing else (no bytecode either). Exit status 0
means no mismatch, and that the run saw offered and queued crossings to compare.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import random
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.dont_write_bytecode = True

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE = "agent_almanac_first_aid_compared"


def load_plugin():
    spec = importlib.util.spec_from_file_location(PACKAGE, REPO_ROOT / "__init__.py",
                                                  submodule_search_locations=[str(REPO_ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = module
    spec.loader.exec_module(module)
    return module


CORPUS = [
    None, "", "ok", "Error: x", "error: x", '{"error": "x"}', '{"error": null}', '{"error": ""}',
    '{"success": false}', '{"success": false, "message": "m"}', '{"success": true, "message": "m"}',
    '{"message": "m"}', '{"status": "failed"}', '{"status": "ERROR"}', '{"status": "timeout"}',
    '{"results": []}', '{"results": [{"error": "a"}]}', '{"results": [{"error": "a"}, {"ok": 1}]}',
    '{"results": [1]}', '[{"error": "x"}]', '["Error"]', '"Error: s"', "42", "null",
    '{"user_summary": "denied"}', '{"guardrail_refusal": true, "error": "x"}',
    '{"guardrail_refusal": "true", "error": "x"}', '{"exit_code": 1}', '{"exit_code": 0, "error": "x"}',
    '{"exit_code": null, "error": "x"}', '{"bytes_written": 1}', '{"bytes_written": 1, "error": "x"}',
    '{"success": true}', '{"success": true, "error": "x"}', "x" * 498 + '"error": "y"',
    "x" * 501 + '"error": "y"', '{"error": {"message": "rate limit"}}', '{"error": "slow", "retry_after": 3}',
    '{"error": "x", "status": 429}', "Error: 429 too many requests", {"error": "dict"}, {"ok": 1}, ["a"],
    '{"error": "x", "first_aid": "y"}', '  {"error": "x"}  ',
    json.dumps({"content": "", "total_lines": 0, "error": "File not found: /tmp/a.md", "not_found": True}),
]
CORPUS_TOOLS = ["read_file", "terminal", "write_file", "patch", "web_search", "delegate_task",
                "mcp__notes__read", "browser_click", "search_files"]
UNTRUSTED_PROBES = ["web_search", "web_extract", "browser_", "browser_click", "mcp_", "mcp__a__b",
                    "mcp_filesystem_read_file", "xmcp_a", "web", "read_file", "terminal", "", "first_aid_log"]

NOT_FOUND = json.dumps({"error": "File not found: a"})
OTHER = json.dumps({"error": "File not found: b"})
THROTTLED = json.dumps({"error": "429 Too Many Requests"})
SUCCESS = json.dumps({"content": "hi"})
SUCCESS_SPACED = '{"content":  "hi"}'
TEXT_ERROR = "Error: broke"
LANDED = json.dumps({"bytes_written": 1})
REFUSAL = json.dumps({"error": "BLOCKED: already read", "guardrail_refusal": True})
CALLS = [
    ("read_file", {"path": "a"}, [NOT_FOUND, OTHER, SUCCESS, SUCCESS_SPACED, TEXT_ERROR, THROTTLED, REFUSAL]),
    ("read_file", {"path": "b"}, [NOT_FOUND, SUCCESS]),
    ("search_files", {"pattern": "x"}, [NOT_FOUND, SUCCESS, THROTTLED]),
    ("web_search", {"q": "x"}, [THROTTLED, NOT_FOUND, SUCCESS]),
    ("mcp__notes__read", {"id": 1}, [NOT_FOUND, SUCCESS]),
    ("browser_click", {"ref": "e1"}, [NOT_FOUND, SUCCESS]),
    ("write_file", {"path": "a"}, [LANDED, NOT_FOUND]),
    ("todo", {}, [SUCCESS]),
    ("terminal", {"command": "x"}, [json.dumps({"exit_code": 1}), json.dumps({"exit_code": 0})]),
    ("first_aid_log", {"practice": "breathe"}, [json.dumps({"error": "bad"}), json.dumps({"success": True})]),
]


def main(argv=None) -> int:
    home = tempfile.mkdtemp(prefix="first-aid-compare-home-")
    os.environ["HERMES_HOME"] = home  # before any core import: core initializes a home on use
    try:
        return _run(argv)
    finally:
        shutil.rmtree(home, ignore_errors=True)


def _run(argv) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hermes", required=True, type=Path, help="a Hermes checkout (hermes-agent)")
    parser.add_argument("--sequences", type=int, default=3000)
    parser.add_argument("--length", type=int, default=40)
    parser.add_argument("--seed", type=int, default=20261109)
    options = parser.parse_args(argv)
    if not (Path.cwd() / "plugin.yaml").is_file() or Path.cwd().resolve() != REPO_ROOT:
        print("Run this from the repo root.", file=sys.stderr)
        return 2
    sys.path.insert(0, str(options.hermes.expanduser().resolve()))

    from agent import display as core_display
    from agent import tool_dispatch_helpers as core_dispatch
    from agent import tool_guardrails as core_guard
    from agent import tool_result_classification as core_classification

    triage = load_plugin().triage
    problems: list[str] = []

    # 1. Constants.
    for name, core_value in [
        ("FAILURE_TOLERANT_TOOL_NAMES", core_guard.FAILURE_TOLERANT_TOOL_NAMES),
        ("PROGRESS_RESET_TOOL_NAMES", core_guard.PROGRESS_RESET_TOOL_NAMES),
        ("IDEMPOTENT_TOOL_NAMES", core_guard.IDEMPOTENT_TOOL_NAMES),
        ("MUTATING_TOOL_NAMES", core_guard.MUTATING_TOOL_NAMES),
        ("FILE_MUTATING_TOOL_NAMES", core_classification.FILE_MUTATING_TOOL_NAMES),
        ("GUARDRAIL_REFUSAL_KEY", core_classification.GUARDRAIL_REFUSAL_KEY),
        ("_RETRY_AFTER_KEYS", core_classification._RETRY_AFTER_KEYS),
        ("_FAILED_STATUSES", core_classification._FAILED_STATUSES),
        ("UNTRUSTED_TOOL_NAMES", core_dispatch._UNTRUSTED_TOOL_NAMES),
        ("UNTRUSTED_TOOL_PREFIXES", core_dispatch._UNTRUSTED_TOOL_PREFIXES),
        ("DEFAULT_EXACT_FAILURE_THRESHOLD", core_guard.ToolCallGuardrailConfig().exact_failure_warn_after),
        ("DEFAULT_SAME_TOOL_FAILURE_THRESHOLD", core_guard.ToolCallGuardrailConfig().same_tool_failure_warn_after),
        ("DEFAULT_REPEAT_THRESHOLD", core_guard.STALL_GUARD_IDENTICAL_CALL_THRESHOLD),
    ]:
        if getattr(triage, name) != core_value:
            problems.append(f"constant {name} differs")
    for name in ("_FAILURE_MARKER_RE", "_RATE_LIMIT_RE"):
        mine, core = getattr(triage, name), getattr(core_classification, name)
        if (mine.pattern, mine.flags) != (core.pattern, core.flags):
            problems.append(f"regex {name} differs")
    print("constants checked")

    # 2. Classifier and helpers on a corpus.
    compared = core_raised = 0
    for tool in CORPUS_TOOLS:
        for result in CORPUS:
            try:
                core_failed = core_display._detect_tool_failure(tool, result)[0]
            except Exception:  # core raises on a few shapes; there is no verdict to match
                core_raised += 1
                continue
            compared += 1
            if triage.detect_failure(tool, result) != core_failed:
                problems.append(f"detect_failure({tool!r}, {str(result)[:60]!r})")
            if isinstance(result, str) and triage.is_rate_limited(result) != core_classification.is_rate_limited(result):
                problems.append(f"is_rate_limited({str(result)[:60]!r})")
    for name in UNTRUSTED_PROBES:
        if triage.is_untrusted_tool(name) != core_dispatch._is_untrusted_tool(name):
            problems.append(f"is_untrusted_tool({name!r})")
    print(f"classifier: {compared} comparisons, {core_raised} inputs on which core itself raised")

    # 3. Random sequential call sequences: every counter after every call, every crossing.
    expected_code = {triage.EXACT_FAILURE: "repeated_exact_failure_warning",
                     triage.SAME_TOOL_FAILURE: "same_tool_failure_warning"}
    calls_checked = 0
    crossing_outcomes: Counter = Counter()
    for sequence in range(options.sequences):
        rng = random.Random(options.seed + sequence)
        clock = iter(range(0, 10 ** 9, 1000)).__next__  # an advancing clock keeps the cooldown out of the way
        tracker = triage.Triage(triage.TriageConfig(), clock=clock)
        core = core_guard.ToolCallGuardrailController()
        for index in range(options.length):
            tool, args, results = rng.choice(CALLS)
            result = rng.choice(results)
            crossing = tracker.observe(tool_name=tool, args=args, result=result, session_id="s", turn_id="t",
                                       tool_call_id=f"c{index}")
            delivered = crossing.transformed_result if crossing and crossing.transformed_result else result
            failed = core_display._detect_tool_failure(tool, delivered)[0]
            decision = core.after_call(tool, args, delivered, failed=failed)
            label = f"sequence {sequence} call {index} ({tool})"
            if failed != core_display._detect_tool_failure(tool, result)[0]:
                problems.append(f"{label}: the note changed core's failure verdict")
            if tool in triage.OWN_TOOL_NAMES:
                continue  # first aid skips its own tool; core counts only that tool's own keys
            calls_checked += 1
            turn = tracker._turns[("s", "t")]
            own = triage.OWN_TOOL_NAMES  # core also counts the plugin's own tool; first aid skips it
            if {(s.tool_name, s.args_hash): n for s, n in core._exact_failure_counts.items()
                    if s.tool_name not in own} != turn.exact:
                problems.append(f"{label}: exact-args counts differ")
            if {name: n for name, n in core._same_tool_failure_counts.items() if name not in own} != turn.same:
                problems.append(f"{label}: same-tool counts differ")
            if {(s.tool_name, s.args_hash) for s, flag in core._progress_since_failure.items()
                    if flag and s.tool_name not in own} != turn.progress:
                problems.append(f"{label}: progress marks differ")
            if {(s.tool_name, s.args_hash): v for s, v in core._no_progress.items()
                    if s.tool_name not in own} != turn.no_progress:
                problems.append(f"{label}: no-progress records differ")
            if crossing is not None:
                crossing_outcomes[crossing.outcome] += 1
                if decision.code != expected_code[crossing.signal]:
                    problems.append(f"{label}: a crossing where core gives {decision.code!r}")
                if crossing.outcome == triage.QUEUED_OUTCOME and (
                        crossing.transformed_result is not None or not core_dispatch._is_untrusted_tool(tool)):
                    problems.append(f"{label}: queued crossing changed the result or is not an untrusted tool")
                if crossing.outcome == "offered" and core_dispatch._is_untrusted_tool(tool):
                    problems.append(f"{label}: a note inside an untrusted tool's result")
    print(f"sequences: {options.sequences} x {options.length} calls, {calls_checked} checked call by call")
    print("crossings by outcome:", dict(sorted(crossing_outcomes.items())))
    if not crossing_outcomes["offered"] or not crossing_outcomes[triage.QUEUED_OUTCOME]:
        problems.append("no offered or no queued crossing: nothing was compared (vacuous run)")

    for problem in problems[:20]:
        print("MISMATCH", problem)
    print(f"{len(problems)} mismatch(es)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
