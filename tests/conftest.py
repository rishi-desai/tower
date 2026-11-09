"""Shared fixtures: a throwaway repo with a ledger in a fake vault (through TOWER_CONF) and a fake `claude` on PATH
whose `agents --json` prints the sessions a test chose."""

from __future__ import annotations

import json
import os
import shlex
import stat
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

FAKE_GH = Path(__file__).with_name("fake_gh.py")  # the program fake_github puts on PATH as gh


def fake_github(tmp_path: Path, prs: list[dict[str, object]], failing: dict[str, str] | None = None) -> None:
    """Put a `gh` on PATH (FAKE_GH) that lists a branch's PRs the way gh and GitHub do, from prs: gh pr list's records,
    newest first, each with its headRefName (one without is on every branch) and isCrossRepository; headRepository (a
    full name, or None for a deleted fork) overrides the head repo that implies. Asking about a branch in failing
    fails with its message on stderr. It answers:
    - `pr list [--head B] --json F [--limit N]` as gh does: every PR on B, forks too, cut at --limit (30 unless
      given), with only the fields asked for.
    - `api 'repos/{owner}/{repo}/pulls?head=...&state=...&per_page=N'` as GitHub does for acme/widget, once gh has
      filled its placeholders (`:repo` too): head=acme:B gives B's PRs whose head repo is acme's (or gone); a head
      without an owner is ignored; state defaults to open and per_page to 30. Records are REST's pull objects.
    - `auth status` and `repo view` succeed; anything else exits 9."""
    data = tmp_path / "github.json"
    data.write_text(json.dumps(prs), encoding="utf-8")
    command = shlex.join([sys.executable, str(FAKE_GH), str(data), json.dumps(failing or {})])
    gh = tmp_path / "bin" / "gh"
    # sh runs it, not a #! naming this Python: a path with a space or past the #! length limit can't break it
    gh.write_text(f'#!/bin/sh\nexec {command} "$@"\n', encoding="utf-8")
    gh.chmod(0o755)


def behind_forks(branch: str, *own: dict[str, object], forks: int = 31) -> list[dict[str, object]]:
    """fake_github's PRs on branch, newest first: forks strangers' merged PRs from their own forks' branch of that
    name (31: more than gh pr list shows unless told), then own, this repo's (older numbers)."""
    strangers = [
        {"number": 1000 - n, "state": "MERGED", "title": "typo", "headRefName": branch, "isCrossRepository": True}
        for n in range(forks)
    ]
    return strangers + [{"headRefName": branch, "isCrossRepository": False, **pr} for pr in own]


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


def track(
    repo_dir: Path, branches: dict[str, tuple[str, int | None]], trunk: str = "main", remote: str | None = None
) -> None:
    """Write git-spice's tracking ref for branches ({name: (parent, PR number or None)}), as git-spice 0.33 does."""
    env = {**os.environ, "GIT_INDEX_FILE": str(repo_dir / ".git" / "spice-test-index")}

    def blob(text: str) -> str:
        """Write text as a git blob; its id."""
        return subprocess.run(
            ["git", "hash-object", "-w", "--stdin"],
            cwd=repo_dir,
            input=text,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    files = {"repo": json.dumps({"trunk": trunk, **({"remote": remote} if remote else {})}), "version": "1"}
    for name, (parent, pr) in branches.items():
        state: dict[str, object] = {"base": {"name": parent, "hash": git(repo_dir, "rev-parse", parent).strip()}}
        if pr is not None:
            state["change"] = {"github": {"pr": {"number": pr, "gqlID": f"PR_{pr}"}}}
        files[f"branches/{name}"] = json.dumps(state, indent=2)
    subprocess.run(["git", "read-tree", "--empty"], cwd=repo_dir, env=env, check=True)
    for path, text in files.items():
        cacheinfo = f"100644,{blob(text)},{path}"
        subprocess.run(["git", "update-index", "--add", "--cacheinfo", cacheinfo], cwd=repo_dir, env=env, check=True)
    tree = subprocess.run(["git", "write-tree"], cwd=repo_dir, env=env, capture_output=True, text=True, check=True)
    commit = git(repo_dir, "commit-tree", tree.stdout.strip(), "-m", "spice").strip()
    git(repo_dir, "update-ref", "refs/spice/data", commit)


def unreadable_tracking(repo_dir: Path, *branches: str) -> None:
    """A git-spice tracking ref that tracks branches but that tower can't read (no `repo` blob, so no trunk)."""
    env = {**os.environ, "GIT_INDEX_FILE": str(repo_dir / ".git" / "spice-test-index")}
    subprocess.run(["git", "read-tree", "--empty"], cwd=repo_dir, env=env, check=True)
    for name in branches:
        blob = subprocess.run(
            ["git", "hash-object", "-w", "--stdin"],
            cwd=repo_dir,
            input="{}",
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        cacheinfo = f"100644,{blob},branches/{name}"
        subprocess.run(["git", "update-index", "--add", "--cacheinfo", cacheinfo], cwd=repo_dir, env=env, check=True)
    tree = subprocess.run(["git", "write-tree"], cwd=repo_dir, env=env, capture_output=True, text=True, check=True)
    git(repo_dir, "update-ref", "refs/spice/data", git(repo_dir, "commit-tree", tree.stdout.strip(), "-m", "x").strip())


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
