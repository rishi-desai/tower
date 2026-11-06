"""Tests for file overlaps between worktrees in `tower state`: each worktree's changed files come from git (commits
since it split from the other branch, plus uncommitted and untracked files), never from cards, and every pair of
worktrees that changed the same file is reported, with the files, under Housekeeping.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import Fixture, git

from tower import cli


def commit_file(worktree: Path, name: str, text: str = "x\n") -> None:
    """Write name in worktree and commit it."""
    path = worktree / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    git(worktree, "add", name)
    git(worktree, "commit", "-q", "-m", f"edit {name}")


def tower_state(capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    """Run `tower state --json` in-process and parse what it prints."""
    assert cli.main(["state", "--json"]) == 0
    result: dict[str, object] = json.loads(capsys.readouterr().out)
    return result


def test_two_worktrees_editing_the_same_file_are_flagged(repo: Fixture, capsys: pytest.CaptureFixture[str]) -> None:
    one = repo.add_worktree("feat-1", commits=0)
    two = repo.add_worktree("feat-2", commits=0)
    commit_file(one, "src/app.py")
    commit_file(one, "README.md")
    commit_file(two, "src/app.py", "y\n")
    commit_file(two, "docs/other.md")

    result = tower_state(capsys)

    assert result["overlaps"] == [{"worktrees": ["feat-1", "feat-2"], "files": ["src/app.py"]}]
    housekeeping = result["housekeeping"]
    assert isinstance(housekeeping, list)
    assert {"label": "feat-1", "note": "changes the same file as feat-2: src/app.py"} in housekeeping


def test_worktrees_editing_different_files_are_not_flagged(repo: Fixture, capsys: pytest.CaptureFixture[str]) -> None:
    commit_file(repo.add_worktree("feat-1", commits=0), "a.py")
    commit_file(repo.add_worktree("feat-2", commits=0), "b.py")

    assert tower_state(capsys)["overlaps"] == []


def test_uncommitted_and_untracked_files_count(repo: Fixture, capsys: pytest.CaptureFixture[str]) -> None:
    commit_file(repo.main, "shared.py")  # on main, so both worktrees start with it
    one = repo.add_worktree("feat-1", commits=0)
    two = repo.add_worktree("feat-2", commits=0)
    (one / "shared.py").write_text("edited, not committed\n", encoding="utf-8")
    (two / "shared.py").write_text("also edited\n", encoding="utf-8")
    (one / "new file.md").write_text("untracked\n", encoding="utf-8")
    (two / "new file.md").write_text("untracked too\n", encoding="utf-8")

    assert tower_state(capsys)["overlaps"] == [
        {"worktrees": ["feat-1", "feat-2"], "files": ["new file.md", "shared.py"]}
    ]


def test_a_branch_stacked_on_another_overlaps_only_on_files_it_changes_itself(
    repo: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    lower = repo.add_worktree("feat-1", commits=0)
    commit_file(lower, "api.py")
    upper = repo.main / "worktrees" / "feat-2"
    git(repo.main, "worktree", "add", "-q", "-b", "feat-2", str(upper), "feat-1")
    commit_file(upper, "ui.py")

    assert tower_state(capsys)["overlaps"] == []  # api.py is in both branches, but only feat-1 changed it

    commit_file(upper, "api.py", "changed again on top\n")
    assert tower_state(capsys)["overlaps"] == []  # built on feat-1's version: nothing to conflict with yet

    commit_file(lower, "api.py", "feat-1 changes it after feat-2 split off\n")
    assert tower_state(capsys)["overlaps"] == [{"worktrees": ["feat-1", "feat-2"], "files": ["api.py"]}]


def test_branches_cut_from_the_same_integration_branch_dont_overlap_on_its_commits(
    repo: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    git(repo.main, "branch", "integration")
    integration = repo.main / "worktrees" / "integration"
    git(repo.main, "worktree", "add", "-q", str(integration), "integration")
    commit_file(integration, "core.py")  # not on main (the base) yet
    git(repo.main, "worktree", "remove", str(integration))
    for name, own in (("feat-1", "a.py"), ("feat-2", "b.py")):
        path = repo.main / "worktrees" / name
        git(repo.main, "worktree", "add", "-q", "-b", name, str(path), "integration")
        commit_file(path, own)

    assert tower_state(capsys)["overlaps"] == []


def test_a_file_renamed_in_one_worktree_and_edited_in_another_is_flagged(
    repo: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    commit_file(repo.main, "old.py", "".join(f"line {n}\n" for n in range(20)))
    one = repo.add_worktree("feat-1", commits=0)
    two = repo.add_worktree("feat-2", commits=0)
    git(one, "mv", "old.py", "new.py")
    git(one, "commit", "-q", "-m", "rename")
    commit_file(two, "old.py", "edited\n")

    assert tower_state(capsys)["overlaps"] == [{"worktrees": ["feat-1", "feat-2"], "files": ["old.py"]}]


def test_the_hub_lists_an_overlap_under_housekeeping_with_the_first_files(
    repo: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    one = repo.add_worktree("feat-1", commits=0)
    two = repo.add_worktree("feat-2", commits=0)
    for n in range(7):
        commit_file(one, f"f{n}.py")
        commit_file(two, f"f{n}.py", "other\n")

    assert cli.main(["state"]) == 0
    text = capsys.readouterr().out

    housekeeping = text[text.index("HOUSEKEEPING") :]
    assert "- feat-1 | changes the same files as feat-2: f0.py, f1.py, f2.py, f3.py, f4.py and 2 more" in housekeeping


def test_the_main_checkout_is_never_compared(repo: Fixture, capsys: pytest.CaptureFixture[str]) -> None:
    one = repo.add_worktree("feat-1", commits=0)
    commit_file(one, "app.py")
    (repo.main / "app.py").write_text("uncommitted on main\n", encoding="utf-8")

    assert tower_state(capsys)["overlaps"] == []
