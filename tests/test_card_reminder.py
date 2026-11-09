"""Tests for card-reminder.py, the Stop hook that sends a session back once to update its status card."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest
from conftest import Fixture

HOOK = Path(__file__).resolve().parent.parent / "plugin" / "scripts" / "card-reminder.py"

# The hook dates a session by its transcript's creation time, which only macOS keeps (st_birthtime); elsewhere it
# stays quiet by design, so these tests would check nothing there (CI runs on Linux).
pytestmark = pytest.mark.skipif(not hasattr(os.stat_result, "st_birthtime"), reason="needs st_birthtime (macOS)")


def stop(cwd: Path, transcript: Path) -> str:
    """Run the hook on a Stop event from a session in cwd; what it prints."""
    event = json.dumps({"cwd": str(cwd), "transcript_path": str(transcript), "stop_hook_active": False})
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PLUGIN_ROOT"}
    proc = subprocess.run(
        ["python3", "-I", str(HOOK)], input=event, capture_output=True, text=True, check=True, env=env
    )
    return proc.stdout


def commit_later(cwd: Path) -> None:
    """Commit in cwd, dated a minute from now: after the session started and after any card written so far."""
    later = str(int(time.time()) + 60)
    env = {**os.environ, "GIT_AUTHOR_DATE": later, "GIT_COMMITTER_DATE": later}
    git_cmd = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run([*git_cmd, "commit", "-q", "--allow-empty", "-m", "later"], cwd=cwd, env=env, check=True)


def test_a_change_after_the_card_names_the_card_to_update(repo: Fixture, tmp_path: Path) -> None:
    worktree = repo.add_worktree("feat-1")
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("", encoding="utf-8")
    changed = worktree / "notes.txt"
    changed.write_text("new work\n", encoding="utf-8")
    later = time.time() + 60
    os.utime(changed, (later, later))

    context = json.loads(stop(worktree, transcript))["hookSpecificOutput"]["additionalContext"]

    assert f"({repo.ledger / 'feat-1.md'})" in context


def test_no_change_since_the_card_says_nothing(repo: Fixture, tmp_path: Path) -> None:
    worktree = repo.add_worktree("feat-1")
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("", encoding="utf-8")
    card = repo.write_card("feat-1", "running")
    later = time.time() + 120
    os.utime(card, (later, later))

    assert stop(worktree, transcript) == ""


def test_a_commit_pulled_into_the_main_checkout_says_nothing(repo: Fixture, tmp_path: Path) -> None:
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("", encoding="utf-8")
    # tower clean's `git pull --ff-only` brings in a merge commit GitHub made after the hub's session started
    commit_later(repo.main)

    assert stop(repo.main, transcript) == ""


def test_a_worker_commit_after_its_card_names_the_card_to_update(repo: Fixture, tmp_path: Path) -> None:
    worktree = repo.add_worktree("feat-1")
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("", encoding="utf-8")
    repo.write_card("feat-1", "running")
    commit_later(worktree)

    context = json.loads(stop(worktree, transcript))["hookSpecificOutput"]["additionalContext"]

    assert f"({repo.ledger / 'feat-1.md'})" in context
