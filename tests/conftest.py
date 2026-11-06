"""Shared fixtures: a throwaway repo with a ledger in a fake vault (through TOWER_CONF) and a fake `claude` on PATH
whose `agents --json` prints the sessions a test chose."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest


def git(cwd: Path, *args: str) -> str:
    """Run one git command in cwd with a throwaway identity; its stdout. Fails the test if git fails."""
    proc = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout


@dataclass
class Fixture:
    """A repo named `repo` with a main checkout, its ledger, and where the fake `claude` reads its sessions from."""

    main: Path
    ledger: Path
    agents_json: Path
    tower_dir: Path

    def add_worktree(self, branch: str, commits: int = 1) -> Path:
        """A new worktree on a new branch under worktrees/, with commits not on main."""
        path = self.main / "worktrees" / branch.replace("/", "-")
        git(self.main, "worktree", "add", "-q", "-b", branch, str(path))
        for i in range(commits):
            git(path, "commit", "-q", "--allow-empty", "-m", f"{branch} {i}")
        return path

    def write_card(self, branch: str, status: str, ticket: str = "#42", age_hours: float = 0) -> Path:
        """The status card for branch, in the format the status-card skill writes."""
        card = self.ledger / f"{branch.replace('/', '__')}.md"
        card.write_text(
            f'---\nstatus: {status}\nticket: "{ticket}"\nupdated: 2026-11-06T09:00\nbranch: {branch}\npr: none\n---\n\n'
            "## Done\n- something\n",
            encoding="utf-8",
        )
        if age_hours:
            then = time.time() - age_hours * 3600
            os.utime(card, (then, then))
        return card

    def sessions(self, *sessions: dict[str, object]) -> None:
        """What `claude agents --json` prints from now on."""
        self.agents_json.write_text(json.dumps(list(sessions)), encoding="utf-8")


def interactive(name: str, cwd: Path, status: str, **extra: object) -> dict[str, object]:
    """One interactive session as `claude agents --json` lists it."""
    return {"id": name, "name": name, "kind": "interactive", "cwd": str(cwd), "status": status, **extra}


@pytest.fixture(autouse=True)
def isolated_tower_dir(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test gets its own tower folder (policy file, event log), so none reads or migrates this machine's real
    ~/.claude/tower. Tests that need a particular one set TOWER_DIR themselves."""
    monkeypatch.setenv("TOWER_DIR", str(tmp_path_factory.mktemp("tower-dir")))


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Fixture]:
    """A repo with only its main checkout, an empty ledger, wip_limit=3, and no sessions; cwd is the main checkout."""
    main = tmp_path / "code" / "repo"
    main.mkdir(parents=True)
    git(main, "init", "-q", "-b", "main")
    git(main, "commit", "-q", "--allow-empty", "-m", "start")
    (main / ".git" / "info" / "exclude").write_text("worktrees/\n", encoding="utf-8")

    vault = tmp_path / "vault"
    ledger = vault / "repo" / "Ledger"
    ledger.mkdir(parents=True)
    conf = tmp_path / "tower.conf"
    conf.write_text(f"projects_dir={vault}\nwip_limit=3\n", encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    agents_json = tmp_path / "agents.json"
    agents_json.write_text("[]", encoding="utf-8")
    fake_claude = bin_dir / "claude"
    fake_claude.write_text(f'#!/bin/sh\n[ "$1 $2" = "agents --json" ] && exec cat "{agents_json}"\nexit 2\n')
    fake_claude.chmod(fake_claude.stat().st_mode | stat.S_IEXEC)

    tower_dir = tmp_path / "tower"
    monkeypatch.setenv("TOWER_CONF", str(conf))
    monkeypatch.setenv("TOWER_DIR", str(tower_dir))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.chdir(main)
    yield Fixture(main=main, ledger=ledger, agents_json=agents_json, tower_dir=tower_dir)
