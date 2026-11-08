"""Tests for src/tower/clean.py (`tower clean`): a real throwaway repo with worktrees and a ledger (conftest.py's
fixture, whose fake `claude` lists the sessions a test chose), FakeGitHub from test_merge.py answering the gh calls,
and FakeOrca standing in for Orca's CLI."""

from __future__ import annotations

import math
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from conftest import Fixture, git, interactive
from test_merge import REPO, Clock, FakeGitHub, FakePull, ok

from tower import clean, cli, gitrepo, merge, state
from tower.merge import GhResult


@dataclass
class CleanGitHub(FakeGitHub):
    """FakeGitHub that also answers `gh pr list --head BRANCH`, as tower clean asks for a worktree's PR (or fails it
    with pr_list_error)."""

    pr_list_error: str = ""

    def __call__(self, args: list[str]) -> GhResult:
        if args[:2] == ["pr", "list"]:
            self.calls.append(args)
            if self.pr_list_error:
                return GhResult(1, "", self.pr_list_error)
            head = args[args.index("--head") + 1]
            return ok(
                [
                    {
                        "number": p.number,
                        "state": "MERGED" if p.merged else p.state.upper(),
                        "headRefOid": p.sha,
                        "url": f"https://github.com/{REPO}/pull/{p.number}",
                        "isCrossRepository": p.head_repo != REPO,
                    }
                    for p in self.pulls
                    if p.head == head
                ]
            )
        return super().__call__(args)


@dataclass
class FakeOrca:
    """Orca's CLI for the commands tower clean runs. `worktree rm` really removes the git worktree (as Orca does),
    refusing a dirty one; it never deletes the branch, as Orca keeps a squash-merged branch it can't prove merged.
    With removes_after, `worktree rm` answers at once and removes the worktree only once that many seconds of the
    fake clock pass (time_passes), as Orca 1.4.237 does; math.inf never removes it."""

    main: Path
    reachable: bool = True
    known: bool = True  # Orca manages every worktree of the repo
    rm_error: str = ""
    list_error: str = ""  # `orca worktree list` fails with this while Orca runs
    status_times_out: bool = False
    removes_after: float = 0.0
    pending: list[tuple[str, float]] = field(default_factory=list)  # (path, seconds left) of a late removal
    calls: list[list[str]] = field(default_factory=list)

    def time_passes(self, seconds: float) -> None:
        """Let seconds pass, finishing each late removal that is due."""
        self.pending = [(path, left - seconds) for path, left in self.pending]
        for path in [path for path, left in self.pending if left <= 0]:
            git(self.main, "worktree", "remove", path)
        self.pending = [(path, left) for path, left in self.pending if left > 0]

    def __call__(self, args: list[str]) -> GhResult:
        self.calls.append(args)
        if args[:1] == ["status"] and self.status_times_out:
            return GhResult(124, "", "orca status timed out after 60s")
        if args[:1] == ["status"]:
            return ok({"ok": True, "result": {"runtime": {"reachable": self.reachable}}})
        if not self.reachable:
            return GhResult(1, "", "orca runtime isn't reachable")
        if args[:2] == ["worktree", "list"] and self.list_error:
            return ok({"ok": False, "error": {"code": "timeout", "message": self.list_error}})
        if args[:2] == ["worktree", "list"]:
            paths = git(self.main, "worktree", "list", "--porcelain").split("\n")
            listed = [{"path": line[len("worktree ") :]} for line in paths if line.startswith("worktree ")]
            return ok({"ok": True, "result": {"worktrees": listed if self.known else []}})
        if args[:2] == ["terminal", "close"]:
            return ok({"ok": True, "result": {"closed": 1}})
        if args[:2] == ["worktree", "rm"]:
            if self.rm_error:
                return ok({"ok": False, "error": {"code": "failed", "message": self.rm_error}})
            path = args[args.index("--worktree") + 1].removeprefix("path:")
            if self.removes_after:
                self.pending.append((path, self.removes_after))
            else:
                git(self.main, "worktree", "remove", path)
            return ok({"ok": True, "result": {"removed": True}})
        raise AssertionError(f"unexpected orca call: {args}")


@dataclass
class Merged:
    """A finished worker: its worktree, branch, and the fakes that know its merged PR."""

    path: Path
    branch: str
    sha: str
    github: CleanGitHub
    orca: FakeOrca


def merged_worker(repo: Fixture, branch: str = "feat-1", number: int = 1) -> Merged:
    """A worktree whose PR merged at its HEAD, with a done card and its branch still on GitHub."""
    path = repo.add_worktree(branch)
    sha = git(path, "rev-parse", "HEAD").strip()
    repo.write_card(branch, "done")
    github = CleanGitHub([FakePull(number, base="main", head=branch, sha=sha, state="closed", merged=True)])
    return Merged(path, branch, sha, github, FakeOrca(repo.main))


def run_clean(repo: Fixture, worker: Merged, *names: str, every: bool = False, dry_run: bool = False) -> list[str]:
    """Run tower clean from the main checkout; the lines it said, with its result as the last line."""
    said: list[str] = []
    clock = Clock()

    def sleep(seconds: float) -> None:
        """Move the fake clock, letting Orca finish a late removal that is due."""
        clock.sleep(seconds)
        worker.orca.time_passes(seconds)

    done = clean.clean(
        repo.main,
        list(names),
        every=every,
        dry_run=dry_run,
        gh=worker.github,
        orca=worker.orca,
        say=said.append,
        here=repo.main,
        sleep=sleep,
        clock=lambda: clock.now,
    )
    return [*said, f"result: {done}"]


def worktree_paths(repo: Fixture) -> list[str]:
    """Every worktree git lists."""
    out = git(repo.main, "worktree", "list", "--porcelain")
    return [line[len("worktree ") :] for line in out.splitlines() if line.startswith("worktree ")]


def branch_exists(repo: Fixture, branch: str) -> bool:
    """Whether the local branch exists."""
    return branch in git(repo.main, "branch", "--format=%(refname:short)").split()


def test_a_merged_worker_is_cleaned_up_in_one_step(repo: Fixture) -> None:
    worker = merged_worker(repo)

    said = run_clean(repo, worker, "feat-1")

    assert said[-1] == "result: True"
    assert str(worker.path) not in worktree_paths(repo)
    assert not branch_exists(repo, "feat-1")
    assert "feat-1" not in worker.github.branches  # deleted on GitHub
    assert not (repo.ledger / "feat-1.md").exists()
    assert (repo.ledger / "_archive" / "feat-1.md").read_text(encoding="utf-8").startswith("---\nstatus: done")
    assert state.gather(repo.main).orphan_cards == []  # an archived card is history, not an orphan
    assert ["terminal", "close", "--worktree", f"path:{worker.path}", "--all", "--json"] in worker.orca.calls
    assert ["worktree", "rm", "--worktree", f"path:{worker.path}", "--json"] in worker.orca.calls


def open_pr(repo: Fixture, worker: Merged) -> None:
    worker.github.pulls.append(FakePull(2, base="main", head=worker.branch, sha=worker.sha))


def no_merged_pr(repo: Fixture, worker: Merged) -> None:
    worker.github.pulls[0].merged = False


def commit_after_merge(repo: Fixture, worker: Merged) -> None:
    git(worker.path, "commit", "-q", "--allow-empty", "-m", "after the merge")


def uncommitted_file(repo: Fixture, worker: Merged) -> None:
    (worker.path / "notes.txt").write_text("not committed\n", encoding="utf-8")


def stash_entry(repo: Fixture, worker: Merged) -> None:
    (worker.path / "notes.txt").write_text("stashed\n", encoding="utf-8")
    git(worker.path, "stash", "push", "-u", "-m", "half done")


def busy_session(repo: Fixture, worker: Merged) -> None:
    repo.sessions(interactive("w1", worker.path, "busy"))


def waiting_session(repo: Fixture, worker: Merged) -> None:
    repo.sessions(interactive("w1", worker.path / "src", "waiting", waitingFor="approve Bash"))


def running_card(repo: Fixture, worker: Merged) -> None:
    repo.write_card(worker.branch, "running")


def sessions_unreadable(repo: Fixture, worker: Merged) -> None:
    repo.agents_json.write_text("not json", encoding="utf-8")


@pytest.mark.parametrize(
    ("break_it", "failure"),
    [
        (open_pr, "PR #2 on feat-1 is open, not merged"),
        (no_merged_pr, "feat-1 has no merged PR"),
        (commit_after_merge, "isn't PR #1's head"),
        (uncommitted_file, "it has 1 uncommitted or untracked file"),
        (stash_entry, "1 stash entry was made on feat-1: On feat-1: half done"),
        (busy_session, "session w1 is busy there"),
        (waiting_session, "session w1 is waiting on you there"),
        (running_card, "its card says running, so work may go on there"),
        (sessions_unreadable, "a session busy or waiting there can't be ruled out"),
    ],
)
def test_each_failed_check_is_said_and_leaves_everything_in_place(
    repo: Fixture, break_it: Callable[[Fixture, Merged], None], failure: str
) -> None:
    worker = merged_worker(repo)
    break_it(repo, worker)
    card_before = (repo.ledger / "feat-1.md").read_text(encoding="utf-8")

    said = run_clean(repo, worker, "feat-1")

    assert said[-1] == "result: False"
    assert said[0] == "feat-1: left as it is, since 1 check(s) failed:"
    assert failure in said[1]
    assert_untouched(repo, worker, card_before)


def assert_untouched(repo: Fixture, worker: Merged, card_before: str) -> None:
    """The worktree, its branch here and on GitHub, and its card are all as they were, and Orca removed nothing."""
    assert str(worker.path) in worktree_paths(repo)
    assert branch_exists(repo, worker.branch)
    assert worker.branch in worker.github.branches
    assert (repo.ledger / f"{worker.branch}.md").read_text(encoding="utf-8") == card_before
    assert not (repo.ledger / "_archive").exists()
    assert not [call for call in worker.orca.calls if call[:2] in (["worktree", "rm"], ["terminal", "close"])]
    assert not [call for call in worker.github.calls if "DELETE" in call]


def test_running_inside_the_worktree_fails_its_check(repo: Fixture) -> None:
    worker = merged_worker(repo)
    card_before = (repo.ledger / "feat-1.md").read_text(encoding="utf-8")

    said: list[str] = []
    done = clean.clean(repo.main, ["feat-1"], every=False, dry_run=False, gh=worker.github, orca=worker.orca,
                       say=said.append, here=worker.path / "src")  # fmt: skip

    assert not done
    assert "tower clean is running inside it: run it from the main checkout" in said[1]
    assert_untouched(repo, worker, card_before)


def test_every_failed_check_is_listed_not_only_the_first(repo: Fixture) -> None:
    worker = merged_worker(repo)
    commit_after_merge(repo, worker)
    uncommitted_file(repo, worker)
    busy_session(repo, worker)

    said = run_clean(repo, worker, "feat-1")

    assert said[0] == "feat-1: left as it is, since 3 check(s) failed:"
    assert len([line for line in said if line.startswith("  - ")]) == 3


def test_without_orca_it_removes_the_worktree_with_git_and_names_idle_sessions_left_open(repo: Fixture) -> None:
    worker = merged_worker(repo)
    worker.orca.reachable = False
    repo.sessions(interactive("w1", worker.path, "idle"))

    said = run_clean(repo, worker, "feat-1")

    assert said[-1] == "result: True"
    assert str(worker.path) not in worktree_paths(repo)
    assert not branch_exists(repo, "feat-1")
    assert f"feat-1: removed the worktree {worker.path} with git (Orca isn't running or doesn't manage it)" in said
    assert f"feat-1: session(s) w1 still open in {worker.path}, which is gone: close them" in said
    assert worker.orca.calls == [["status", "--json"]]


def test_a_worktree_orca_doesnt_manage_is_removed_with_git(repo: Fixture) -> None:
    worker = merged_worker(repo)
    worker.orca.known = False

    said = run_clean(repo, worker, "feat-1")

    assert said[-1] == "result: True"
    assert str(worker.path) not in worktree_paths(repo)
    assert not [call for call in worker.orca.calls if call[:2] in (["worktree", "rm"], ["terminal", "close"])]


def test_when_orca_cant_remove_the_worktree_its_branch_and_card_stay(repo: Fixture) -> None:
    worker = merged_worker(repo)
    worker.orca.rm_error = "worktree has running processes"
    card_before = (repo.ledger / "feat-1.md").read_text(encoding="utf-8")

    said = run_clean(repo, worker, "feat-1")

    assert said[-1] == "result: False"
    assert (
        "feat-1: Orca closed its terminals but couldn't remove the worktree (worktree has running processes), so"
        " nothing else was removed" in said
    )
    assert not any("git pull" in line or "nothing was pulled" in line for line in said)  # nothing was removed
    assert branch_exists(repo, "feat-1") and "feat-1" in worker.github.branches
    assert (repo.ledger / "feat-1.md").read_text(encoding="utf-8") == card_before


def test_dry_run_says_what_would_go_and_touches_nothing(repo: Fixture) -> None:
    worker = merged_worker(repo)
    card_before = (repo.ledger / "feat-1.md").read_text(encoding="utf-8")

    said = run_clean(repo, worker, "feat-1", dry_run=True)

    assert said == [
        f"feat-1: every check passed (PR #1 merged at {worker.sha[:7]})",
        (
            f"feat-1: would close its terminals and remove it through Orca ({worker.path}, git-ignored files"
            " included), delete feat-1 here and on GitHub (when that's safe), and archive its card feat-1.md"
        ),
        f"would then run git pull --ff-only in {repo.main}",
        "result: True",
    ]
    assert_untouched(repo, worker, card_before)


def test_all_cleans_every_merged_worker_that_passes_and_says_why_the_others_stay(repo: Fixture) -> None:
    worker = merged_worker(repo)
    second = repo.add_worktree("feat-2")
    second_sha = git(second, "rev-parse", "HEAD").strip()
    (second / "notes.txt").write_text("not committed\n", encoding="utf-8")
    repo.add_worktree("feat-3")  # its PR is still open: a running worker, skipped
    worker.github.pulls += [
        FakePull(2, base="main", head="feat-2", sha=second_sha, state="closed", merged=True),
        FakePull(3, base="main", head="feat-3"),
    ]
    worker.github.branches.update({"feat-2": second_sha, "feat-3": "headsha0"})

    said = run_clean(repo, worker, every=True)

    assert said[-1] == "result: False"
    assert "feat-3: skipped, PR #3 on feat-3 is open, not merged" in said
    assert "feat-2: left as it is, since 1 check(s) failed:" in said
    assert str(worker.path) not in worktree_paths(repo)
    assert str(second) in worktree_paths(repo) and branch_exists(repo, "feat-2")
    assert branch_exists(repo, "feat-3")


def test_all_with_no_merged_worker_does_nothing(repo: Fixture) -> None:
    worker = merged_worker(repo)
    no_merged_pr(repo, worker)

    said = run_clean(repo, worker, every=True)

    assert said == [
        "feat-1: skipped, feat-1 has no merged PR",
        "no worktree has a merged PR, so nothing to clean up",
        "result: True",
    ]
    assert str(worker.path) in worktree_paths(repo)


def test_a_card_archived_before_under_the_same_name_is_kept(repo: Fixture) -> None:
    worker = merged_worker(repo)
    (repo.ledger / "_archive").mkdir()
    (repo.ledger / "_archive" / "feat-1.md").write_text("an earlier feat-1\n", encoding="utf-8")

    run_clean(repo, worker, "feat-1")

    archived = sorted(p.name for p in (repo.ledger / "_archive").iterdir())
    assert len(archived) == 2 and archived[0].startswith("feat-1.") and archived[1] == "feat-1.md"
    assert (repo.ledger / "_archive" / "feat-1.md").read_text(encoding="utf-8") == "an earlier feat-1\n"


def test_the_decisions_log_is_archived_with_the_card_so_a_reused_branch_name_starts_empty(repo: Fixture) -> None:
    worker = merged_worker(repo)
    (repo.ledger / "_decisions").mkdir()
    (repo.ledger / "_decisions" / "feat-1.md").write_text("- 2026-11-07T09:00 chose X over Y\n", encoding="utf-8")

    said = run_clean(repo, worker, "feat-1")

    archived = repo.ledger / "_archive" / "feat-1.decisions.md"
    assert f"feat-1: archived its decisions log to {archived}" in said
    assert said[-1] == "result: True"
    assert archived.read_text(encoding="utf-8") == "- 2026-11-07T09:00 chose X over Y\n"
    assert not (repo.ledger / "_decisions" / "feat-1.md").exists()


def test_a_decisions_log_with_no_card_is_archived_too(repo: Fixture) -> None:
    worker = merged_worker(repo)
    (repo.ledger / "feat-1.md").unlink()
    (repo.ledger / "_decisions").mkdir()
    (repo.ledger / "_decisions" / "feat-1.md").write_text("- 2026-11-07T09:00 chose X over Y\n", encoding="utf-8")

    said = run_clean(repo, worker, "feat-1")

    assert f"feat-1: archived its decisions log to {repo.ledger / '_archive' / 'feat-1.decisions.md'}" in said
    assert not (repo.ledger / "_decisions" / "feat-1.md").exists()


def test_the_main_checkout_pulls_the_merged_work(repo: Fixture, tmp_path: Path) -> None:
    origin = tmp_path / "origin.git"
    git(repo.main, "clone", "-q", "--bare", str(repo.main), str(origin))
    git(repo.main, "remote", "add", "origin", str(origin))
    git(repo.main, "fetch", "-q", "origin")
    git(repo.main, "branch", "-q", "--set-upstream-to=origin/main")
    elsewhere = tmp_path / "elsewhere"
    git(tmp_path, "clone", "-q", str(origin), str(elsewhere))
    git(elsewhere, "commit", "-q", "--allow-empty", "-m", "the squash merge")
    git(elsewhere, "push", "-q", "origin", "main")
    worker = merged_worker(repo)

    said = run_clean(repo, worker, "feat-1")

    assert said[-2:] == [f"pulled the merged work into {repo.main} (git pull --ff-only)", "result: True"]
    assert git(repo.main, "log", "-1", "--format=%s").strip() == "the squash merge"


@pytest.mark.parametrize(
    ("names", "message"),
    [
        ([], "name the worktrees to clean up (a path, branch or folder name), or pass --all"),
        (["feat-9"], "no worktree is 'feat-9' (the worktrees besides the main checkout: feat-1)"),
        (["main"], "'main' is the main checkout, which is never cleaned up"),
    ],
)
def test_names_that_dont_resolve_touch_nothing(repo: Fixture, names: list[str], message: str) -> None:
    worker = merged_worker(repo)
    card_before = (repo.ledger / "feat-1.md").read_text(encoding="utf-8")

    with pytest.raises(clean.CleanError, match="^" + message.replace("(", r"\(").replace(")", r"\)") + "$"):
        run_clean(repo, worker, *names)

    assert_untouched(repo, worker, card_before)


def test_a_worktree_can_be_named_by_its_path(repo: Fixture) -> None:
    worker = merged_worker(repo, branch="rishi/feat-4")

    said = run_clean(repo, worker, "worktrees/rishi-feat-4")

    assert said[-1] == "result: True"
    assert (repo.ledger / "_archive" / "rishi__feat-4.md").exists()


@pytest.fixture
def cli_worker(repo: Fixture, monkeypatch: pytest.MonkeyPatch) -> Merged:
    """A merged worker, with `tower clean`'s gh and orca replaced by its fakes."""
    worker = merged_worker(repo)
    monkeypatch.setattr(merge, "run_gh", lambda cwd: worker.github)
    monkeypatch.setattr(clean, "run_orca", lambda cwd: worker.orca)
    return worker


def test_tower_clean_says_each_step_and_exits_0_once_everything_is_removed(
    repo: Fixture, cli_worker: Merged, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["clean", "feat-1"]) == 0

    out = capsys.readouterr().out.splitlines()
    assert out[0] == f"tower: feat-1: every check passed (PR #1 merged at {cli_worker.sha[:7]})"
    assert "tower: feat-1: deleted the local branch feat-1" in out
    assert str(cli_worker.path) not in worktree_paths(repo)


def test_tower_clean_exits_1_when_a_check_fails(
    repo: Fixture, cli_worker: Merged, capsys: pytest.CaptureFixture[str]
) -> None:
    uncommitted_file(repo, cli_worker)

    assert cli.main(["clean", "--dry-run", "feat-1"]) == 1

    assert "tower:   - it has 1 uncommitted or untracked file" in capsys.readouterr().out


def test_tower_clean_exits_1_with_the_reason_for_a_name_it_cant_resolve(
    repo: Fixture, cli_worker: Merged, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["clean", "feat-9"]) == 1

    assert capsys.readouterr().err.startswith("tower: no worktree is 'feat-9'")


def test_tower_clean_takes_names_or_all_not_both(cli_worker: Merged, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exited:
        cli.main(["clean", "--all", "feat-1"])

    assert exited.value.code == 2
    assert "name worktrees or pass --all, not both" in capsys.readouterr().err


def test_a_session_that_turns_busy_after_the_checks_keeps_its_worktree(repo: Fixture) -> None:
    worker = merged_worker(repo)
    card_before = (repo.ledger / "feat-1.md").read_text(encoding="utf-8")
    asked_status = worker.orca.__call__

    def orca(args: list[str]) -> GhResult:
        if args[:1] == ["status"]:  # asked after the checks, before any removal
            repo.sessions(interactive("w1", worker.path, "busy"))
        return asked_status(args)

    said: list[str] = []
    done = clean.clean(repo.main, ["feat-1"], every=False, dry_run=False, gh=worker.github, orca=orca,
                       say=said.append, here=repo.main)  # fmt: skip

    assert not done
    assert "feat-1: left as it is: session w1 is busy there now" in said
    assert not any("git pull" in line or "nothing was pulled" in line for line in said)  # nothing removed
    assert_untouched(repo, worker, card_before)


def test_when_orca_runs_but_cant_list_its_worktrees_nothing_is_removed(repo: Fixture) -> None:
    worker = merged_worker(repo)
    worker.orca.list_error = "runtime timed out"
    card_before = (repo.ledger / "feat-1.md").read_text(encoding="utf-8")

    said = run_clean(repo, worker, "feat-1")

    assert said[-2:] == [
        "Orca couldn't say which worktrees it manages (runtime timed out), so nothing was removed: run it again",
        "result: False",
    ]
    assert_untouched(repo, worker, card_before)


def test_a_worktree_orca_doesnt_manage_names_its_idle_sessions_before_and_after(repo: Fixture) -> None:
    worker = merged_worker(repo)
    worker.orca.known = False
    repo.sessions(interactive("w1", worker.path, "idle"))

    planned = run_clean(repo, worker, "feat-1", dry_run=True)
    said = run_clean(repo, worker, "feat-1")

    assert "(Orca doesn't manage it, so session(s) w1 stay open there)" in planned[1]
    assert f"feat-1: session(s) w1 still open in {worker.path}, which is gone: close them" in said


def test_a_branch_github_couldnt_delete_fails_the_run_after_the_rest_is_done(repo: Fixture) -> None:
    worker = merged_worker(repo)
    worker.github.settings_fail = True  # so whether deleting it is safe can't be read

    said = run_clean(repo, worker, "feat-1")

    assert said[-1] == "result: False"
    assert "feat-1" in worker.github.branches
    assert str(worker.path) not in worktree_paths(repo) and not branch_exists(repo, "feat-1")
    assert (repo.ledger / "_archive" / "feat-1.md").exists()


def test_all_fails_a_worktree_whose_pr_github_couldnt_be_asked_about_instead_of_skipping_it(repo: Fixture) -> None:
    worker = merged_worker(repo)
    worker.github.pr_list_error = "gh: not logged in"

    said = run_clean(repo, worker, every=True)

    assert said[:2] == [
        "feat-1: left as it is, since 1 check(s) failed:",
        "  - couldn't read feat-1's PR from GitHub (gh: not logged in)",
    ]
    assert said[-1] == "result: False"


def test_the_local_branchs_upstream_settings_go_with_it(repo: Fixture) -> None:
    worker = merged_worker(repo)
    git(repo.main, "config", "branch.feat-1.remote", "origin")
    git(repo.main, "config", "branch.feat-1.merge", "refs/heads/feat-1")

    run_clean(repo, worker, "feat-1")

    assert "branch.feat-1" not in git(repo.main, "config", "--list", "--local")


def test_untracked_files_hidden_by_git_config_still_fail_the_check(repo: Fixture) -> None:
    worker = merged_worker(repo)
    git(repo.main, "config", "status.showUntrackedFiles", "no")
    uncommitted_file(repo, worker)
    card_before = (repo.ledger / "feat-1.md").read_text(encoding="utf-8")

    said = run_clean(repo, worker, "feat-1")

    assert "it has 1 uncommitted or untracked file" in said[1]
    assert_untouched(repo, worker, card_before)


def test_an_orca_that_times_out_removes_nothing_rather_than_fall_back_to_git(repo: Fixture) -> None:
    worker = merged_worker(repo)
    worker.orca.status_times_out = True
    card_before = (repo.ledger / "feat-1.md").read_text(encoding="utf-8")

    said = run_clean(repo, worker, "feat-1")

    assert said[-2] == (
        "Orca couldn't say which worktrees it manages (orca status timed out after 60s), so nothing was removed:"
        " run it again"
    )
    assert_untouched(repo, worker, card_before)


def test_a_pull_that_fails_is_said_but_the_cleanup_still_counts_as_done(repo: Fixture, tmp_path: Path) -> None:
    origin = tmp_path / "origin.git"
    git(repo.main, "clone", "-q", "--bare", str(repo.main), str(origin))
    git(repo.main, "remote", "add", "origin", str(origin))
    git(repo.main, "fetch", "-q", "origin")
    git(repo.main, "branch", "-q", "--set-upstream-to=origin/main")
    git(repo.main, "commit", "-q", "--allow-empty", "-m", "local only")  # main has diverged from origin
    elsewhere = tmp_path / "elsewhere"
    git(tmp_path, "clone", "-q", str(origin), str(elsewhere))
    git(elsewhere, "commit", "-q", "--allow-empty", "-m", "the squash merge")
    git(elsewhere, "push", "-q", "origin", "main")
    worker = merged_worker(repo)

    said = run_clean(repo, worker, "feat-1")

    assert said[-1] == "result: True"
    assert said[-2].startswith(f"git pull --ff-only in {repo.main} failed (")
    assert said[-2].endswith("); the cleanup itself is done")


def test_a_worktree_whose_folder_is_gone_says_how_to_forget_it(repo: Fixture) -> None:
    worker = merged_worker(repo)
    shutil.rmtree(worker.path)

    listed = run_clean(repo, worker, every=True)
    named = run_clean(repo, worker, "feat-1")

    hint = "its folder is gone (removed outside tower): git worktree prune in the main checkout forgets it"
    assert listed == [f"feat-1: skipped, {hint}", "no worktree has a merged PR, so nothing to clean up", "result: True"]
    assert named == ["feat-1: left as it is, since 1 check(s) failed:", f"  - {hint}", "result: False"]


def test_a_worktree_orca_removes_a_moment_after_rm_returns_is_cleaned_up(repo: Fixture) -> None:
    worker = merged_worker(repo)
    worker.orca.removes_after = 2.0

    said = run_clean(repo, worker, "feat-1")

    assert said[-1] == "result: True"
    assert f"feat-1: removed the worktree {worker.path} through Orca" in said
    assert str(worker.path) not in worktree_paths(repo)
    assert not branch_exists(repo, "feat-1") and "feat-1" not in worker.github.branches
    assert (repo.ledger / "_archive" / "feat-1.md").exists()


def test_a_worktree_orca_never_removes_stops_the_cleanup_after_a_bounded_wait(repo: Fixture) -> None:
    worker = merged_worker(repo)
    worker.orca.removes_after = math.inf
    card_before = (repo.ledger / "feat-1.md").read_text(encoding="utf-8")

    said = run_clean(repo, worker, "feat-1")

    assert said[-2:] == [
        (
            "feat-1: the worktree is still listed by git after removing it, so nothing else was removed: once git no"
            " longer lists it, tower clean feat-1 finishes the rest"
        ),
        "result: False",
    ]
    assert branch_exists(repo, "feat-1") and "feat-1" in worker.github.branches
    assert (repo.ledger / "feat-1.md").read_text(encoding="utf-8") == card_before


def half_done(repo: Fixture, worker: Merged) -> None:
    """What a cleanup that stopped after Orca's late removal leaves: no worktree, the card still in the Ledger."""
    git(repo.main, "worktree", "remove", str(worker.path))


def test_a_half_done_cleanup_is_finished_by_naming_its_branch(repo: Fixture) -> None:
    worker = merged_worker(repo)
    half_done(repo, worker)

    said = run_clean(repo, worker, "feat-1")

    assert said[-1] == "result: True"
    assert said[0] == f"feat-1: every check passed (PR #1 merged at {worker.sha[:7]}); its worktree is already gone"
    assert "feat-1: deleted the local branch feat-1" in said
    assert not branch_exists(repo, "feat-1") and "feat-1" not in worker.github.branches
    assert (repo.ledger / "_archive" / "feat-1.md").exists() and not (repo.ledger / "feat-1.md").exists()
    assert worker.orca.calls == []  # no worktree, so nothing to ask Orca


def test_a_half_done_cleanup_whose_branches_are_already_gone_archives_the_card(repo: Fixture) -> None:
    worker = merged_worker(repo)
    half_done(repo, worker)
    git(repo.main, "branch", "-q", "-D", "feat-1")
    del worker.github.branches["feat-1"]

    said = run_clean(repo, worker, "feat-1")

    assert said[-1] == "result: True"
    assert "feat-1: feat-1 is already deleted on GitHub" in said
    assert "feat-1: the local branch is already gone" in said
    assert (repo.ledger / "_archive" / "feat-1.md").exists()


def test_dry_run_of_a_half_done_cleanup_lists_the_steps_left_and_touches_nothing(repo: Fixture) -> None:
    worker = merged_worker(repo)
    half_done(repo, worker)

    said = run_clean(repo, worker, "feat-1", dry_run=True)

    assert said == [
        f"feat-1: every check passed (PR #1 merged at {worker.sha[:7]}); its worktree is already gone",
        (
            "feat-1: would delete feat-1 here and on GitHub (when that's safe), and archive its card feat-1.md (its"
            " worktree is already gone)"
        ),
        f"would then run git pull --ff-only in {repo.main}",
        "result: True",
    ]
    assert branch_exists(repo, "feat-1") and "feat-1" in worker.github.branches
    assert (repo.ledger / "feat-1.md").exists() and not (repo.ledger / "_archive").exists()


@pytest.mark.parametrize(
    ("break_it", "failure"),
    [
        (open_pr, "PR #2 on feat-1 is open, not merged"),
        (no_merged_pr, "feat-1 has no merged PR"),
        (commit_after_merge, "its local branch ("),
        (stash_entry, "1 stash entry was made on feat-1: On feat-1: half done"),
        (running_card, "its card says running, so work may go on there"),
    ],
)
def test_a_half_done_cleanup_that_fails_a_check_still_standing_removes_nothing(
    repo: Fixture, break_it: Callable[[Fixture, Merged], None], failure: str
) -> None:
    worker = merged_worker(repo)
    break_it(repo, worker)  # before the worktree goes, so a stash entry can be made on its branch
    half_done(repo, worker)
    card_before = (repo.ledger / "feat-1.md").read_text(encoding="utf-8")

    said = run_clean(repo, worker, "feat-1")

    assert said[:2] == ["feat-1: left as it is, since 1 check(s) failed:", said[1]]
    assert failure in said[1]
    assert said[-1] == "result: False"
    assert branch_exists(repo, "feat-1") and "feat-1" in worker.github.branches
    assert (repo.ledger / "feat-1.md").read_text(encoding="utf-8") == card_before
    assert not [call for call in worker.github.calls if "DELETE" in call]


def test_a_name_with_no_worktree_and_no_card_touches_nothing(repo: Fixture) -> None:
    worker = merged_worker(repo)
    half_done(repo, worker)
    (repo.ledger / "feat-1.md").unlink()

    with pytest.raises(
        clean.CleanError, match=r"^no worktree is 'feat-1' \(the worktrees besides the main checkout: none\)$"
    ):
        run_clean(repo, worker, "feat-1")

    assert branch_exists(repo, "feat-1") and "feat-1" in worker.github.branches


def test_a_local_branch_deleted_while_clean_deletes_it_is_said_as_gone(
    repo: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = merged_worker(repo)
    real_git = gitrepo.git

    def git_racing_orca(cwd: Path, *args: str) -> str | None:
        """Run git, deleting feat-1 first when tower is about to delete it."""
        if args[:1] == ("update-ref",):  # Orca deletes the branch between tower's read and its delete
            real_git(cwd, "update-ref", "-d", "refs/heads/feat-1")
        return real_git(cwd, *args)

    monkeypatch.setattr(clean, "git", git_racing_orca)

    said = run_clean(repo, worker, "feat-1")

    assert "feat-1: the local branch is already gone" in said
    assert said[-1] == "result: True"


@dataclass
class RacingGitHub(CleanGitHub):
    """CleanGitHub where the head branch is deleted (by Orca) just before tower's DELETE, which GitHub then refuses."""

    def __call__(self, args: list[str]) -> GhResult:
        """Answer like CleanGitHub, but a DELETE finds the branch already gone."""
        if "DELETE" in args:
            self.calls.append(args)
            self.branches.pop("feat-1", None)
            return GhResult(1, '{"message":"Reference does not exist"}', "gh: Reference does not exist (HTTP 422)")
        return super().__call__(args)


def test_a_github_branch_deleted_while_clean_deletes_it_is_said_as_gone(repo: Fixture) -> None:
    worker = merged_worker(repo)
    worker.github = RacingGitHub(worker.github.pulls)

    said = run_clean(repo, worker, "feat-1")

    assert "feat-1: feat-1 is already deleted on GitHub" in said
    assert said[-1] == "result: True"


def test_a_half_done_cleanup_isnt_confused_by_branches_under_its_name(repo: Fixture) -> None:
    worker = merged_worker(repo)
    half_done(repo, worker)
    git(repo.main, "branch", "-q", "-D", "feat-1")
    git(repo.main, "branch", "-q", "feat-1/next", "main")  # matches for-each-ref's refs/heads/feat-1 pattern

    said = run_clean(repo, worker, "feat-1")

    assert said[-1] == "result: True"
    assert "feat-1: the local branch is already gone" in said
    assert branch_exists(repo, "feat-1/next")


def test_a_git_worktree_list_that_fails_during_the_wait_keeps_waiting(
    repo: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = merged_worker(repo)
    worker.orca.removes_after = 2.0
    calls = {"n": 0}

    def list_failing_once(repo_dir: Path) -> list[gitrepo.GitWorktree]:
        """git's worktree list, failing on the first call after the one clean starts with."""
        calls["n"] += 1
        if calls["n"] == 2:
            raise gitrepo.NotARepo("git worktree list timed out")
        return gitrepo.list_worktrees(repo_dir)

    monkeypatch.setattr(clean, "list_worktrees", list_failing_once)

    said = run_clean(repo, worker, "feat-1")

    assert said[-1] == "result: True"
    assert (repo.ledger / "_archive" / "feat-1.md").exists()


def test_orca_failing_to_list_still_lets_a_half_done_cleanup_finish(repo: Fixture) -> None:
    worker = merged_worker(repo)
    second = merged_worker(repo, branch="feat-2", number=2)
    worker.github.pulls += second.github.pulls
    worker.github.branches["feat-2"] = second.sha
    half_done(repo, worker)
    worker.orca.list_error = "runtime timed out"

    said = run_clean(repo, worker, "feat-2", "feat-1")

    assert (
        "Orca couldn't say which worktrees it manages (runtime timed out), so no worktree was removed: run it again"
        in said
    )
    assert said[-1] == "result: False"
    assert (repo.ledger / "_archive" / "feat-1.md").exists() and "feat-1" not in worker.github.branches
    assert str(second.path) in worktree_paths(repo) and (repo.ledger / "feat-2.md").exists()
