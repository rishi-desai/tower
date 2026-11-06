"""Tests for plugin/scripts/session-env.py, the SessionStart hook that sets each session's runtime variables (PORT and
the rest, from the project's runtime config) through Claude Code's CLAUDE_ENV_FILE.

Each test runs the hook the way Claude Code does: a separate `python3 -I` process with the hook's JSON on stdin. It
must always exit 0: a session's start never fails over runtime variables.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import Fixture

HOOK = Path(__file__).resolve().parent.parent / "plugin" / "scripts" / "session-env.py"
SYSTEM_PYTHON = "/usr/bin/python3"  # what a hook's `python3` can resolve to; 3.9 on macOS


def run_hook(cwd: Path, env_file: Path | None, python: str = sys.executable) -> subprocess.CompletedProcess[str]:
    """Run the hook once for a session starting in cwd; check it exited 0."""
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_ENV_FILE"}
    if env_file is not None:
        env["CLAUDE_ENV_FILE"] = str(env_file)
    payload = {"session_id": "s-1", "cwd": str(cwd), "hook_event_name": "SessionStart", "source": "startup"}
    proc = subprocess.run(
        [python, "-I", str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return proc


def context(proc: subprocess.CompletedProcess[str]) -> str:
    """The additionalContext the hook gave Claude."""
    output = json.loads(proc.stdout)
    assert output["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    text: str = output["hookSpecificOutput"]["additionalContext"]
    return text


def write_config(repo: Fixture, text: str) -> None:
    path = repo.tower_dir / "projects" / "repo.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.mark.parametrize("python", [sys.executable, SYSTEM_PYTHON])
def test_a_worker_session_gets_its_own_port(repo: Fixture, tmp_path: Path, python: str) -> None:
    if not os.path.exists(python):
        pytest.skip(f"{python} isn't on this machine")
    write_config(repo, "port_base = 3000\nseed = 'cp -R {main}/data data'\n")
    worktree = repo.add_worktree("feat-1")
    env_file = tmp_path / "session.env"
    env_file.write_text("export SET_BY_ANOTHER_HOOK=1\n", encoding="utf-8")

    told = context(run_hook(worktree, env_file, python))

    assert env_file.read_text(encoding="utf-8").startswith(
        "export SET_BY_ANOTHER_HOOK=1\nexport TOWER_SLOT=1\nexport PORT=3001\n"
    )
    assert "runtime slot 1" in told
    assert "TOWER_SLOT=1 PORT=3001" in told
    assert f"cp -R {repo.main}/data data" in told


def test_without_a_config_the_hook_does_nothing(repo: Fixture, tmp_path: Path) -> None:
    env_file = tmp_path / "session.env"

    proc = run_hook(repo.add_worktree("feat-1"), env_file)

    assert proc.stdout == ""
    assert not env_file.exists()


def test_outside_a_repo_the_hook_does_nothing(repo: Fixture, tmp_path: Path) -> None:
    write_config(repo, "port_base = 3000\n")
    elsewhere = tmp_path / "not-a-repo"
    elsewhere.mkdir()

    assert run_hook(elsewhere, tmp_path / "session.env").stdout == ""


def test_a_bad_config_is_told_to_claude_and_nothing_is_set(repo: Fixture, tmp_path: Path) -> None:
    write_config(repo, "port_bsae = 3000\n")
    env_file = tmp_path / "session.env"

    told = context(run_hook(repo.add_worktree("feat-1"), env_file))

    assert not env_file.exists()
    assert "couldn't set this worktree's runtime variables" in told
    assert "unknown setting port_bsae" in told


def test_without_claude_env_file_claude_is_told_to_export_them_itself(repo: Fixture) -> None:
    write_config(repo, "port_base = 3000\n")

    told = context(run_hook(repo.add_worktree("feat-1"), None))

    assert "export TOWER_SLOT=1 PORT=3001" in told
