"""Tests for stacks in `tower state`: git-spice's tracking read into a tree, bottom first, with what needs a restack.

Each test tracks branches the way git-spice does, by writing its tracking ref (`refs/spice/data`) with git plumbing,
so no test needs git-spice installed; test_git_spice_contract.py checks that format against the real git-spice.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from conftest import Fixture, git, track
from test_state import fake_gh_pr_list

from tower import cli


def stack_worktree(repo: Fixture, *branches: str) -> Path:
    """One worktree holding a stack: the first branch from main, each next one on top of the last, one commit each;
    the worktree ends on the top branch."""
    path = repo.add_worktree(branches[0])
    for branch in branches[1:]:
        git(path, "checkout", "-q", "-b", branch)
        git(path, "commit", "-q", "--allow-empty", "-m", branch)
    return path


def stacks(capsys: pytest.CaptureFixture[str], *args: str) -> list[dict[str, object]]:
    """`tower state --json`'s stacks."""
    assert cli.main(["state", "--json", *args]) == 0
    found: list[dict[str, object]] = json.loads(capsys.readouterr().out)["stacks"]
    return found


def branch_rows(stack: dict[str, object]) -> list[dict[str, object]]:
    """A stack's branches, in the order tower lists them."""
    rows = stack["branches"]
    assert isinstance(rows, list)
    return rows


def test_a_three_branch_stack_in_one_worktree_is_listed_bottom_first(
    repo: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    path = stack_worktree(repo, "feat-a", "feat-b", "feat-c")
    track(repo.main, {"feat-c": ("feat-b", None), "feat-a": ("main", None), "feat-b": ("feat-a", None)})

    (stack,) = stacks(capsys)

    assert stack["base"] == "main"
    assert stack["worktrees"] == [str(path)]
    assert [(b["name"], b["parent"], b["depth"]) for b in branch_rows(stack)] == [
        ("feat-a", "main", 0),
        ("feat-b", "feat-a", 1),
        ("feat-c", "feat-b", 2),
    ]
    assert [b["worktree"] for b in branch_rows(stack)] == [None, None, str(path)]
    assert [b["restack"] for b in branch_rows(stack)] == ["", "", ""]


def test_a_branch_whose_parent_was_amended_needs_a_restack_but_the_base_moving_flags_nothing(
    repo: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    path = stack_worktree(repo, "feat-a", "feat-b", "feat-c")
    track(repo.main, {"feat-a": ("main", None), "feat-b": ("feat-a", None), "feat-c": ("feat-b", None)})
    git(repo.main, "commit", "-q", "--allow-empty", "-m", "main moves on")
    git(path, "checkout", "-q", "feat-a")
    git(path, "commit", "-q", "--amend", "--allow-empty", "-m", "feat-a amended")

    (stack,) = stacks(capsys)

    assert [(b["name"], b["restack"]) for b in branch_rows(stack)] == [
        ("feat-a", ""),
        ("feat-b", "its parent feat-a moved"),
        ("feat-c", ""),
    ]


def merge_on_origin(repo: Fixture, branch: str) -> None:
    """What a fetch shows after `tower merge` merged branch's stacked PR: origin's main gained a merge commit of it."""
    git(repo.main, "checkout", "-q", "--detach", "main")
    git(repo.main, "merge", "-q", "--no-ff", "-m", f"Merge {branch}", branch)
    git(repo.main, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(repo.main, "checkout", "-q", "main")


def test_the_bottom_submitted_branch_is_next_to_merge_and_a_merged_parent_needs_a_restack_above_it(
    repo: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    stack_worktree(repo, "feat-a", "feat-b", "feat-c")
    git(repo.main, "update-ref", "refs/remotes/origin/main", "main")
    git(repo.main, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    track(repo.main, {"feat-a": ("main", 11), "feat-b": ("feat-a", 12), "feat-c": ("feat-b", 13)})

    (stack,) = stacks(capsys)
    assert stack["merge_key"] == "merge_to_main"
    assert [(b["name"], b["pr"], b["merged"], b["next"]) for b in branch_rows(stack)] == [
        ("feat-a", 11, False, True),
        ("feat-b", 12, False, False),
        ("feat-c", 13, False, False),
    ]

    merge_on_origin(repo, "feat-a")

    (stack,) = stacks(capsys)
    assert [(b["name"], b["merged"], b["next"], b["restack"]) for b in branch_rows(stack)] == [
        ("feat-a", True, False, ""),
        ("feat-b", False, True, "its parent feat-a merged"),
        ("feat-c", False, False, ""),
    ]


def test_a_branch_never_submitted_isnt_merged_even_when_main_already_has_its_commit(
    repo: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    """A branch just made with git-spice branch create has no commits of its own yet: on main, but not merged."""
    path = repo.add_worktree("feat-a", commits=0)
    git(path, "checkout", "-q", "-b", "feat-b")
    track(repo.main, {"feat-a": ("main", None), "feat-b": ("feat-a", None)})

    (stack,) = stacks(capsys)

    assert [(b["name"], b["merged"], b["next"]) for b in branch_rows(stack)] == [
        ("feat-a", False, True),
        ("feat-b", False, False),
    ]


def test_with_prs_read_from_github_their_state_decides_merged(
    repo: Fixture, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stack_worktree(repo, "feat-a", "feat-b")
    track(repo.main, {"feat-a": ("main", None), "feat-b": ("feat-a", None)})
    fake_gh_pr_list(
        tmp_path,
        [
            {"number": 21, "state": "MERGED", "headRefName": "feat-a", "url": "u", "isCrossRepository": False},
            {"number": 22, "state": "OPEN", "headRefName": "feat-b", "url": "u", "isCrossRepository": False},
        ],
    )

    (stack,) = stacks(capsys, "--pr")

    assert [(b["name"], b["pr"], b["pr_state"], b["merged"], b["next"], b["restack"]) for b in branch_rows(stack)] == [
        ("feat-a", 21, "MERGED", True, False, ""),
        ("feat-b", 22, "OPEN", False, True, "its parent feat-a merged"),
    ]


def test_a_repo_without_git_spice_has_no_stacks_and_tower_never_creates_its_tracking(
    repo: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    stack_worktree(repo, "feat-a", "feat-b")

    assert cli.main(["state", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)

    assert (result["stacks"], result["notes"]) == ([], [])
    assert git(repo.main, "for-each-ref", "refs/spice") == ""


def test_one_tracked_branch_alone_is_not_a_stack_and_a_deleted_branch_drops_out(
    repo: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    stack_worktree(repo, "feat-a", "feat-b")
    repo.add_worktree("feat-x")
    track(repo.main, {"feat-a": ("main", None), "feat-b": ("feat-a", None), "feat-x": ("main", None)})
    git(repo.main, "worktree", "remove", "--force", str(repo.main / "worktrees" / "feat-a"))
    git(repo.main, "branch", "-q", "-D", "feat-b")

    assert stacks(capsys) == []


def test_a_tracking_layout_tower_doesnt_know_is_noted_not_guessed(
    repo: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    stack_worktree(repo, "feat-a", "feat-b")
    track(repo.main, {"feat-a": ("main", None), "feat-b": ("feat-a", None)})
    index = {**os.environ, "GIT_INDEX_FILE": str(repo.main / ".git" / "spice-test-index")}
    two = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"], cwd=repo.main, input="2", capture_output=True, text=True, check=True
    ).stdout.strip()
    subprocess.run(
        ["git", "update-index", "--cacheinfo", f"100644,{two},version"], cwd=repo.main, env=index, check=True
    )
    tree = subprocess.run(["git", "write-tree"], cwd=repo.main, env=index, capture_output=True, text=True, check=True)
    git(
        repo.main,
        "update-ref",
        "refs/spice/data",
        git(repo.main, "commit-tree", tree.stdout.strip(), "-m", "v2").strip(),
    )

    assert cli.main(["state", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)

    assert result["stacks"] == []
    assert (
        "git-spice's tracking is in layout version 2, which tower doesn't read, so stacks weren't shown"
        in result["notes"]
    )


def test_the_hub_reads_the_tree_in_merge_order_with_who_merges_next_and_a_housekeeping_line_per_restack(
    repo: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    repo.tower_dir.mkdir(exist_ok=True)
    (repo.tower_dir / "policy.toml").write_text('default_profile = "work"\n', encoding="utf-8")
    path = stack_worktree(repo, "feat-a", "feat-b", "feat-c")
    track(repo.main, {"feat-a": ("main", 11), "feat-b": ("feat-a", 12), "feat-c": ("feat-b", None)})
    git(path, "checkout", "-q", "feat-a")
    git(path, "commit", "-q", "--amend", "--allow-empty", "-m", "feat-a amended")

    assert cli.main(["state"]) == 0
    text = capsys.readouterr().out

    section = text[text.index("== STACKS") :].split("\n\n")[0].splitlines()
    assert section == [
        "== STACKS (git-spice; merge order, bottom first) ==",
        f"- stack on main | next to merge: feat-a, PR #11 (merge_to_main is ask: it waits for you) | {path}",
        "  1. feat-a | PR #11 | next to merge | checked out",
        "    2. feat-b | PR #12 | needs restack: its parent feat-a moved",
        "      3. feat-c | no PR",
    ]
    assert "- feat-a | feat-b needs a restack (its parent feat-a moved): run git-spice stack restack there" in text


def test_a_three_pr_stack_goes_from_creation_to_its_bottom_pr_waiting_for_you(
    repo: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    """The ticket's done-when (#32): the hub's STACKS section is right at every step, ending on "waits for you"
    under a profile where merges into main ask (work)."""
    repo.tower_dir.mkdir(exist_ok=True)
    (repo.tower_dir / "policy.toml").write_text('default_profile = "work"\n', encoding="utf-8")

    def section() -> list[str]:
        """The STACKS section of `tower state`, without its heading."""
        assert cli.main(["state"]) == 0
        text = capsys.readouterr().out
        return text[text.index("== STACKS") :].split("\n\n")[0].splitlines()[1:]

    path = repo.add_worktree("feat-a")
    track(repo.main, {"feat-a": ("main", None)})
    assert section() == ["(none)"]  # one tracked branch alone is an ordinary PR

    git(path, "checkout", "-q", "-b", "feat-b")
    git(path, "commit", "-q", "--allow-empty", "-m", "feat-b")
    track(repo.main, {"feat-a": ("main", None), "feat-b": ("feat-a", None)})
    assert section() == [
        f"- stack on main | next to merge: feat-a, no PR yet | {path}",
        "  1. feat-a | no PR | next to merge",
        "    2. feat-b | no PR | checked out",
    ]

    git(path, "checkout", "-q", "-b", "feat-c")
    git(path, "commit", "-q", "--allow-empty", "-m", "feat-c")
    track(repo.main, {"feat-a": ("main", None), "feat-b": ("feat-a", None), "feat-c": ("feat-b", None)})
    assert section()[1:] == [
        "  1. feat-a | no PR | next to merge",
        "    2. feat-b | no PR",
        "      3. feat-c | no PR | checked out",
    ]

    # git-spice stack submit opened #11, #12 and #13: the bottom one waits for the user's merge.
    track(repo.main, {"feat-a": ("main", 11), "feat-b": ("feat-a", 12), "feat-c": ("feat-b", 13)})
    assert section() == [
        f"- stack on main | next to merge: feat-a, PR #11 (merge_to_main is ask: it waits for you) | {path}",
        "  1. feat-a | PR #11 | next to merge",
        "    2. feat-b | PR #12",
        "      3. feat-c | PR #13 | checked out",
    ]


def test_a_parent_deleted_before_git_spice_noticed_leaves_its_child_on_the_stacks_base_needing_a_restack(
    repo: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    path = stack_worktree(repo, "feat-a", "feat-b", "feat-c")
    git(repo.main, "update-ref", "refs/remotes/origin/main", "main")
    git(repo.main, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    track(repo.main, {"feat-a": ("main", 11), "feat-b": ("feat-a", 12), "feat-c": ("feat-b", 13)})
    git(path, "checkout", "-q", "feat-c")
    git(repo.main, "branch", "-q", "-D", "feat-a")  # merged on GitHub, deleted by hand before git-spice repo sync

    (stack,) = stacks(capsys)

    assert (stack["base"], stack["merge_key"]) == ("main", "merge_to_main")
    assert [(b["name"], b["parent"], b["next"], b["restack"]) for b in branch_rows(stack)] == [
        ("feat-b", "feat-a", True, "its parent feat-a is gone (merged, or deleted)"),
        ("feat-c", "feat-b", False, ""),
    ]


def test_a_stack_worktree_on_its_merged_bottom_branch_is_never_ready_to_clean(
    repo: Fixture, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = stack_worktree(repo, "feat-a", "feat-b")
    git(path, "checkout", "-q", "feat-a")
    track(repo.main, {"feat-a": ("main", 11), "feat-b": ("feat-a", 12)})
    head = git(path, "rev-parse", "feat-a").strip()
    pr = {"url": "u", "isCrossRepository": False}
    fake_gh_pr_list(
        tmp_path,
        [
            {"number": 11, "state": "MERGED", "headRefName": "feat-a", "headRefOid": head, **pr},
            {"number": 12, "state": "OPEN", "headRefName": "feat-b", **pr},
        ],
    )
    repo.write_card("feat-a", "done")

    assert cli.main(["state", "--json", "--pr"]) == 0
    (row,) = [r for r in json.loads(capsys.readouterr().out)["worktrees"] if r["path"] == str(path)]

    assert (row["status"], row["reason"]) == (
        "done",
        "the card says done; PR #11 is merged, but its stack has unmerged branches: feat-b",
    )


def test_a_middle_branch_deleted_by_hand_keeps_one_stack_with_the_branch_above_reattached(
    repo: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    stack_worktree(repo, "feat-a", "feat-b", "feat-c", "feat-d")
    track(
        repo.main,
        {"feat-a": ("main", 11), "feat-b": ("feat-a", 12), "feat-c": ("feat-b", 13), "feat-d": ("feat-c", 14)},
    )
    git(repo.main, "branch", "-q", "-D", "feat-b")

    (stack,) = stacks(capsys)

    assert (stack["base"], stack["merge_key"]) == ("main", "merge_to_main")
    assert [(b["name"], b["depth"], b["next"], b["restack"]) for b in branch_rows(stack)] == [
        ("feat-a", 0, True, ""),
        ("feat-c", 1, False, "its parent feat-b is gone (merged, or deleted)"),
        ("feat-d", 2, False, ""),
    ]


def test_a_closed_bottom_pr_is_never_next_to_merge(
    repo: Fixture, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stack_worktree(repo, "feat-a", "feat-b")
    track(repo.main, {"feat-a": ("main", 11), "feat-b": ("feat-a", 12)})
    pr = {"url": "u", "isCrossRepository": False}
    fake_gh_pr_list(
        tmp_path,
        [
            {"number": 11, "state": "CLOSED", "headRefName": "feat-a", **pr},
            {"number": 12, "state": "OPEN", "headRefName": "feat-b", **pr},
        ],
    )

    assert cli.main(["state", "--pr"]) == 0
    text = capsys.readouterr().out

    assert "- stack on main | nothing can merge now |" in text
    assert "next to merge |" not in text and "| next to merge" not in text


def test_a_deleted_parent_is_fixed_with_a_repo_sync_and_git_spices_own_remote_says_what_merged(
    repo: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    stack_worktree(repo, "feat-a", "feat-b", "feat-c")
    track(repo.main, {"feat-a": ("main", 11), "feat-b": ("feat-a", 12), "feat-c": ("feat-b", 13)}, remote="upstream")
    git(repo.main, "merge", "-q", "--no-ff", "-m", "Merge feat-a", "feat-a")
    git(repo.main, "update-ref", "refs/remotes/upstream/main", "main")
    git(repo.main, "reset", "-q", "--hard", "main~1")  # the local main hasn't pulled the merge

    assert cli.main(["state"]) == 0
    text = capsys.readouterr().out
    assert (
        "feat-b needs a restack (its parent feat-a merged): run git-spice repo sync, then git-spice stack restack"
        in text
    )

    git(repo.main, "branch", "-q", "-D", "feat-a")
    assert cli.main(["state"]) == 0
    text = capsys.readouterr().out
    assert "feat-b needs a restack (its parent feat-a is gone (merged, or deleted)): run git-spice repo sync" in text


def test_without_github_a_done_card_on_a_merged_bottom_branch_isnt_ready_to_clean_while_its_stack_is_open(
    repo: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    """What the pane's refresh and the hub's scan see (no --pr): tower merge merged feat-a with a merge commit."""
    path = stack_worktree(repo, "feat-a", "feat-b")
    git(path, "checkout", "-q", "feat-a")
    track(repo.main, {"feat-a": ("main", 11), "feat-b": ("feat-a", 12)})
    git(repo.main, "merge", "-q", "--no-ff", "-m", "Merge feat-a", "feat-a")
    repo.write_card("feat-a", "done")

    assert cli.main(["state", "--json"]) == 0
    (row,) = [r for r in json.loads(capsys.readouterr().out)["worktrees"] if r["path"] == str(path)]

    assert (row["status"], row["reason"]) == ("done", "the card says done, but its stack has unmerged branches: feat-b")


def test_an_old_merged_pr_on_a_reused_branch_name_isnt_the_stacked_branchs_pr(
    repo: Fixture, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stack_worktree(repo, "feat-a", "feat-b")
    track(repo.main, {"feat-a": ("main", None), "feat-b": ("feat-a", None)})
    old_head = git(repo.main, "rev-parse", "main").strip()  # earlier work's head, which this branch's tip isn't in
    pr = {"url": "u", "isCrossRepository": False}
    fake_gh_pr_list(tmp_path, [{"number": 3, "state": "MERGED", "headRefName": "feat-a", "headRefOid": old_head, **pr}])

    (stack,) = stacks(capsys, "--pr")

    assert [(b["name"], b["pr"], b["merged"], b["next"], b["restack"]) for b in branch_rows(stack)] == [
        ("feat-a", None, False, True, ""),
        ("feat-b", None, False, False, ""),
    ]


def test_a_merged_pr_git_spice_recorded_is_the_stacked_branchs_even_when_github_rebased_it(
    repo: Fixture, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """GitHub rebased feat-a's PR and merged it, so the local feat-a isn't in its head: earlier work, by the worktree's
    PR rule alone. git-spice recorded that PR for feat-a, so the stack still counts it merged."""
    stack_worktree(repo, "feat-a", "feat-b")
    track(repo.main, {"feat-a": ("main", 11), "feat-b": ("feat-a", 12)})
    git(repo.main, "commit", "-q", "--allow-empty", "-m", "feat-a, rebased by GitHub")
    rebased = git(repo.main, "rev-parse", "main").strip()
    pr = {"url": "u", "isCrossRepository": False}
    fake_gh_pr_list(
        tmp_path,
        [
            {"number": 11, "state": "MERGED", "headRefName": "feat-a", "headRefOid": rebased, **pr},
            {"number": 12, "state": "OPEN", "headRefName": "feat-b", **pr},
        ],
    )

    (stack,) = stacks(capsys, "--pr")

    assert [(b["name"], b["pr"], b["merged"], b["next"]) for b in branch_rows(stack)] == [
        ("feat-a", 11, True, False),
        ("feat-b", 12, False, True),
    ]


def test_a_branch_in_no_stack_is_never_held_open_by_another_stack(repo: Fixture) -> None:
    from tower import stacks as stacks_module

    stack_worktree(repo, "feat-a", "feat-b")
    repo.add_worktree("feat-x")
    track(repo.main, {"feat-a": ("main", 11), "feat-b": ("feat-a", 12), "feat-x": ("main", 31)})

    assert stacks_module.open_besides(repo.main, "feat-x") == ([], "")
    assert stacks_module.open_besides(repo.main, "feat-untracked") == ([], "")
    assert stacks_module.open_besides(repo.main, "feat-a") == (["feat-b"], "")


def test_a_closed_pr_never_holds_its_stack_worktree_open(
    repo: Fixture, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = stack_worktree(repo, "feat-a", "feat-b")
    git(path, "checkout", "-q", "feat-a")
    track(repo.main, {"feat-a": ("main", 11), "feat-b": ("feat-a", 12)})
    head = git(path, "rev-parse", "feat-a").strip()
    pr = {"url": "u", "isCrossRepository": False}
    fake_gh_pr_list(
        tmp_path,
        [
            {"number": 11, "state": "MERGED", "headRefName": "feat-a", "headRefOid": head, **pr},
            {"number": 12, "state": "CLOSED", "headRefName": "feat-b", **pr},
        ],
    )

    assert cli.main(["state", "--json", "--pr"]) == 0
    (row,) = [r for r in json.loads(capsys.readouterr().out)["worktrees"] if r["path"] == str(path)]

    assert row["status"] == "ready-to-clean"


def test_a_pr_github_rebased_and_merged_counts_as_merged_without_github(
    repo: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    """GitHub rebased feat-b onto main after feat-a merged, then merged it: the local feat-b never landed, its copy on
    origin did."""
    path = stack_worktree(repo, "feat-a", "feat-b", "feat-c")
    track(repo.main, {"feat-a": ("main", 11), "feat-b": ("feat-a", 12), "feat-c": ("feat-b", 13)})
    git(repo.main, "checkout", "-q", "--detach", "main")
    git(repo.main, "commit", "-q", "--allow-empty", "-m", "feat-a, squashed by GitHub")
    git(repo.main, "commit", "-q", "--allow-empty", "-m", "feat-b, rebased by GitHub")
    git(repo.main, "update-ref", "refs/remotes/origin/feat-b", "HEAD")
    git(repo.main, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(repo.main, "checkout", "-q", "main")
    git(path, "checkout", "-q", "feat-c")

    (stack,) = stacks(capsys)

    assert [(b["name"], b["merged"]) for b in branch_rows(stack)] == [
        ("feat-a", False),  # squashed: nothing on origin shows it, so it stays unmerged without --pr
        ("feat-b", True),
        ("feat-c", False),
    ]


def test_a_tracked_worktree_whose_stack_cant_be_read_is_never_offered_for_cleanup(
    repo: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    from conftest import unreadable_tracking

    path = repo.add_worktree("feat-a")
    git(repo.main, "merge", "-q", "--no-ff", "-m", "Merge feat-a", "feat-a")
    unreadable_tracking(repo.main, "feat-a")
    repo.write_card("feat-a", "done")

    assert cli.main(["state", "--json"]) == 0
    (row,) = [r for r in json.loads(capsys.readouterr().out)["worktrees"] if r["path"] == str(path)]

    assert (row["status"], row["reason"]) == (
        "done",
        "the card says done, but whether its git-spice stack is finished couldn't be checked",
    )
