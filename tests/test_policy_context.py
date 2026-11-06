"""Tests for plugin/scripts/policy-context.py, the SessionStart hook that tells a session its tower policy.

Each test runs the hook the way Claude Code does: a separate `python3 -I` process with the hook's JSON on stdin. On
SessionStart, plain stdout is added to the session's context, so the hook prints exactly one line or nothing, and it
always exits 0: it must never fail a session.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent.parent / "plugin" / "scripts" / "policy-context.py"
SYSTEM_PYTHON = "/usr/bin/python3"  # what a hook's `python3` can resolve to; 3.9 on macOS


def run_hook(payload: object, tower_dir: Path, python: str = sys.executable) -> str:
    """Run the hook once with payload (JSON-encoded unless it's already a str); returns what it printed."""
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    proc = subprocess.run(
        [python, "-I", str(HOOK)],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "TOWER_DIR": str(tower_dir), "TOWER_CONF": str(tower_dir / "tower.conf")},
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


@pytest.fixture
def widget(tmp_path: Path) -> Path:
    """A checkout whose origin is acme/widget."""
    checkout = tmp_path / "widget"
    checkout.mkdir()
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    subprocess.run(
        ["git", "-C", str(checkout), "remote", "add", "origin", "git@github.com:acme/widget.git"], check=True
    )
    return checkout


def start(cwd: Path) -> dict[str, object]:
    """A SessionStart hook call as Claude Code sends it."""
    return {"session_id": "s-1", "hook_event_name": "SessionStart", "source": "startup", "cwd": str(cwd)}


@pytest.mark.parametrize("python", [sys.executable, SYSTEM_PYTHON])
def test_the_session_learns_its_profile_and_what_asks(tmp_path: Path, widget: Path, python: str) -> None:
    if not Path(python).exists():
        pytest.skip(f"no {python} here")
    tower_dir = tmp_path / "tower"
    tower_dir.mkdir()
    (tower_dir / "policy.toml").write_text(
        'default_profile = "work"\n[repos]\n"acme/*" = "personal"\n', encoding="utf-8"
    )

    printed = run_hook(start(widget), tower_dir, python)

    assert printed.count("\n") == 1
    assert printed.startswith('tower policy for this repo (acme/widget): personal ([repos] "acme/*" in ')
    assert "Without asking: plain git pushes; merging PRs into other branches; creating, editing and closing" in printed
    assert (
        "Asks the user first: force pushes and branch deletes; merging PRs into the default branch; PR and" in printed
    )
    assert printed.endswith("Changing tower's policy, tower.conf or rule files always asks.\n")


def test_with_no_policy_file_the_session_hears_everything_asks(tmp_path: Path, widget: Path) -> None:
    printed = run_hook(start(widget), tmp_path / "tower")

    assert ": work (no policy file (" in printed
    assert "Without asking: nothing." in printed


@pytest.mark.parametrize("payload", ["not json", [], {"hook_event_name": "SessionStart", "cwd": 5}])
def test_input_it_cant_use_prints_nothing_and_never_fails(tmp_path: Path, payload: object) -> None:
    assert run_hook(payload, tmp_path / "tower") == ""
