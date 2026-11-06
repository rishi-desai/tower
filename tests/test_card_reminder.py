"""Tests for card-reminder.py, the Stop hook that sends a session back once to update its status card."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from conftest import Fixture

HOOK = Path(__file__).resolve().parent.parent / "plugin" / "scripts" / "card-reminder.py"


def stop(cwd: Path, transcript: Path) -> str:
    """Run the hook on a Stop event from a session in cwd; what it prints."""
    event = json.dumps({"cwd": str(cwd), "transcript_path": str(transcript), "stop_hook_active": False})
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PLUGIN_ROOT"}
    proc = subprocess.run(
        ["python3", "-I", str(HOOK)], input=event, capture_output=True, text=True, check=True, env=env
    )
    return proc.stdout


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
