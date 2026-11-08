"""The contract with the real git-spice: tower reads git-spice's tracking ref itself (stacks.py, ADR 0010), so this
checks that what tower reads matches what git-spice says, on a stack git-spice built. Skipped when git-spice isn't on
PATH (mise.toml pins the version CI runs)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import Fixture, git

from tower import cli

ROOT = Path(__file__).resolve().parent.parent


def real_git_spice() -> str | None:
    """The git-spice binary to run: mise's shim picks the version from the current folder's mise.toml, which a test's
    throwaway repo doesn't have, so ask mise from this repo for the real path; else whatever is on PATH."""
    if shutil.which("mise"):
        found = subprocess.run(["mise", "which", "git-spice"], cwd=ROOT, capture_output=True, text=True, check=False)
        if found.returncode == 0 and found.stdout.strip():
            return found.stdout.strip()
    return shutil.which("git-spice")


GIT_SPICE = real_git_spice()
pytestmark = pytest.mark.skipif(GIT_SPICE is None, reason="git-spice isn't installed")
IDENTITY = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}


def spice(cwd: Path, *args: str) -> str:
    """Run git-spice in cwd, never prompting; its stdout. Fails the test if it fails."""
    proc = subprocess.run(
        [str(GIT_SPICE), "--no-prompt", *args],
        cwd=cwd,
        env={**os.environ, **IDENTITY},
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def spice_parents(cwd: Path) -> dict[str, tuple[str, bool]]:
    """git-spice's own view: each tracked branch -> (its parent, whether git-spice says it needs a restack)."""
    lines = spice(cwd, "log", "short", "--all", "--json").splitlines()
    branches = [json.loads(line) for line in lines]
    return {b["name"]: (b["down"]["name"], bool(b["down"].get("needsRestack"))) for b in branches if "down" in b}


def tower_parents(capsys: pytest.CaptureFixture[str]) -> dict[str, tuple[str, bool]]:
    """tower's view of the same: each stacked branch -> (its parent, whether it needs a restack)."""
    assert cli.main(["state", "--json"]) == 0
    stacks = json.loads(capsys.readouterr().out)["stacks"]
    return {b["name"]: (b["parent"], b["restack"] != "") for s in stacks for b in s["branches"]}


def test_tower_reads_the_stack_git_spice_built_as_git_spice_reports_it(
    repo: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    path = repo.add_worktree("feat-a")
    spice(path, "repo", "init", "--trunk", "main")
    spice(path, "branch", "track")
    for branch in ("feat-b", "feat-c"):
        (path / f"{branch}.txt").write_text(branch, encoding="utf-8")
        git(path, "add", f"{branch}.txt")
        spice(path, "branch", "create", branch, "-m", branch)

    assert (
        tower_parents(capsys)
        == spice_parents(repo.main)
        == {
            "feat-a": ("main", False),
            "feat-b": ("feat-a", False),
            "feat-c": ("feat-b", False),
        }
    )

    # Amending feat-b moves it under feat-c: both tools say feat-c needs a restack.
    spice(path, "down")
    (path / "more.txt").write_text("more", encoding="utf-8")
    git(path, "add", "more.txt")
    git(path, "commit", "-q", "--amend", "--no-edit")

    assert (
        tower_parents(capsys)
        == spice_parents(repo.main)
        == {
            "feat-a": ("main", False),
            "feat-b": ("feat-a", False),
            "feat-c": ("feat-b", True),
        }
    )
