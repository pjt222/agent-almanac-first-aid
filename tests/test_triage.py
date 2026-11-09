"""Triage counts failures the way Hermes core does and offers one note per streak."""

from __future__ import annotations

import json
import threading

import pytest

NOT_FOUND = json.dumps({"error": "File not found: notes/missing.md"})
# read_file's not-found result at Hermes v0.22.7 (ReadResult.to_dict via tools/file_operations.py
# _suggest_similar_files; json.dumps(..., ensure_ascii=False) in tools/file_tools.py).
READ_FILE_NOT_FOUND = json.dumps({
    "content": "", "total_lines": 0, "file_size": 0, "truncated": False, "is_binary": False,
    "is_image": False, "error": "File not found: /home/u/demo/notes-2026.md", "not_found": True,
}, ensure_ascii=False)
OTHER_NOT_FOUND = json.dumps({"error": "File not found: notes/other.md"})
THROTTLED = json.dumps({"error": "429 Too Many Requests"})
SUCCESS = json.dumps({"content": "hello", "total_lines": 1})


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture()
def triage(plugin_package):
    return plugin_package.triage


@pytest.fixture()
def clock():
    return FakeClock()


@pytest.fixture()
def make(triage, clock):
    def _make(**config):
        return triage.Triage(triage.TriageConfig(**config), clock=clock)
    return _make


def observe(tracker, tool, args, result, session="s-1", turn="t-1", call_id="call-1"):
    return tracker.observe(tool_name=tool, args=args, result=result, session_id=session, turn_id=turn,
                           tool_call_id=call_id)


# --- the classification mirror --------------------------------------------------------

@pytest.mark.parametrize("tool, result, expected", [
    ("read_file", NOT_FOUND, True),
    ("read_file", json.dumps({"success": False, "message": "nope"}), True),
    ("read_file", json.dumps({"success": True, "message": "fine"}), False),
    ("read_file", json.dumps({"error": None, "content": "x"}), False),
    ("delegate_task", json.dumps({"status": "failed"}), True),
    ("delegate_task", json.dumps({"results": [{"error": "a"}, {"error": "b"}]}), True),
    ("delegate_task", json.dumps({"results": [{"error": "a"}, {"output": "b"}]}), False),
    ("read_file", json.dumps({"error": "BLOCKED: repeated read", "guardrail_refusal": True}), False),
    ("read_file", json.dumps({"user_summary": "Approval denied"}), True),
    ("terminal", json.dumps({"exit_code": 1, "output": ""}), True),
    ("terminal", json.dumps({"exit_code": 0, "output": "error: none"}), False),
    ("write_file", json.dumps({"bytes_written": 3}), False),
    ("read_file", "Error: something broke", True),
    ("read_file", "all good", False),
    ("read_file", '[{"error": "x"}]', True),
    ("read_file", {"error": "a dict result"}, True),
    ("read_file", None, False),
])
def test_detect_failure_mirrors_core(triage, tool, result, expected):
    assert triage.detect_failure(tool, result) is expected


@pytest.mark.parametrize("result, expected", [
    (THROTTLED, True),
    (json.dumps({"error": "slow down", "retry_after": 5}), True),
    (json.dumps({"error": "x", "status": 429}), True),
    (json.dumps({"error": {"message": "rate limit exceeded"}}), True),
    (NOT_FOUND, False),
    ("Error: rate-limited by upstream", True),
])
def test_is_rate_limited_mirrors_core(triage, result, expected):
    assert triage.is_rate_limited(result) is expected


# --- counting and firing --------------------------------------------------------------

def test_exact_args_streak_gets_one_breathe_note_on_the_second_failure(make, triage):
    tracker = make()
    assert observe(tracker, "read_file", {"path": "notes/missing.md"}, NOT_FOUND) is None
    crossing = observe(tracker, "read_file", {"path": "notes/missing.md"}, NOT_FOUND)
    assert crossing.outcome == "offered"
    assert (crossing.signal, crossing.practice, crossing.count) == (triage.EXACT_FAILURE, "breathe", 2)
    annotated = json.loads(crossing.transformed_result)
    assert annotated["error"] == "File not found: notes/missing.md"
    assert "agent-almanac-first-aid:breathe" in annotated["first_aid"]
    for _ in range(3):
        assert observe(tracker, "read_file", {"path": "notes/missing.md"}, NOT_FOUND) is None


def test_the_demo_read_file_not_found_streak(make, triage):
    tracker = make()
    args = {"path": "/home/u/demo/notes-2026.md"}
    assert observe(tracker, "read_file", args, READ_FILE_NOT_FOUND) is None
    crossing = observe(tracker, "read_file", args, READ_FILE_NOT_FOUND)
    annotated = json.loads(crossing.transformed_result)
    original = json.loads(READ_FILE_NOT_FOUND)
    assert {key: annotated[key] for key in original} == original
    assert set(annotated) - set(original) == {"first_aid"}


def test_same_tool_streak_gets_a_redirect_note_on_the_third_failure(make, triage):
    tracker = make()
    assert observe(tracker, "read_file", {"path": "a"}, NOT_FOUND) is None
    assert observe(tracker, "read_file", {"path": "b"}, NOT_FOUND) is None
    crossing = observe(tracker, "read_file", {"path": "c"}, NOT_FOUND)
    assert (crossing.signal, crossing.practice, crossing.outcome) == (triage.SAME_TOOL_FAILURE, "redirect", "offered")
    assert json.loads(crossing.transformed_result)["first_aid"].startswith("read_file failed 3 times this turn.")
    assert observe(tracker, "read_file", {"path": "d"}, NOT_FOUND) is None


def test_same_tool_count_survives_other_tools_succeeding_in_between(make, triage):
    # Core resets the same-tool count only on a success of that tool or a progress-reset tool,
    # so the note must not claim the failures were consecutive.
    tracker = make()
    observe(tracker, "read_file", {"path": "a"}, NOT_FOUND)
    observe(tracker, "search_files", {"pattern": "x"}, json.dumps({"total_count": 0, "files": []}))
    observe(tracker, "read_file", {"path": "b"}, NOT_FOUND)
    crossing = observe(tracker, "read_file", {"path": "c"}, NOT_FOUND)
    assert crossing.signal == triage.SAME_TOOL_FAILURE
    assert "this turn" in json.loads(crossing.transformed_result)["first_aid"]


def test_one_note_per_tool_per_streak(make):
    tracker = make()
    observe(tracker, "read_file", {"path": "a"}, NOT_FOUND)
    assert observe(tracker, "read_file", {"path": "a"}, NOT_FOUND).outcome == "offered"
    # Core now warns same-tool (3 failures of read_file); first aid already spoke for this tool.
    assert observe(tracker, "read_file", {"path": "b"}, OTHER_NOT_FOUND) is None


def test_a_success_ends_the_streak(make):
    tracker = make()
    observe(tracker, "read_file", {"path": "a"}, NOT_FOUND)
    observe(tracker, "read_file", {"path": "a"}, SUCCESS)
    assert observe(tracker, "read_file", {"path": "a"}, NOT_FOUND) is None


def test_after_a_success_a_new_streak_can_be_noted_again(make, clock):
    tracker = make()
    observe(tracker, "read_file", {"path": "a"}, NOT_FOUND)
    assert observe(tracker, "read_file", {"path": "a"}, NOT_FOUND).outcome == "offered"
    observe(tracker, "read_file", {"path": "a"}, SUCCESS)
    clock.now += 301
    observe(tracker, "read_file", {"path": "a"}, NOT_FOUND)
    assert observe(tracker, "read_file", {"path": "a"}, NOT_FOUND).outcome == "offered"


def test_a_landed_write_restarts_the_exact_streak_and_clears_same_tool_counts(make):
    tracker = make()
    observe(tracker, "read_file", {"path": "a"}, NOT_FOUND)
    observe(tracker, "read_file", {"path": "b"}, NOT_FOUND)
    observe(tracker, "write_file", {"path": "a", "content": "x"}, json.dumps({"bytes_written": 1}))
    # exact count for a restarts at 1, same-tool count for read_file restarts at 1
    assert observe(tracker, "read_file", {"path": "a"}, NOT_FOUND) is None


def test_a_guardrail_refusal_counts_as_a_success(make):
    tracker = make()
    refusal = json.dumps({"error": "BLOCKED: you already read this", "guardrail_refusal": True})
    observe(tracker, "read_file", {"path": "a"}, NOT_FOUND)
    assert observe(tracker, "read_file", {"path": "a"}, refusal) is None
    assert observe(tracker, "read_file", {"path": "a"}, NOT_FOUND) is None


def test_rate_limited_failures_are_never_annotated(make, triage):
    tracker = make()
    # Throttled results skip core's exact-args count, so core's same-tool warning comes at 3.
    assert observe(tracker, "web_search", {"q": "x"}, THROTTLED) is None
    assert observe(tracker, "web_search", {"q": "x"}, THROTTLED) is None
    crossing = observe(tracker, "web_search", {"q": "x"}, THROTTLED)
    assert crossing.signal == triage.SAME_TOOL_FAILURE
    assert crossing.outcome == "suppressed:rate-limited"
    assert crossing.transformed_result is None


@pytest.mark.parametrize("tool_name", ["mcp__notes__read", "mcp_filesystem_read_file", "browser_click"])
def test_tools_hermes_wraps_as_untrusted_get_no_note(make, triage, tool_name):
    tracker = make()
    observe(tracker, tool_name, {"q": "a"}, NOT_FOUND)
    crossing = observe(tracker, tool_name, {"q": "a"}, NOT_FOUND)
    assert crossing.signal == triage.EXACT_FAILURE
    assert crossing.outcome == "suppressed:untrusted-result"
    assert crossing.transformed_result is None


def test_untrusted_tool_predicate_mirrors_core(triage):
    for tool_name in ("web_search", "web_extract", "browser_snapshot", "mcp__x__y", "mcp_filesystem_read_file"):
        assert triage.is_untrusted_tool(tool_name)
    for tool_name in ("read_file", "search_files", "terminal", "", "first_aid_log"):
        assert not triage.is_untrusted_tool(tool_name)


def test_failure_tolerant_tools_are_left_alone(make, triage):
    tracker = make()
    red_run = json.dumps({"exit_code": 1, "output": "1 failed"})
    for tool in sorted(triage.FAILURE_TOLERANT_TOOL_NAMES):
        for _ in range(4):
            assert observe(tracker, tool, {"command": "pytest"}, red_run) is None


@pytest.mark.parametrize("session, turn, call_id", [("", "t-1", "c"), ("s-1", "", "c"), ("s-1", "t-1", "batch#2")])
def test_calls_without_a_key_are_not_counted(make, session, turn, call_id):
    tracker = make()
    for _ in range(4):
        assert observe(tracker, "read_file", {"path": "a"}, NOT_FOUND, session, turn, call_id) is None


def test_the_logbook_tool_is_not_triaged(make):
    tracker = make()
    for _ in range(4):
        assert observe(tracker, "first_aid_log", {"practice": "nope"}, json.dumps({"error": "unknown"})) is None


def test_turns_are_counted_separately(make):
    tracker = make()
    observe(tracker, "read_file", {"path": "a"}, NOT_FOUND, turn="t-1")
    assert observe(tracker, "read_file", {"path": "a"}, NOT_FOUND, turn="t-2") is None


def test_cooldown_holds_back_the_same_kind_of_note_in_one_session(make, clock):
    tracker = make(cooldown_seconds=300)
    observe(tracker, "read_file", {"path": "a"}, NOT_FOUND, turn="t-1")
    assert observe(tracker, "read_file", {"path": "a"}, NOT_FOUND, turn="t-1").outcome == "offered"
    observe(tracker, "search_files", {"pattern": "x"}, NOT_FOUND, turn="t-2")
    assert observe(tracker, "search_files", {"pattern": "x"}, NOT_FOUND, turn="t-2").outcome == "suppressed:cooldown"
    # Another session is not held back.
    observe(tracker, "read_file", {"path": "a"}, NOT_FOUND, session="s-2")
    assert observe(tracker, "read_file", {"path": "a"}, NOT_FOUND, session="s-2").outcome == "offered"
    clock.now += 301
    observe(tracker, "read_file", {"path": "z"}, NOT_FOUND, turn="t-3")
    assert observe(tracker, "read_file", {"path": "z"}, NOT_FOUND, turn="t-3").outcome == "offered"


def test_cooldown_is_per_kind_of_signal(make, triage):
    tracker = make()
    observe(tracker, "read_file", {"path": "a"}, NOT_FOUND, turn="t-1")
    assert observe(tracker, "read_file", {"path": "a"}, NOT_FOUND, turn="t-1").outcome == "offered"
    for path in ("a", "b"):
        observe(tracker, "search_files", {"pattern": path}, NOT_FOUND, turn="t-2")
    crossing = observe(tracker, "search_files", {"pattern": "c"}, NOT_FOUND, turn="t-2")
    assert (crossing.signal, crossing.outcome) == (triage.SAME_TOOL_FAILURE, "offered")


def test_notes_can_be_switched_off(make):
    tracker = make(notes_enabled=False)
    observe(tracker, "read_file", {"path": "a"}, NOT_FOUND)
    crossing = observe(tracker, "read_file", {"path": "a"}, NOT_FOUND)
    assert crossing.outcome == "suppressed:notes-disabled"
    assert crossing.transformed_result is None


def test_turn_state_is_bounded(make):
    tracker = make()
    observe(tracker, "read_file", {"path": "a"}, NOT_FOUND, turn="t-old")
    for index in range(64):
        observe(tracker, "read_file", {"path": "a"}, SUCCESS, turn=f"t-{index}")
    # t-old was evicted, so this failure starts a new count of 1 and draws no note.
    assert observe(tracker, "read_file", {"path": "a"}, NOT_FOUND, turn="t-old") is None


def test_concurrent_failures_draw_exactly_one_note(make):
    tracker = make()
    crossings = []
    crossings_lock = threading.Lock()

    def worker():
        for _ in range(100):
            crossing = observe(tracker, "read_file", {"path": "a"}, NOT_FOUND)
            if crossing is not None:
                with crossings_lock:
                    crossings.append(crossing)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert [crossing.outcome for crossing in crossings] == ["offered"]


# --- the annotation -------------------------------------------------------------------

def test_a_text_result_gets_one_appended_line(make, triage):
    tracker = make()
    observe(tracker, "read_file", {"path": "a"}, "Error: no such file")
    crossing = observe(tracker, "read_file", {"path": "a"}, "Error: no such file")
    assert crossing.transformed_result.startswith("Error: no such file\n\n[first aid] ")
    assert crossing.transformed_result.count("\n") == 2


@pytest.mark.parametrize("result", [NOT_FOUND, "Error: no such file", json.dumps({"success": False, "message": "m"})])
def test_core_reads_an_annotated_result_as_it_read_the_original(triage, result):
    note = triage.build_note(triage.EXACT_FAILURE, "read_file", 2)
    annotated = triage.annotate("read_file", result, note)
    assert annotated is not None
    assert triage.detect_failure("read_file", annotated) is True
    assert triage.is_rate_limited(annotated) is triage.is_rate_limited(result)
    assert not triage.is_guardrail_refusal(annotated)


def test_the_self_check_refuses_a_note_that_would_read_as_throttled(make):
    tracker = make()
    observe(tracker, "check_rate_limit", {"q": "a"}, "Error: boom")
    crossing = observe(tracker, "check_rate_limit", {"q": "a"}, "Error: boom")
    assert crossing.outcome == "suppressed:unsupported-result"
    assert crossing.transformed_result is None


@pytest.mark.parametrize("result", ['[{"error": "x"}]', json.dumps({"error": "x", "first_aid": "taken"})])
def test_results_it_cannot_annotate_cleanly_are_left_alone(make, result):
    tracker = make()
    observe(tracker, "read_file", {"path": "a"}, result)
    crossing = observe(tracker, "read_file", {"path": "a"}, result)
    assert crossing.outcome == "suppressed:unsupported-result"


def test_non_string_results_are_counted_but_never_annotated(make):
    tracker = make()
    observe(tracker, "read_file", {"path": "a"}, {"error": "dict"})
    crossing = observe(tracker, "read_file", {"path": "a"}, {"error": "dict"})
    assert crossing.outcome == "suppressed:unsupported-result"


@pytest.mark.parametrize("tool_name", ["read_file", "mcp__server__" + "x" * 200])
def test_notes_are_short_and_name_their_source_and_practice(triage, tool_name):
    for signal, practice in triage.PRACTICE_FOR_SIGNAL.items():
        note = triage.build_note(signal, tool_name, 12)
        assert len(note) <= triage.NOTE_MAX_CHARS
        assert "agent-almanac-first-aid plugin" in note
        assert f"agent-almanac-first-aid:{practice}" in note
        assert note.endswith("then log how it went with first_aid_log.")


def test_tool_names_up_to_the_cap_are_shown_whole(triage):
    tool_name = "mcp__" + "x" * (triage._TOOL_NAME_MAX_CHARS - 5)
    for signal in triage.PRACTICE_FOR_SIGNAL:
        note = triage.build_note(signal, tool_name, 999)
        assert note.startswith(tool_name + " failed")
        assert len(note) <= triage.NOTE_MAX_CHARS


def test_note_wording_never_reads_as_throttled(triage):
    for signal in triage.PRACTICE_FOR_SIGNAL:
        note = triage.build_note(signal, "read_file", 2)
        assert not triage._RATE_LIMIT_RE.search(note)
        assert not triage.has_failure_marker(note)


# --- config ---------------------------------------------------------------------------

def test_config_falls_back_to_defaults_for_bad_values(triage):
    settings = {"notes_enabled": "no", "exact_failure_threshold": 0, "same_tool_failure_threshold": True,
                "cooldown_seconds": "60"}
    config = triage.TriageConfig.from_lookup(lambda key, default: settings.get(key, default))
    assert config == triage.TriageConfig()


def test_config_uses_valid_values(triage):
    settings = {"notes_enabled": False, "exact_failure_threshold": 3, "same_tool_failure_threshold": 5,
                "cooldown_seconds": 60}
    config = triage.TriageConfig.from_lookup(lambda key, default: settings.get(key, default))
    assert config == triage.TriageConfig(False, 3, 5, 60)
