"""Tests for plugin/scripts/record-event.py, the hook that appends each session's facts to tower's event log.

Each test runs the hook the way Claude Code does: a separate `python3 -I` process with the hook's JSON on stdin.
TOWER_DIR points the log at a temporary folder. The hook must never fail or change the session, so every run is
checked for exit code 0 and an empty stdout (on SessionStart and UserPromptSubmit, stdout would reach Claude).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent.parent / "plugin" / "scripts" / "record-event.py"
# The interpreter behind the dev venv: a Python with no `tower` installed, so the hook can only find the core through
# the plugin's lib/tower link.
BASE_PYTHON = str(Path(sys.base_prefix) / "bin" / "python3")
SYSTEM_PYTHON = "/usr/bin/python3"  # what a hook's `python3` can resolve to; 3.9 on macOS


def run_hook(
    payload: object, tower_dir: Path, python: str = sys.executable, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run the hook once with payload (JSON-encoded unless it's already a str) and check it stayed silent."""
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    proc = subprocess.run(
        [python, "-I", str(HOOK)],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "TOWER_DIR": str(tower_dir), **(env or {})},
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""
    return proc


def logged(tower_dir: Path) -> list[dict[str, object]]:
    """Every event in tower_dir's log, oldest first."""
    log = tower_dir / "events.jsonl"
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def git(cwd: Path, *args: str) -> None:
    """Run one git command in cwd with a throwaway identity, failing the test if it fails."""
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def worktree(tmp_path: Path) -> Path:
    """A repo at tmp/tower (branch main) with a linked worktree at tmp/tower/worktrees/feat-22 (branch feat-22)."""
    repo = tmp_path / "tower"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "commit", "-q", "--allow-empty", "-m", "start")
    git(repo, "worktree", "add", "-q", "-b", "feat-22", "worktrees/feat-22")
    return (repo / "worktrees" / "feat-22").resolve()


def hook_input(event_name: str, cwd: Path, **fields: object) -> dict[str, object]:
    """The fields every hook receives from Claude Code, plus the event's own."""
    return {
        "session_id": "s-1",
        "transcript_path": "/home/u/.claude/projects/x/s-1.jsonl",
        "cwd": str(cwd),
        "hook_event_name": event_name,
        **fields,
    }


INTERPRETERS = [
    pytest.param(sys.executable, id="dev-venv"),
    # No `tower` installed: the core can only come through the plugin's lib/tower link.
    pytest.param(BASE_PYTHON, id="no-tower-installed"),
    # What a hook's `python3` falls back to when Claude Code was started without mise on PATH: 3.9 on a stock Mac,
    # older than the core's 3.11 floor, so the hook path must still work there.
    pytest.param(
        SYSTEM_PYTHON,
        id="system-python3",
        marks=pytest.mark.skipif(not Path(SYSTEM_PYTHON).exists(), reason=f"no {SYSTEM_PYTHON} on this machine"),
    ),
]


@pytest.mark.parametrize("python", INTERPRETERS)
def test_a_worker_session_records_its_events_in_order(worktree: Path, tmp_path: Path, python: str) -> None:
    tower_dir = tmp_path / "state"
    run_hook(hook_input("SessionStart", worktree, source="startup", model="claude-opus-5-5"), tower_dir, python)
    run_hook(hook_input("UserPromptSubmit", worktree, prompt="/implement #22"), tower_dir, python)
    run_hook(
        hook_input(
            "Notification",
            worktree,
            message="Claude needs your permission to use Bash",
            notification_type="permission_prompt",
        ),
        tower_dir,
        python,
    )
    run_hook(
        hook_input(
            "PostToolUse",
            worktree,
            tool_name="AskUserQuestion",
            tool_input={"questions": [{"question": "Which?"}]},
            tool_response={"answers": {"Which?": "A"}},
        ),
        tower_dir,
        python,
    )
    run_hook(hook_input("Stop", worktree, stop_hook_active=False), tower_dir, python)
    run_hook(hook_input("SessionEnd", worktree, reason="prompt_input_exit"), tower_dir, python)

    events = logged(tower_dir)
    assert [(e["type"], e["data"]) for e in events] == [
        ("session-start", {"source": "startup"}),
        ("prompt", {"prompt_chars": 14}),
        (
            "needs-you",
            {"notification_type": "permission_prompt", "message": "Claude needs your permission to use Bash"},
        ),
        ("answer", {}),
        ("stop", {"stop_hook_active": False}),
        ("session-end", {"reason": "prompt_input_exit"}),
    ]
    repo = worktree.parent.parent
    for event in events:
        assert (event["session_id"], event["repo"], event["worktree"], event["branch"]) == (
            "s-1",
            str(repo),
            str(worktree),
            "feat-22",
        )
        assert isinstance(event["ts"], str) and event["ts"].endswith("Z")
    assert not (tower_dir / "errors.log").exists()


def test_outside_git_the_place_fields_are_null(tmp_path: Path) -> None:
    tower_dir = tmp_path / "state"
    plain = tmp_path / "plain"
    plain.mkdir()
    run_hook(hook_input("Stop", plain, stop_hook_active=True), tower_dir)

    [event] = logged(tower_dir)
    assert (event["type"], event["repo"], event["worktree"], event["branch"]) == ("stop", None, None, None)
    assert event["data"] == {"stop_hook_active": True}


def test_a_detached_head_has_no_branch(worktree: Path, tmp_path: Path) -> None:
    git(worktree, "checkout", "-q", "--detach")
    run_hook(hook_input("Stop", worktree), tmp_path / "state")

    [event] = logged(tmp_path / "state")
    assert (event["worktree"], event["branch"]) == (str(worktree), None)


def test_other_hook_calls_record_nothing(worktree: Path, tmp_path: Path) -> None:
    run_hook(hook_input("PostToolUse", worktree, tool_name="Bash", tool_input={}, tool_response={}), tmp_path / "s")
    run_hook(hook_input("PreToolUse", worktree, tool_name="Bash", tool_input={}), tmp_path / "s")

    assert logged(tmp_path / "s") == []
    assert not (tmp_path / "s" / "errors.log").exists()


@pytest.mark.parametrize("stdin", ["not json", "[1, 2]", ""])
def test_bad_input_is_noted_in_errors_log_and_the_session_carries_on(tmp_path: Path, stdin: str) -> None:
    tower_dir = tmp_path / "state"
    run_hook(stdin, tower_dir)

    assert logged(tower_dir) == []
    errors = (tower_dir / "errors.log").read_text(encoding="utf-8")
    assert "record-event:" in errors and "Error" in errors


def test_an_unwritable_tower_dir_still_exits_0_and_says_why_on_stderr(worktree: Path, tmp_path: Path) -> None:
    blocker = tmp_path / "state"
    blocker.write_text("a file where the folder should be", encoding="utf-8")
    proc = run_hook(hook_input("Stop", worktree), blocker)

    assert "record-event:" in proc.stderr and "errors.log failed" in proc.stderr


def test_a_missing_core_is_noted_in_errors_log_and_the_session_carries_on(tmp_path: Path) -> None:
    lonely_plugin = tmp_path / "plugin"  # a plugin copy whose lib/tower link was dropped
    (lonely_plugin / "lib").mkdir(parents=True)
    tower_dir = tmp_path / "state"
    run_hook(
        hook_input("Stop", tmp_path), tower_dir, python=BASE_PYTHON, env={"CLAUDE_PLUGIN_ROOT": str(lonely_plugin)}
    )

    assert logged(tower_dir) == []
    assert "can't import tower's core" in (tower_dir / "errors.log").read_text(encoding="utf-8")


def test_errors_log_is_capped_by_moving_a_full_one_aside(tmp_path: Path) -> None:
    tower_dir = tmp_path / "state"
    tower_dir.mkdir()
    (tower_dir / "errors.log").write_text("old\n" * 300_000, encoding="utf-8")  # 1.2 MB
    run_hook("not json", tower_dir)

    assert (tower_dir / "errors.log.1").read_text(encoding="utf-8").startswith("old\n")
    fresh = (tower_dir / "errors.log").read_text(encoding="utf-8")
    assert "old\n" not in fresh and "record-event:" in fresh  # "old" alone can be in the traceback's paths ("folder")
