"""merge.py: `tower merge [PR]`, the one way a worker merges its own PR, stacked or not.

GitHub groups PRs into a native stack when they are submitted as one (git-spice and `gh stack` do; PRs whose bases
merely chain stay separate). It then refuses `gh pr merge` and `PUT .../pulls/{n}/merge` for them: only its
asynchronous merge API merges them, and merging a stacked PR merges every unmerged PR below it too, onto the stack's
base. So:

- A stacked PR is merged only from the bottom of its stack (its base is the stack's base), one PR per call, so a
  merge lands exactly one PR and one approval covers exactly that. It goes through `PUT .../pulls/{n}/merge-async`
  (a direct merge, pinned to the head just read), then `GET .../merge-async/{uuid}` is polled every POLL_SECONDS
  until GitHub says merged or failed, or MERGE_TIMEOUT_SECONDS pass.
- Any other PR is merged with `gh pr merge N`, as before, pinned to the head just read (--match-head-commit).
- The method (merge_method()): a merge commit for a stacked PR, for a PR with open PRs based on its head (a chain
  outside a stack) and for one whose head other PRs were merged into (an integration branch, like
  integration/a-plus -> main), so commits other branches hold are never rewritten; a squash for every other PR.
  There is no setting for it.
- The head branch is deleted on GitHub only when that is safe (delete_head_branch()). A delete through the API
  closes the open PRs based on the branch, so it waits until none is: GitHub moves the PR above a merged stacked PR
  onto the stack's base by itself, but outside a stack it moves them only when it deletes the branch itself. An
  integration branch is kept. A PR with nothing based on or merged into its branch lets `gh pr merge
  --delete-branch` delete it, on GitHub and locally; otherwise the local branch is left (`git-spice repo sync`
  removes it).

Run it again after a timeout or a branch left behind: a pending merge is picked up (GitHub answers a second request
with the first one's uuid), and an already merged PR only gets its branch deleted.

The approval hook (plugin/scripts/confirm-public.py) judges `tower merge` like `gh pr merge`, since the gh calls here
are out of its sight: by merge_to_main or merge_to_branch for the branch the PR lands on (for a stacked PR, the
stack's base). docs/adr/0005-tower-merge-runs-the-merge-and-the-hook-judges-it.md says why this script merges
itself, and docs/research/stacked-merge.md has GitHub's behaviour, checked end to end.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

GH_TIMEOUT_SECONDS = 120  # one gh call; `gh pr merge` is the slowest
POLL_SECONDS = 5
MERGE_TIMEOUT_SECONDS = 300  # the spike's merges took under a minute
RETARGET_TIMEOUT_SECONDS = 120
METHOD_NAMES = {"merge": "a merge commit", "squash": "a squash", "rebase": "a rebase"}
PR_URL = re.compile(r"https://(?P<host>[^/]+)/(?P<repo>[^/]+/[^/]+)/pull/(?P<number>[0-9]+)")


@dataclass(frozen=True)
class GhResult:
    """What one gh call returned: its exit code, stdout and stderr."""

    code: int
    out: str
    err: str


Gh = Callable[[list[str]], GhResult]
Say = Callable[[str], None]


class MergeError(Exception):
    """The PR wasn't merged: it can't be found, isn't open, or GitHub refused or failed the merge."""


class StillMerging(Exception):
    """GitHub accepted the merge but hadn't finished it by MERGE_TIMEOUT_SECONDS."""


@dataclass(frozen=True)
class Pull:
    """What merging one PR needs to know, from GitHub's REST pull request object."""

    host: str
    repo: str  # owner/repo
    number: int
    state: str  # open | closed
    merged: bool
    base: str
    head: str
    head_sha: str
    head_repo: str | None  # owner/repo of the head branch: another one for a fork, None when that repo is deleted
    stack_base: str | None  # the branch the PR's GitHub stack lands on; None when it isn't in a stack

    def api(self, *args: str) -> list[str]:
        """A gh api command line for this PR's host."""
        return ["api", "--hostname", self.host, *args]


def run_gh(cwd: Path) -> Gh:
    """A Gh that runs gh in cwd with a timeout, never prompting; a gh that can't start or times out is exit 1."""
    env = {**os.environ, "GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1", "NO_COLOR": "1"}

    def gh(args: list[str]) -> GhResult:
        """Run gh with args."""
        try:
            proc = subprocess.run(
                ["gh", *args],
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=GH_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return GhResult(1, "", f"gh {args[0]} didn't run ({type(error).__name__}: {error})")
        return GhResult(proc.returncode, proc.stdout, proc.stderr)

    return gh


def merge_pr(
    pr: int | None,
    gh: Gh,
    say: Say,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Merge PR (None: this branch's PR) the way merge_method() picks, saying what happens; then delete its head
    branch when that is safe. Raises MergeError if it isn't merged, StillMerging if GitHub hasn't finished."""
    pull = find_pull(pr, gh)
    if pull.merged:  # run again after a timeout, or after its branch was left
        say(f"PR #{pull.number} is already merged")
        delete_head_branch(pull, gh, say, sleep, clock)
        return
    if pull.state != "open":
        raise MergeError(f"PR #{pull.number} is {pull.state}, not open")
    if pull.stack_base is not None and pull.base != pull.stack_base:
        raise MergeError(
            f"PR #{pull.number} is based on {pull.base}, not on its stack's base {pull.stack_base}: GitHub would merge"
            " every PR below it too. tower merge merges a stack one PR at a time from the bottom, so merge the PR"
            f" from {pull.base} first"
        )
    above, merged_in = based_on(pull, gh), merged_into(pull, gh)
    if above is None or merged_in is None:
        raise MergeError(f"couldn't list the PRs based on {pull.head}, so nothing was merged; run it again")
    method, why = merge_method(pull, above, merged_in, json_body(gh(pull.api(f"repos/{pull.repo}"))))
    if why:
        say(f"merging PR #{pull.number} with {METHOD_NAMES[method]}: {why}")
    if pull.stack_base is not None:
        if merge_async(pull, method, gh, say, sleep, clock):
            delete_head_branch(pull, gh, say, sleep, clock)
        return
    # gh deletes the branch through the API at once, which would close the PRs based on it
    gh_deletes = not above and not merged_in
    merge_unstacked(pull, method, gh, say, delete_branch=gh_deletes)
    if not gh_deletes:
        delete_head_branch(pull, gh, say, sleep, clock)


def merge_method(pull: Pull, above: list[int], merged_in: list[int], settings: dict[str, object]) -> tuple[str, str]:
    """How to merge pull, and why when it isn't a plain squash ("" then). A merge commit (never a squash, which would
    rewrite commits other branches hold) for a PR in a GitHub stack, one with open PRs based on its head (a chain
    outside a stack), and one whose head other PRs were merged into (an integration branch: its per-ticket commits
    survive, and both branches keep a shared history). If the repo (settings) allows no merge commits, a squash, or
    a rebase if it allows no squash either. Every other PR: a squash, as workers always have."""
    if pull.stack_base is not None:
        why = "it is in a GitHub stack"
    elif merged_in:
        why = f"other PRs were merged into its head branch {pull.head} ({', '.join(f'#{n}' for n in merged_in)})"
    elif above:
        why = f"open PRs are based on its head branch {pull.head} ({', '.join(f'#{n}' for n in above)})"
    else:
        return "squash", ""
    if settings.get("allow_merge_commit") is False:
        fallback = "squash" if settings.get("allow_squash_merge") is not False else "rebase"
        return fallback, f"{why}, but {pull.repo} doesn't allow merge commits"
    return "merge", why


def find_pull(pr: int | None, gh: Gh) -> Pull:
    """The PR numbered pr (None: this branch's), found the way gh pr merge finds it, then read from GitHub's REST API."""
    found = gh(["pr", "view", *([str(pr)] if pr is not None else []), "--json", "url"])
    url = PR_URL.fullmatch(str(json_body(found).get("url", ""))) if found.code == 0 else None
    if url is None:
        raise MergeError(f"couldn't find the PR: {gh_reason(found)}")
    host, repo, number = url.group("host"), url.group("repo"), url.group("number")
    read = gh(["api", "--hostname", host, f"repos/{repo}/pulls/{number}"])
    body = json_body(read)
    if read.code != 0 or not body:
        raise MergeError(f"couldn't read PR #{number} from GitHub: {gh_reason(read)}")
    state, base, head, head_sha = (
        dig(body, *path) for path in (["state"], ["base", "ref"], ["head", "ref"], ["head", "sha"])
    )
    if not (isinstance(state, str) and isinstance(base, str) and isinstance(head, str) and isinstance(head_sha, str)):
        raise MergeError(f"GitHub's answer for PR #{number} isn't the shape expected: {read.out[:200]!r}")
    head_repo, stack_base = dig(body, "head", "repo", "full_name"), dig(body, "stack", "base", "ref")
    return Pull(
        host=host,
        repo=repo,
        number=int(number),
        state=state,
        merged=dig(body, "merged") is True,
        base=base,
        head=head,
        head_sha=head_sha,
        head_repo=head_repo if isinstance(head_repo, str) else None,
        stack_base=stack_base if isinstance(stack_base, str) else None,
    )


def merge_unstacked(pull: Pull, method: str, gh: Gh, say: Say, delete_branch: bool) -> None:
    """Merge a PR outside any stack with gh pr merge, as workers always have, by method; with delete_branch, gh
    deletes its head branch too, on GitHub and locally."""
    flags = [f"--{method}", *(["--delete-branch"] if delete_branch else []), "--match-head-commit", pull.head_sha]
    merged = gh(["pr", "merge", str(pull.number), *flags])
    if merged.code == 0:
        for line in (merged.out + "\n" + merged.err).splitlines():
            if line.strip():
                say(line.strip())
        say(f"merged PR #{pull.number} into {pull.base} with {METHOD_NAMES[method]}")
        return
    # gh merges first and cleans up after (deletes the branches, switches the worktree), so a failure may come late
    if not find_pull(pull.number, gh).merged:
        raise MergeError(f"gh pr merge didn't merge PR #{pull.number}: {gh_reason(merged)}")
    say(f"PR #{pull.number} is merged, but gh pr merge also said: {gh_reason(merged)}")


def merge_async(
    pull: Pull, method: str, gh: Gh, say: Say, sleep: Callable[[float], None], clock: Callable[[], float]
) -> bool:
    """Merge a stacked PR with the asynchronous merge API and wait until GitHub says it merged (True) or put it in the
    merge queue (False). Raises MergeError if GitHub refuses or fails it, StillMerging if it is still pending at
    MERGE_TIMEOUT_SECONDS."""
    endpoint = f"repos/{pull.repo}/pulls/{pull.number}/merge-async"
    say(f"PR #{pull.number} is in a GitHub stack on {pull.stack_base}: merging it with GitHub's asynchronous merge API")
    fields = ["-f", f"merge_method={method}", "-f", "merge_action=direct_merge", "-f", f"sha={pull.head_sha}"]
    asked = gh(pull.api("-X", "PUT", endpoint, *fields))
    answer = json_body(asked)
    uuid = dig(answer, "details", "uuid")
    if asked.code != 0 and not uuid:
        raise MergeError(f"GitHub refused to merge PR #{pull.number}: {gh_reason(asked)}")
    if asked.code != 0:  # 409: a merge of this PR is already pending, and GitHub gave its uuid
        say(f"a merge of PR #{pull.number} was already pending ({uuid}); waiting for it")
    status = answer.get("status")
    deadline = clock() + MERGE_TIMEOUT_SECONDS
    while status == "pending":
        if clock() >= deadline:
            raise StillMerging(
                f"GitHub is still merging PR #{pull.number} after {MERGE_TIMEOUT_SECONDS // 60} minutes; run tower"
                f" merge {pull.number} again to keep waiting (it picks up this merge, {uuid})"
            )
        sleep(POLL_SECONDS)
        answer = json_body(gh(pull.api(f"{endpoint}/{uuid}")))
        status = answer.get("status")
    if status == "enqueued":
        say(f"PR #{pull.number} is in the merge queue: GitHub merges it once the queue's checks pass")
        return False
    if status != "merged":
        message = dig(answer, "details", "message")
        raise MergeError(f"GitHub didn't merge PR #{pull.number}: {message or f'its merge says {status!r}'}")
    say(f"merged PR #{pull.number} into {pull.stack_base} with {METHOD_NAMES[method]}")
    return True


def delete_head_branch(
    pull: Pull, gh: Gh, say: Say, sleep: Callable[[float], None], clock: Callable[[], float]
) -> None:
    """Delete a merged PR's head branch on GitHub, as --delete-branch would, but only when that is safe: the branch is
    in pull's repo, the repo doesn't delete merged branches itself, no PR was merged into it (an integration branch
    stays), no open PR is based on it, and it still points at the PR's head. Otherwise it says why the branch stays.
    The local branch is never touched.

    A delete through the API closes the open PRs based on the branch, so it waits for them to move. GitHub moves the
    PRs above a stacked PR onto the stack's base by itself, so for one it waits up to RETARGET_TIMEOUT_SECONDS.
    Outside a stack GitHub moves them only when it deletes the branch itself."""
    if pull.head_repo is None or pull.head_repo.lower() != pull.repo.lower():
        say(f"left {pull.head}: it is in {pull.head_repo or 'a deleted fork'}, not {pull.repo}")
        return
    merged_in = merged_into(pull, gh)
    if merged_in:
        say(
            f"kept {pull.head}: other PRs were merged into it ({', '.join(f'#{n}' for n in merged_in)}), so it is an integration branch"
        )
        return
    settings = json_body(gh(pull.api(f"repos/{pull.repo}")))
    if settings.get("delete_branch_on_merge") is True:
        say(f"left {pull.head} to GitHub: {pull.repo} deletes head branches on merge")
        return
    github_retargets = pull.stack_base is not None
    deadline = clock() + (RETARGET_TIMEOUT_SECONDS if github_retargets else 0)
    above = based_on(pull, gh)
    while above and clock() < deadline:
        sleep(POLL_SECONDS)
        above = based_on(pull, gh)
    if above is None or merged_in is None:
        say(f"left {pull.head} on GitHub: couldn't list the PRs based on it; run tower merge {pull.number} again")
        return
    if above:
        listed = ", ".join(f"#{n}" for n in above)
        if github_retargets:
            then = f"run tower merge {pull.number} again in a minute to delete it"
        else:
            moves = " and ".join(f"gh pr edit {n} --base {pull.base}" for n in above)
            then = (
                f"GitHub moves them only when it deletes a merged branch itself, so move them ({moves}) or turn on"
                f" the repo's automatic head branch deletion, then run tower merge {pull.number} again to delete it"
            )
        say(
            f"left {pull.head} on GitHub: open PRs are still based on it ({listed}), and deleting it would close them; {then}"
        )
        return
    ref = quote(pull.head, safe="/")
    tip = gh(pull.api(f"repos/{pull.repo}/git/ref/heads/{ref}"))
    if tip.code != 0:
        gone = "HTTP 404" in tip.err
        say(f"{pull.head} is already deleted on GitHub" if gone else f"left {pull.head}: {gh_reason(tip)}")
        return
    sha = dig(json_body(tip), "object", "sha")
    if sha != pull.head_sha:
        say(
            f"left {pull.head} on GitHub: it has moved since PR #{pull.number} merged (it is at {str(sha)[:7]}, the"
            f" PR's head was {pull.head_sha[:7]})"
        )
        return
    deleted = gh(pull.api("-X", "DELETE", f"repos/{pull.repo}/git/refs/heads/{ref}"))
    if deleted.code != 0:
        say(f"couldn't delete {pull.head} on GitHub: {gh_reason(deleted)}")
        return
    say(f"deleted {pull.head} on GitHub (the local branch is left as it is)")


def based_on(pull: Pull, gh: Gh) -> list[int] | None:
    """The open PRs whose base is pull's head branch; None if GitHub can't say."""
    return listed_prs(pull, gh, "open")


def merged_into(pull: Pull, gh: Gh) -> list[int] | None:
    """The PRs merged into pull's head branch (their base was it); None if GitHub can't say."""
    return listed_prs(pull, gh, "closed", merged_only=True)


def listed_prs(pull: Pull, gh: Gh, state: str, merged_only: bool = False) -> list[int] | None:
    """The numbers of the PRs in state (open or closed) whose base is pull's head branch, only the merged ones with
    merged_only (first 100: any one is enough here); None if GitHub can't say."""
    listed = gh(
        pull.api(
            "-X",
            "GET",
            f"repos/{pull.repo}/pulls",
            "-f",
            f"state={state}",
            "-f",
            f"base={pull.head}",
            "-f",
            "per_page=100",
        )
    )
    try:
        items = json.loads(listed.out)
    except ValueError:
        return None
    if listed.code != 0 or not isinstance(items, list):
        return None
    chosen = [item for item in items if not merged_only or dig(item, "merged_at")]
    numbers = [dig(item, "number") for item in chosen]
    return [n for n in numbers if isinstance(n, int)] if all(isinstance(n, int) for n in numbers) else None


def json_body(result: GhResult) -> dict[str, object]:
    """result's stdout as a JSON object (gh api prints GitHub's body, an error's too), or {} if it isn't one."""
    try:
        body = json.loads(result.out)
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def dig(value: object, *keys: str) -> object:
    """value[keys[0]][keys[1]]... in parsed JSON, or None where a level is missing or isn't an object."""
    for key in keys:
        value = value.get(key) if isinstance(value, dict) else None
    return value


def gh_reason(result: GhResult) -> str:
    """Why a gh call failed, in GitHub's or gh's own words: the body's message, else gh's last line of stderr."""
    message = json_body(result).get("message")
    if isinstance(message, str) and message:
        return message
    return (result.err.strip().splitlines() or [f"exit {result.code}"])[-1]
