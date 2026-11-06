"""Tests for src/tower/merge.py (`tower merge`), against FakeGitHub: a tiny GitHub for one repo that answers the gh
command lines merge.py runs, the way the real one did in docs/research/stacked-merge.md."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

from tower import merge
from tower.merge import GhResult

REPO = "acme/widget"


@dataclass
class FakePull:
    """One PR in the fake repo. stack_base is set for a PR in a GitHub stack (the branch the stack lands on)."""

    number: int
    base: str
    head: str
    sha: str = "headsha0"
    state: str = "open"
    merged: bool = False
    stack_base: str | None = None
    position: int = 1
    head_repo: str = REPO


@dataclass
class FakeGitHub:
    """The fake repo's PRs and branches. A merge-async stays pending for merge_polls polls (then merged, or failed
    with fail_message); after a merge, the PRs based on the merged branch move to the stack's base once their list
    has been read retarget_after times. Every gh call is kept in calls."""

    pulls: list[FakePull]
    delete_on_merge: bool = False
    current: int | None = None  # the PR `gh pr view` finds with no number
    merge_polls: int = 1
    fail_message: str = ""
    retarget_after: int = 0
    pending_merge: str = ""  # a merge-async already pending: a second request gets a 409 with this uuid
    local_cleanup_fails: bool = False  # gh pr merge merges, then fails to switch the worktree off the branch
    queued: bool = False  # the PR is already in a merge queue: GitHub answers the merge request with enqueued
    allow_merge_commit: bool = True  # the repo's settings
    allow_squash_merge: bool = True
    gh_queues: bool = False  # gh pr merge exits 0 after only queueing the PR (a merge queue, or auto-merge)
    pr_merge_error: str = ""  # gh pr merge fails with this, merging nothing
    reads_fail_after_merge: bool = False  # reading a PR fails once gh pr merge has run
    put_unreachable: bool = False  # the merge-async request never reaches GitHub (no body)
    list_fails: bool = False  # listing PRs by base fails
    poll_fails: bool = False  # reading a merge-async's status fails
    settings_fail: bool = False  # reading the repo's settings fails
    methods: list[str] = field(default_factory=list)  # each merge's method: merge, squash or rebase
    calls: list[list[str]] = field(default_factory=list)
    branches: dict[str, str] = field(default_factory=dict)
    polls: int = 0
    list_reads: int = 0
    retarget_from: str = ""

    def __post_init__(self) -> None:
        self.branches = {"main": "mainsha", **{p.head: p.sha for p in self.pulls}, **self.branches}

    def pull(self, number: int) -> FakePull:
        """The fake PR numbered number."""
        return next(p for p in self.pulls if p.number == number)

    def __call__(self, args: list[str]) -> GhResult:
        """Answer one gh command line the way gh and GitHub would: pr view, pr merge, or gh api."""
        self.calls.append(args)
        if args[:2] == ["pr", "view"]:
            number = int(args[2]) if args[2:3] and args[2].isdigit() else self.current
            if number is None:
                return GhResult(1, "", 'no pull requests found for branch "feat"')
            return ok({"url": f"https://github.com/{REPO}/pull/{number}"})
        if args[:2] == ["pr", "merge"]:
            return self.gh_pr_merge(args[2:])
        assert args[0] == "api" and args[1:3] == ["--hostname", "github.com"], args
        return self.api(args[3:])

    def gh_pr_merge(self, args: list[str]) -> GhResult:
        """`gh pr merge N ...`: refused for a stacked PR, as on GitHub; otherwise merges (or only queues) the PR."""
        pull = self.pull(int(args[0]))
        if self.pr_merge_error:
            return GhResult(1, "", self.pr_merge_error)
        if pull.stack_base is not None:
            return GhResult(1, "", "GraphQL: This pull request is part of a stack and must be merged using the"
                            " asynchronous merge REST API.")  # fmt: skip
        assert "--match-head-commit" in args and args[args.index("--match-head-commit") + 1] == pull.sha
        self.methods += [flag[2:] for flag in args if flag in ("--merge", "--squash", "--rebase")]
        if self.gh_queues:
            return GhResult(
                0, "", f"! Pull request {REPO}#{pull.number} will be automatically merged when all requirements are met"
            )
        self.land(pull)
        if "--delete-branch" in args:  # gh deletes it through the API at once: the PRs based on it close
            self.delete_branch(pull.head)
        if self.local_cleanup_fails:
            return GhResult(1, "", "failed to run git: fatal: 'main' is already used by worktree at '/code/repo'")
        return GhResult(0, "", f"✓ Squashed and merged pull request {REPO}#{pull.number}")

    def api(self, args: list[str]) -> GhResult:
        """`gh api [-X METHOD] repos/acme/widget/... [-f key=value]...`: the endpoints merge.py reads and writes."""
        method = args[args.index("-X") + 1] if "-X" in args else "GET"
        fields = dict(args[i + 1].split("=", 1) for i, arg in enumerate(args) if arg == "-f")
        endpoint = next(
            arg for i, arg in enumerate(args) if not arg.startswith("-") and args[i - 1] not in ("-X", "-f")
        )
        parts = endpoint.split("/")
        assert parts[:3] == ["repos", *REPO.split("/")], endpoint
        rest = parts[3:]
        if not rest and self.settings_fail:
            return GhResult(1, '{"message":"Server Error"}', "gh: Server Error (HTTP 502)")
        if not rest:
            allowed = {"allow_merge_commit": self.allow_merge_commit, "allow_squash_merge": self.allow_squash_merge}
            return ok({"full_name": REPO, "delete_branch_on_merge": self.delete_on_merge, **allowed})
        if rest[0] == "pulls" and len(rest) == 1 and self.list_fails:
            return GhResult(1, '{"message":"Server Error"}', "gh: Server Error (HTTP 502)")
        if rest[0] == "pulls" and len(rest) == 1 and fields["state"] == "closed":  # by base, or by head (owner:branch)
            closed = [p for p in self.pulls if p.state == "closed"]
            if "head" in fields:
                assert fields["head"].startswith("acme:"), fields
                closed = [p for p in closed if p.head == fields["head"].removeprefix("acme:")]
            else:
                closed = [p for p in closed if p.base == fields["base"]]
            merged_at = "2026-11-06T10:00:00Z"
            return ok([{"number": p.number, "base": {"ref": p.base}, "head": {"ref": p.head, "sha": p.sha},
                        "merged_at": merged_at if p.merged else None}
                       for p in closed])  # fmt: skip
        if rest[0] == "pulls" and len(rest) == 1:
            assert method == "GET" and fields["state"] == "open"
            self.list_reads += 1
            if self.retarget_from and self.list_reads > self.retarget_after:
                for pull in self.pulls:  # GitHub moves a stack's PRs, and any PR when it deletes the branch itself
                    moves = pull.stack_base is not None or self.delete_on_merge
                    if pull.base == self.retarget_from and pull.state == "open" and moves:
                        pull.base = pull.stack_base or "main"
            return ok([{"number": p.number} for p in self.pulls if p.state == "open" and p.base == fields["base"]])
        if rest[0] == "pulls" and len(rest) == 2:
            if self.reads_fail_after_merge and any(call[:2] == ["pr", "merge"] for call in self.calls):
                return GhResult(1, "", "gh: connection reset")
            return ok(self.pull_json(self.pull(int(rest[1]))))
        if rest[0] == "pulls" and rest[2] == "merge-async":
            return self.merge_async(self.pull(int(rest[1])), method, fields, rest[3:])
        if rest[:3] == ["git", "ref", "heads"]:
            sha = self.branches.get("/".join(rest[3:]))
            return (
                ok({"object": {"sha": sha}})
                if sha
                else GhResult(1, '{"message":"Not Found"}', "gh: Not Found (HTTP 404)")
            )
        if rest[:3] == ["git", "refs", "heads"] and method == "DELETE":
            self.delete_branch("/".join(rest[3:]))
            return GhResult(0, "", "")
        raise AssertionError(f"unexpected gh api call: {args}")

    def pull_json(self, pull: FakePull) -> dict[str, object]:
        """The REST pull request object for pull, with only the fields merge.py reads."""
        stack = None
        if pull.stack_base is not None:
            stack = {
                "base": {"ref": pull.stack_base, "sha": "x"},
                "id": 1,
                "number": 4,
                "position": pull.position,
                "size": 3,
            }
        return {
            "number": pull.number,
            "state": pull.state,
            "merged": pull.merged,
            "base": {"ref": pull.base, "repo": {"full_name": REPO, "default_branch": "main"}},
            "head": {"ref": pull.head, "sha": pull.sha, "repo": {"full_name": pull.head_repo}},
            "stack": stack,
        }

    def merge_async(self, pull: FakePull, method: str, fields: dict[str, str], rest: list[str]) -> GhResult:
        """PUT .../merge-async (accepted: pending with a uuid; 409 with the pending one's uuid) and GET .../{uuid}."""
        if rest and self.poll_fails:
            return GhResult(1, '{"message":"Server Error"}', "gh: Server Error (HTTP 502)")
        if rest:  # GET .../merge-async/{uuid}: the merge's status
            self.polls += 1
            if self.polls < self.merge_polls:
                return ok({"status": "pending", "details": {"uuid": rest[0]}})
            if self.fail_message:
                return ok({"status": "failed", "details": {"message": self.fail_message}})
            self.land(pull)
            return ok({"status": "merged", "details": {"message": "merged", "sha": "mergesha"}})
        assert method == "PUT", method
        assert {key: fields[key] for key in ("merge_action", "sha")} == {
            "merge_action": "default",
            "sha": pull.sha,
        }
        self.methods.append(fields["merge_method"])
        if self.queued:
            return ok({"status": "enqueued", "details": {"message": "already in the merge queue"}})
        if self.put_unreachable:
            return GhResult(1, "", "gh merge-async didn't run (TimeoutExpired)")
        if self.pending_merge:  # GitHub documents no 409 body beyond "that request's UUID and merge options"
            body = {"details": {"uuid": self.pending_merge, "merge_method": "merge"}}
            return GhResult(1, json.dumps(body), "gh: A merge request is already pending (HTTP 409)")
        return ok({"status": "pending", "details": {"uuid": "uuid-1", "message": "accepted"}})

    def delete_branch(self, branch: str) -> None:
        """An API delete of branch: as on GitHub (docs/research/stacked-merge.md), the open PRs based on it close."""
        del self.branches[branch]
        for pull in self.pulls:
            if pull.base == branch and pull.state == "open":
                pull.state = "closed"

    def land(self, pull: FakePull) -> None:
        """GitHub merges the PR; the PRs based on its branch get retargeted (see retarget_after)."""
        pull.merged, pull.state = True, "closed"
        if self.retarget_from == "":
            self.retarget_from = pull.head
        if self.delete_on_merge:  # GitHub deletes the branch itself, moving the PRs based on it first
            for above in self.pulls:
                if above.base == pull.head and above.state == "open":
                    above.base = pull.base
            self.branches.pop(pull.head, None)


def ok(body: object) -> GhResult:
    """A successful gh call printing BODY as JSON."""
    return GhResult(0, json.dumps(body), "")


@dataclass
class Clock:
    """A fake clock that moves only when merge.py sleeps."""

    now: float = 0.0
    slept: list[float] = field(default_factory=list)

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def run(github: FakeGitHub, pr: int | None = 1, clock: Clock | None = None) -> list[str]:
    """Run merge_pr on the fake repo; the lines it said."""
    clock = clock or Clock()
    said: list[str] = []
    merge.merge_pr(pr, github, said.append, sleep=clock.sleep, clock=lambda: clock.now)
    return said


def test_an_unstacked_pr_is_squash_merged_with_gh_pr_merge_and_its_branch_deleted() -> None:
    github = FakeGitHub([FakePull(1, base="main", head="feat")])

    said = run(github)

    assert github.pull(1).merged and github.methods == ["squash"]
    assert said[-1] == "merged PR #1 into main with a squash"
    assert "feat" not in github.branches
    assert not any("merge-async" in arg for call in github.calls for arg in call)


def stack() -> list[FakePull]:
    """A three-PR stack on main: feat1 <- feat2 <- feat3."""
    return [
        FakePull(1, base="main", head="feat1", sha="sha1", stack_base="main", position=1),
        FakePull(2, base="feat1", head="feat2", sha="sha2", stack_base="main", position=2),
        FakePull(3, base="feat2", head="feat3", sha="sha3", stack_base="main", position=3),
    ]


def test_a_stacked_pr_is_merged_with_merge_async_and_polled_until_github_says_merged() -> None:
    github = FakeGitHub(stack(), merge_polls=3)

    run(github)

    assert github.pull(1).merged and not github.pull(2).merged
    assert github.methods == ["merge"]  # a squash would rewrite the commits every PR above holds
    assert github.polls == 3
    assert not any(call[:2] == ["pr", "merge"] for call in github.calls)


def test_a_merged_stack_branch_is_deleted_only_after_github_retargets_the_pr_above_it() -> None:
    github = FakeGitHub(stack(), retarget_after=2)

    run(github)

    assert "feat1" not in github.branches
    assert (github.pull(2).state, github.pull(2).base) == ("open", "main")


def test_an_upper_pr_in_a_stack_is_refused_since_github_would_merge_every_pr_below_it_too() -> None:
    github = FakeGitHub(stack())

    with pytest.raises(merge.MergeError, match=r"PR #2 is based on feat1, not on its stack's base main.*feat1 first"):
        run(github, pr=2)

    assert not any(p.merged for p in github.pulls)
    assert not any(call[1:2] == ["--hostname"] and "PUT" in call for call in github.calls)


def test_a_merge_github_fails_is_an_error_with_githubs_message_and_the_branch_stays() -> None:
    github = FakeGitHub(stack(), merge_polls=2, fail_message='Required status check "ci" is failing')

    with pytest.raises(merge.MergeError, match=r"GitHub didn't merge PR #1: Required status check \"ci\" is failing"):
        run(github)

    assert "feat1" in github.branches


def test_a_merge_still_pending_at_the_timeout_says_to_run_again_and_deletes_nothing() -> None:
    github = FakeGitHub(stack(), merge_polls=10_000)
    clock = Clock()

    with pytest.raises(merge.StillMerging, match=r"run tower merge 1 again"):
        run(github, clock=clock)

    assert clock.now >= merge.MERGE_TIMEOUT_SECONDS
    assert "feat1" in github.branches


def test_a_merge_already_pending_is_picked_up_instead_of_failing() -> None:
    """GitHub answers a second merge request for the same PR with a 409 and the first request's uuid."""
    github = FakeGitHub(stack(), pending_merge="uuid-earlier")

    run(github)

    assert github.pull(1).merged
    assert any(call[-1].endswith("/merge-async/uuid-earlier") for call in github.calls)


def test_the_branch_stays_when_the_pr_above_isnt_retargeted_in_time_and_running_again_deletes_it() -> None:
    github = FakeGitHub(stack(), retarget_after=10_000)

    said = run(github)

    assert "feat1" in github.branches and github.pull(2).state == "open"
    assert any("left feat1 on GitHub: open PRs are still based on it (#2)" in line and "tower merge 1 again" in line
               for line in said)  # fmt: skip

    github.retarget_after = 0
    said = run(github)
    assert "feat1" not in github.branches and github.pull(2).base == "main"
    assert "PR #1 is already merged" in said[0]


def test_a_repo_that_deletes_head_branches_on_merge_is_left_to_do_it() -> None:
    github = FakeGitHub(stack(), delete_on_merge=True)

    said = run(github)

    assert not any("DELETE" in call for call in github.calls)
    assert any("deletes head branches on merge" in line for line in said)


def test_a_branch_that_moved_after_the_merge_is_left_alone() -> None:
    pulls = stack()
    pulls[0].merged, pulls[0].state = True, "closed"
    pulls[1].base = "main"  # GitHub retargeted it after the merge
    github = FakeGitHub(pulls, branches={"feat1": "newer-commit"})

    said = run(github)

    assert github.branches["feat1"] == "newer-commit"
    assert any("left feat1 on GitHub: it has moved since PR #1" in line for line in said)


def test_an_already_merged_pr_from_a_fork_never_deletes_a_branch_of_the_same_name_here() -> None:
    github = FakeGitHub([FakePull(1, "main", "feat", state="closed", merged=True, head_repo="someone/widget")])

    said = run(github)

    assert "feat" in github.branches
    assert any("someone/widget" in line for line in said)


def test_gh_pr_merge_failing_after_the_merge_is_a_merge_with_gh_s_complaint_passed_on() -> None:
    github = FakeGitHub([FakePull(1, base="main", head="feat")], local_cleanup_fails=True)

    said = run(github)

    assert github.pull(1).merged
    assert any("PR #1 is merged, but gh pr merge also said: " in line and "already used by worktree" in line
               for line in said)  # fmt: skip


@pytest.mark.parametrize(
    ("github", "pr", "message"),
    [
        (FakeGitHub([FakePull(1, "main", "feat", state="closed")]), 1, "PR #1 is closed, not open"),
        (
            FakeGitHub([FakePull(1, "main", "feat")]),
            None,
            'couldn\'t find the PR: no pull requests found for branch "feat"',
        ),
    ],
    ids=["closed", "no PR for this branch"],
)
def test_nothing_is_merged_for_a_pr_that_is_closed_or_missing(github: FakeGitHub, pr: int | None, message: str) -> None:
    with pytest.raises(merge.MergeError) as raised:
        run(github, pr=pr)

    assert str(raised.value) == message
    assert not any(call[:2] == ["pr", "merge"] or "PUT" in call for call in github.calls)


def test_with_no_number_it_merges_this_branchs_pr() -> None:
    github = FakeGitHub(stack(), current=1)

    run(github, pr=None)

    assert github.pull(1).merged


def test_a_stacked_pr_put_in_the_merge_queue_is_still_merging_and_keeps_its_branch() -> None:
    github = FakeGitHub(stack(), queued=True)

    with pytest.raises(merge.StillMerging, match="PR #1 is in the merge queue"):
        run(github)

    assert "feat1" in github.branches and not github.pull(1).merged


def chain() -> list[FakePull]:
    """Two PRs whose bases chain (feat1 <- feat2) but that aren't a GitHub stack."""
    return [FakePull(1, base="main", head="feat1", sha="sha1"), FakePull(2, base="feat1", head="feat2", sha="sha2")]


def test_an_unstacked_pr_with_a_pr_based_on_it_keeps_its_branch_since_deleting_it_would_close_that_pr() -> None:
    """GitHub moves the PRs above a merged one outside a stack only when it deletes the branch itself."""
    github = FakeGitHub(chain())

    said = run(github)

    assert github.pull(1).merged and github.methods == ["merge"]
    assert "feat1" in github.branches
    assert (github.pull(2).state, github.pull(2).base) == ("open", "feat1")
    based = "left feat1 on GitHub: open PRs are still based on it (#2)"
    assert any(based in line and "gh pr edit 2 --base main" in line
               for line in said)  # fmt: skip


def test_an_unstacked_pr_with_a_pr_based_on_it_leaves_the_branch_to_a_repo_that_deletes_merged_branches() -> None:
    github = FakeGitHub(chain(), delete_on_merge=True)

    said = run(github)

    assert "feat1" not in github.branches
    assert (github.pull(2).state, github.pull(2).base) == ("open", "main")
    assert any("deletes head branches on merge" in line for line in said)


def integration() -> list[FakePull]:
    """integration/a-plus -> main, after two ticket PRs were squash-merged into integration/a-plus."""
    return [
        FakePull(55, base="integration/a-plus", head="fix-a", state="closed", merged=True),
        FakePull(56, base="integration/a-plus", head="fix-b", state="closed", merged=True),
        FakePull(57, base="integration/a-plus", head="fix-c", state="closed"),  # closed unmerged: doesn't count
        FakePull(60, base="main", head="integration/a-plus", sha="intsha"),
    ]


def test_an_integration_branch_is_merged_with_a_merge_commit_and_kept_so_both_branches_share_history() -> None:
    github = FakeGitHub(integration())

    said = run(github, pr=60)

    assert github.pull(60).merged and github.methods == ["merge"]
    assert "integration/a-plus" in github.branches
    assert any("other PRs were merged into its head branch integration/a-plus (#55, #56)" in line for line in said)

    said = run(github, pr=60)  # run again: still kept
    assert "integration/a-plus" in github.branches
    assert any("kept integration/a-plus" in line for line in said)


def test_a_repo_without_merge_commits_gets_a_squash_and_says_so() -> None:
    github = FakeGitHub(stack(), allow_merge_commit=False)

    said = run(github)

    assert github.methods == ["squash"]
    assert any("allows no merge commits" in line for line in said)


def test_nothing_is_merged_when_github_cant_list_the_prs_based_on_the_branch() -> None:
    github = FakeGitHub([FakePull(1, base="main", head="feat")], list_fails=True)

    with pytest.raises(merge.MergeError, match="nothing was merged"):
        run(github)

    assert not github.pull(1).merged


def test_a_pr_based_on_a_chained_branch_whose_pr_merged_is_refused_until_it_is_moved() -> None:
    """Merging it would put it on feat1, which is already merged: its commits would never reach main."""
    github = FakeGitHub(chain())
    run(github)  # feat1's PR: merged, feat1 kept since #2 is based on it

    with pytest.raises(merge.MergeError) as raised:
        run(github, pr=2)

    assert "PR #2 is based on feat1, whose PR #1 is already merged" in str(raised.value)
    assert "gh pr edit 2 --base main" in str(raised.value)
    assert not github.pull(2).merged


def test_a_pr_into_an_integration_branch_that_once_merged_into_main_is_still_merged() -> None:
    github = FakeGitHub([*integration(), FakePull(61, base="integration/a-plus", head="fix-d")])
    run(github, pr=60)  # integration/a-plus -> main

    run(github, pr=61)

    assert github.pull(61).merged and github.methods == ["merge", "squash"]


def test_an_integration_branch_is_never_squashed_even_when_the_repo_allows_no_merge_commits() -> None:
    github = FakeGitHub(integration(), allow_merge_commit=False)

    with pytest.raises(merge.MergeError, match="allows no merge commits.*per-ticket commits"):
        run(github, pr=60)

    assert not github.pull(60).merged


def test_a_status_that_cant_be_read_says_still_merging_not_failed() -> None:
    github = FakeGitHub(stack(), merge_polls=3, poll_fails=True)

    with pytest.raises(merge.StillMerging, match="couldn't read .*Server Error.*run tower merge 1 again"):
        run(github)


def test_nothing_is_merged_when_the_repos_settings_cant_be_read() -> None:
    github = FakeGitHub([FakePull(1, base="main", head="feat")], settings_fail=True)

    with pytest.raises(merge.MergeError, match="couldn't read acme/widget's merge settings.*nothing was merged"):
        run(github)

    assert not github.pull(1).merged


def test_gh_pr_merge_that_only_queues_the_pr_is_still_merging_not_merged() -> None:
    github = FakeGitHub([FakePull(1, base="main", head="feat")], gh_queues=True)
    said: list[str] = []

    with pytest.raises(merge.StillMerging, match="isn't merged yet.*merge queue or turned on auto-merge"):
        merge.merge_pr(1, github, said.append)

    assert not any(line.startswith("merged PR") for line in said)


def test_a_failed_gh_pr_merge_whose_pr_cant_be_read_back_is_still_merging_with_ghs_reason() -> None:
    github = FakeGitHub([FakePull(1, base="main", head="feat")], pr_merge_error="HTTP 502", reads_fail_after_merge=True)

    with pytest.raises(merge.StillMerging, match=r"gh pr merge failed \(HTTP 502\).*couldn't be read back"):
        run(github)


def test_a_merge_request_that_never_reached_github_is_still_merging_not_refused() -> None:
    github = FakeGitHub(stack(), put_unreachable=True)

    with pytest.raises(merge.StillMerging, match="couldn't ask GitHub to merge PR #1"):
        run(github)


def test_a_fork_pr_is_squashed_though_this_repo_has_a_same_named_branch_with_prs_on_it() -> None:
    github = FakeGitHub(
        [FakePull(1, base="main", head="feat", head_repo="someone/widget"), FakePull(2, base="feat", head="other")]
    )

    run(github)

    assert github.pull(1).merged and github.methods == ["squash"]


def test_a_repo_without_squash_merges_gets_a_merge_commit_for_a_ticket_pr() -> None:
    github = FakeGitHub([FakePull(1, base="main", head="feat")], allow_squash_merge=False)

    said = run(github)

    assert github.methods == ["merge"]
    assert any("allows no squash merges" in line for line in said)


def test_a_pr_outside_the_stack_based_on_the_merged_branch_gets_the_gh_pr_edit_advice() -> None:
    """GitHub moves the stack's next PR, never the hand-opened #9, so running again alone can't help."""
    github = FakeGitHub([*stack(), FakePull(9, base="feat1", head="side")])

    said = run(github)

    assert "feat1" in github.branches and github.pull(9).state == "open"
    assert any("open PRs are still based on it (#9)" in line and "gh pr edit 9 --base main" in line for line in said)


def test_a_pr_into_a_chained_branch_that_moved_on_after_its_merge_is_merged() -> None:
    """A long-lived branch merged once, then given new commits, is a base like any other."""
    pulls = chain()
    pulls[0].merged, pulls[0].state = True, "closed"
    github = FakeGitHub(pulls, branches={"feat1": "newer-commit"})

    run(github, pr=2)

    assert github.pull(2).merged
