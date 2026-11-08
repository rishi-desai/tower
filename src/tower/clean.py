"""clean.py: `tower clean [WORKTREE ...|--all] [--dry-run]`, cleanup of finished workers, proven safe first.

Every check is read fresh for each worktree, and all of them must pass, or tower says which failed and leaves that
worktree exactly as it was:

    its branch's PR is merged (`gh pr list --head`; an open PR on the branch means work goes on)
    its HEAD is that PR's head commit, so the merge took every commit (a squash merge leaves none on the base)
    `git status --porcelain` is empty: nothing uncommitted or untracked
    no stash entry was made on its branch (the stash is shared by every worktree)
    its card doesn't say work goes on (running, needs-you, blocked: the branch name may have been reused)
    live sessions (`claude agents --json`, never the event log) show none busy or waiting there
    tower clean isn't running inside it

Then, in this order: its terminals are closed and the worktree removed through Orca (`orca terminal close --all`,
`orca worktree rm`, never forced, then waiting up to ORCA_REMOVE_WAIT_SECONDS for git to stop listing it, since Orca
answers before it is gone), or with `git worktree remove` (never forced) when Orca isn't running or doesn't manage it
(Orca running but unable to list its worktrees removes nothing); its branch on GitHub is deleted by tower
merge's rules (merge.delete_head_branch: never while open PRs are based on it, never an integration branch, never once
it moved, and it says why when it leaves one); the local branch is deleted only if it still points at the checked
commit (`git update-ref -d`), with its upstream settings; its card (and its decisions log, as <card>.decisions.md) moves to the Ledger's _archive folder. Live sessions are read again just before each worktree's terminals close, since a removal before it (a stacked
PR's branch delete waits for GitHub) takes time. When the worktree can't be removed, nothing after it runs; a branch or
card step that fails is said and makes the run fail, but the others still run. Once every worktree is done, the main
checkout runs `git pull --ff-only` if any was removed; a pull that fails is said, but the cleanup is done by then. Git-ignored files (.env, build output) go with the worktree:
neither `git status --porcelain` nor `git worktree remove` counts them, so --dry-run says so.

--all takes every worktree but the main checkout whose branch's PR is merged, and says why each other one was left;
one whose PR GitHub couldn't be asked about fails its check instead of being skipped.
A half-done cleanup (the worktree is gone, but its card is still in the Ledger) is finished by naming its branch: the
checks that still apply run (its PR is merged, the local branch is at the PR's head, no stash entry on it, the card
doesn't say work goes on), then the branch, card and pull steps. --all doesn't look for them.
--dry-run runs every check and says what would be removed, touching nothing. tower clean never asks: the approval hook
(plugin/scripts/confirm-public.py) judges it by the policy's cleanup key, so under "ask" the hub shows --dry-run's list
and the real run asks once (docs/adr/0006-tower-clean-checks-then-removes-and-the-hook-judges-it.md).

Runs under whatever `python3` the plugin finds (3.9 on a stock Mac), like the rest of the core.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from tower import ledger, merge, sessions, state
from tower.gitrepo import GitWorktree, NotARepo, git, list_worktrees, repo_name
from tower.merge import Gh, GhResult, Say

ORCA_TIMEOUT_SECONDS = 60  # one orca call; removing a worktree is the slowest
PULL_TIMEOUT_SECONDS = 120
GIT_REMOVE_TIMEOUT_SECONDS = 120  # git deletes every file in the worktree, node_modules included
ORCA_REMOVE_WAIT_SECONDS = 15  # Orca's worktree rm answers before the worktree is gone (Orca 1.4.237, #92)
ORCA_REMOVE_POLL_SECONDS = 0.5
WORK_GOES_ON = ("running", "needs-you", "blocked")

# A program run with a list of arguments, answering with its exit code, stdout and stderr (Orca's CLI here).
Run = Callable[[list[str]], GhResult]


class CleanError(Exception):
    """tower clean can't start: no worktree named, a name that matches none or several, or the main checkout."""


@dataclass(frozen=True)
class Target:
    """One worktree tower clean was asked about, with what its checks found. failures is empty when every check
    passed. pr_number and head are the merged PR's, None when there is none; pr_known is False when GitHub couldn't
    say (so --all can't call it unfinished). worktree is None for a half-done cleanup: the worktree is already gone,
    but branch still has its card in the Ledger."""

    worktree: GitWorktree | None
    branch: str | None
    pr_number: int | None
    head: str | None
    pr_known: bool
    card: Path | None  # the card file, when there is one
    idle_sessions: tuple[str, ...]
    failures: tuple[str, ...]

    @property
    def label(self) -> str:
        """How tower names the worktree: its branch, or (detached <sha>)."""
        return self.worktree.label if self.worktree else str(self.branch)


def run_program(program: str, cwd: Path, timeout: float) -> Run:
    """A Run for program in cwd with a timeout, never prompting (git asks for no credentials): one that isn't
    installed is exit 127 and one that times out exit 124, as in a shell; one that can't start otherwise, exit 1."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}

    def run(args: list[str]) -> GhResult:
        """Run program with args."""
        try:
            proc = subprocess.run(
                [program, *args],
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError:
            return GhResult(127, "", f"{program} isn't installed")
        except subprocess.TimeoutExpired:
            return GhResult(124, "", f"{program} {args[0]} timed out after {timeout:.0f}s")
        except OSError as error:
            return GhResult(1, "", f"{program} {args[0]} didn't run ({type(error).__name__}: {error})")
        return GhResult(proc.returncode, proc.stdout, proc.stderr)

    return run


def run_orca(cwd: Path) -> Run:
    """Orca's CLI, run in cwd."""
    return run_program("orca", cwd, ORCA_TIMEOUT_SECONDS)


def clean(
    repo_dir: Path,
    names: list[str],
    every: bool,
    dry_run: bool,
    gh: Gh,
    orca: Run,
    say: Say,
    here: Path,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    """Clean up the worktrees that `names` names (paths, branches or folder names), or with every, each one whose PR
    is merged, in the repo holding repo_dir, saying each check that failed and each step taken. A name that matches
    no worktree but a branch whose card is still in the Ledger finishes a half-done cleanup (check_gone). With
    dry_run, only check and say what would be removed. True when every worktree asked about passed its checks and
    every step that reports failure worked (a GitHub branch tower merge's rules leave is said, not a failure). Raises
    CleanError (touching nothing) when the names can't be resolved, NotARepo outside a repo."""
    worktrees = list_worktrees(repo_dir)
    main = worktrees[0]
    ledger_folder, ledger_note = ledger_for(Path(main.path))
    chosen, gone = (worktrees[1:], []) if every else resolve(worktrees, names, here, ledger_folder)
    if not chosen and not gone:
        say("no worktrees besides the main checkout, so nothing to clean up")
        return True
    listed, live_note = sessions.read_agents() if chosen else ([], "")  # a half-done cleanup has no sessions
    live = None if listed is None else state.live_sessions(listed, [os.path.realpath(w.path) for w in chosen])
    targets = [check(w, gh, live, live_note, ledger_folder, ledger_note, here) for w in chosen]
    targets += [check_gone(branch, Path(main.path), gh, ledger_folder, ledger_note) for branch in gone]
    if every:
        unfinished = [t for t in targets if t.pr_known and t.pr_number is None]
        for target in unfinished:  # say why each is left, without listing it as a failure
            say(f"{target.label}: skipped, {target.failures[0]}")
        targets = [t for t in targets if t not in unfinished]
        if not targets:
            say("no worktree has a merged PR, so nothing to clean up")
            return True
    all_passed = True
    for target in targets:
        if target.failures:
            all_passed = False
            say(f"{target.label}: left as it is, since {len(target.failures)} check(s) failed:")
            for failure in target.failures:
                say(f"  - {failure}")
        else:
            passed = f"every check passed (PR #{target.pr_number} merged at {str(target.head)[:7]})"
            say(f"{target.label}: {passed}" + ("" if target.worktree else "; its worktree is already gone"))
    ready = [t for t in targets if not t.failures]
    if not ready:
        return False
    managed: set[str] = set()
    if any(t.worktree for t in ready):  # a half-done cleanup has no worktree for Orca to manage
        found, orca_error = orca_worktrees(orca)
        if found is None:
            ready = [t for t in ready if t.worktree is None]  # half-done cleanups don't need Orca
            what = "no worktree was removed" if ready else "nothing was removed"
            say(f"Orca couldn't say which worktrees it manages ({orca_error}), so {what}: run it again")
            if not ready:
                return False
            all_passed = False
        else:
            managed = found
    if dry_run:
        for target in ready:
            say(f"{target.label}: would {plan(target, managed_by_orca(target, managed))}")
        say(f"would then run git pull --ff-only in {main.path}")
        return all_passed
    worked, removed_any = True, False
    for target in ready:  # every one, even after one fails: each worktree is independent
        via_orca = managed_by_orca(target, managed)
        removed, steps_worked = remove(target, Path(main.path), ledger_folder, via_orca, gh, orca, say, sleep, clock)
        worked, removed_any = worked and steps_worked, removed_any or removed
    if removed_any:
        pull_main(Path(main.path), say)
    return all_passed and worked


def resolve(
    worktrees: list[GitWorktree], names: list[str], here: Path, ledger_folder: Path | None
) -> tuple[list[GitWorktree], list[str]]:
    """The worktrees that `names` names, each once, in the order named: by path (from here), branch, label or folder
    name; and the names that match no worktree but are a branch whose card is still in the Ledger (a half-done
    cleanup). Raises CleanError for no name, a name matching several worktrees, or none and no card, or the main
    checkout."""
    if not names:
        raise CleanError("name the worktrees to clean up (a path, branch or folder name), or pass --all")
    chosen: list[GitWorktree] = []
    gone: list[str] = []
    for name in names:
        path = os.path.realpath(here / name)
        matches = [
            w
            for w in worktrees
            if os.path.realpath(w.path) == path or name in (w.branch, w.label, os.path.basename(w.path))
        ]
        if (
            not matches
            and ledger_folder is not None
            and (ledger_folder / ledger.card_name(name, "HEAD", here)).is_file()
        ):
            if name not in gone:
                gone.append(name)
            continue
        if not matches:
            known = ", ".join(w.label for w in worktrees[1:]) or "none"
            raise CleanError(f"no worktree is {name!r} (the worktrees besides the main checkout: {known})")
        if len(matches) > 1:
            raise CleanError(f"{name!r} names several worktrees ({', '.join(w.path for w in matches)}): use its path")
        if matches[0] == worktrees[0]:
            raise CleanError(f"{name!r} is the main checkout, which is never cleaned up")
        if matches[0] not in chosen:
            chosen.append(matches[0])
    return chosen, gone


def ledger_for(main_checkout: Path) -> tuple[Path | None, str]:
    """The repo's Ledger folder, or None and why it can't be found."""
    try:
        conf = ledger.read_conf(ledger.conf_path())
    except ledger.LedgerError as error:
        return None, str(error)
    return ledger.ledger_dir(repo_name(main_checkout), conf)


def check(
    worktree: GitWorktree,
    gh: Gh,
    live: dict[str, list[state.Session]] | None,
    live_note: str,
    ledger_folder: Path | None,
    ledger_note: str,
    here: Path,
) -> Target:
    """Run every check on worktree (none stops the others, so every failure is said) and return what they found."""
    path = Path(worktree.path)
    if not path.is_dir():  # removed outside tower: git still lists it until it is pruned
        gone = "its folder is gone (removed outside tower): git worktree prune in the main checkout forgets it"
        return Target(worktree, worktree.branch, None, None, True, None, (), (gone,))
    failures: list[str] = []
    pr_number, head, pr_failure, pr_known = merged_pr(gh, worktree.branch)
    if pr_failure:
        failures.append(pr_failure)
    current = git(path, "rev-parse", "HEAD")
    if head is not None and current != head:
        failures.append(
            f"its HEAD ({str(current)[:7] if current else 'unreadable'}) isn't PR #{pr_number}'s head ({head[:7]}),"
            " so the merge may not have every commit"
        )
    # Explicit, so status.showUntrackedFiles=no or a submodule setting can't hide a file git worktree remove deletes
    status = git(path, "status", "--porcelain", "--untracked-files=all", "--ignore-submodules=none")
    if status is None:
        failures.append("git couldn't say whether it has uncommitted changes")
    elif status:
        changed = status.splitlines()
        failures.append(f"it has {state.plural(len(changed), 'uncommitted or untracked file')}")
    stash_failure = stashed_on(path, worktree.branch)
    if stash_failure:
        failures.append(stash_failure)
    card, card_failure = card_of(worktree.branch, worktree.sha, path, ledger_folder, ledger_note)
    if card_failure:
        failures.append(card_failure)
    idle: list[str] = []
    if live is None:
        failures.append(f"{live_note}, so a session busy or waiting there can't be ruled out")
    else:
        for session in live.get(os.path.realpath(worktree.path), []):
            blocker = session_blocker(session)
            if blocker:
                failures.append(blocker)
            else:
                idle.append(session.name)
    if sessions.owning_worktree(os.path.realpath(here), [os.path.realpath(worktree.path)]):
        failures.append("tower clean is running inside it: run it from the main checkout")
    return Target(worktree, worktree.branch, pr_number, head, pr_known, card, tuple(idle), tuple(failures))


def check_gone(branch: str, main: Path, gh: Gh, ledger_folder: Path | None, ledger_note: str) -> Target:
    """Run the checks that still apply to a half-done cleanup, whose worktree is already gone (Orca removed it after
    tower stopped waiting): its PR is merged, its local branch (when it is still there) is at the PR's head, no stash
    entry was made on it, and its card doesn't say work goes on. With no worktree, nothing can be uncommitted and no
    session can be there; open PRs based on it are delete_head_branch's to check, as always."""
    failures: list[str] = []
    pr_number, head, pr_failure, pr_known = merged_pr(gh, branch)
    if pr_failure:
        failures.append(pr_failure)
    tip = local_tip(main, branch)
    if tip is None:
        failures.append(f"git couldn't say where the local branch {branch} points")
    elif head is not None and tip and tip != head:
        failures.append(
            f"its local branch ({tip[:7]}) isn't PR #{pr_number}'s head ({head[:7]}), so the merge may not have every"
            " commit"
        )
    stash_failure = stashed_on(main, branch)
    if stash_failure:
        failures.append(stash_failure)
    card, card_failure = card_of(branch, "HEAD", main, ledger_folder, ledger_note)
    if card_failure:
        failures.append(card_failure)
    return Target(None, branch, pr_number, head, pr_known, card, (), tuple(failures))


def session_blocker(session: state.Session) -> str:
    """Why a live session stops cleanup: it is busy or waiting on you; "" when it is idle."""
    if session.activity == "idle":
        return ""
    return f"session {session.name} {'is busy' if session.activity == 'busy' else 'is waiting on you'} there"


def merged_pr(gh: Gh, branch: str | None) -> tuple[int | None, str | None, str, bool]:
    """The branch's merged PR (its number and head commit), or why there is none to go by, and whether GitHub said
    (False when it couldn't be read). An open PR on the branch means work goes on; of several merged ones (a reused
    branch), the newest counts."""
    if branch is None:
        return None, None, "it is on a detached HEAD, so it has no branch or PR", True
    listed = gh(
        ["pr", "list", "--head", branch, "--state", "all", "--json", "number,state,headRefOid,isCrossRepository"]
    )
    try:
        found = json.loads(listed.out) if listed.code == 0 else None
    except ValueError:
        found = None
    if not isinstance(found, list):
        return None, None, f"couldn't read {branch}'s PR from GitHub ({merge.gh_reason(listed)})", False
    pulls = [p for p in found if isinstance(p, dict) and isinstance(p.get("number"), int)]
    pulls = [p for p in pulls if not p.get("isCrossRepository")]  # a fork's same-named branch isn't this one
    opened = [p["number"] for p in pulls if p.get("state") == "OPEN"]
    if opened:
        return None, None, f"PR #{opened[0]} on {branch} is open, not merged", True
    merged = sorted((p for p in pulls if p.get("state") == "MERGED"), key=lambda p: int(p["number"]))
    if not merged:
        return None, None, f"{branch} has no merged PR", True
    newest = merged[-1]
    head = newest.get("headRefOid")
    if not isinstance(head, str) or not state.COMMIT_ID.fullmatch(head):
        return None, None, f"GitHub didn't give PR #{newest['number']}'s head commit", False
    return int(newest["number"]), head, "", True


def local_tip(main: Path, branch: str) -> str | None:
    """The commit the local branch points at, "" when there is no such branch, or None when git can't say. (Only the
    exact ref counts: for-each-ref's pattern would also match branches under BRANCH/.)"""
    ref = f"refs/heads/{branch}"
    listed = git(main, "for-each-ref", "--format=%(refname) %(objectname)", ref)
    if listed is None:
        return None
    return next((line.split()[1] for line in listed.splitlines() if line.split()[0] == ref), "")


def stashed_on(path: Path, branch: str | None) -> str:
    """Why the stash stops cleanup: entries were made on branch, or git can't list them; "" when neither."""
    stashes = stash_entries(path, branch)
    if stashes is None:
        return "git couldn't list the stash"
    if not stashes:
        return ""
    return (
        f"{state.plural(len(stashes), 'stash entry')} {'was' if len(stashes) == 1 else 'were'} made on {branch}:"
        f" {stashes[0]}"
    )


def stash_entries(path: Path, branch: str | None) -> list[str] | None:
    """The subjects of the stash entries made on branch (git names it in each: "WIP on BRANCH: ..." or "On BRANCH:
    ..."); None if git can't list them. A detached HEAD's entries say "(no branch)", which can't be told apart."""
    out = git(path, "stash", "list", "--format=%gs")
    if out is None:
        return None
    if branch is None:
        return []
    return [line for line in out.splitlines() if line.startswith((f"WIP on {branch}: ", f"On {branch}: "))]


def card_of(
    branch: str | None, sha: str, path: Path, ledger_folder: Path | None, ledger_note: str
) -> tuple[Path | None, str]:
    """The card file of the worktree on branch (at sha in path when detached; None when it has none), and why it fails
    the check: it says work goes on, or it can't be read."""
    if ledger_folder is None:
        return None, f"{ledger_note}, so its card couldn't be checked"
    card_file = ledger_folder / ledger.card_name(branch, sha, path)
    try:
        card = state.read_card(card_file, time.time())
    except OSError as error:
        return card_file, f"its card {card_file} couldn't be read ({error.strerror or type(error).__name__})"
    if card is None:
        return None, ""
    if card.status in WORK_GOES_ON:
        return card_file, f"its card says {card.status}, so work may go on there (a reused branch?)"
    return card_file, ""


def orca_worktrees(orca: Run) -> tuple[set[str] | None, str]:
    """The real paths of the worktrees Orca manages: none when Orca isn't running (`orca status --json` isn't ok with
    result.runtime.reachable, as the hub decides it), or None and why when Orca times out or runs but can't list them
    (then removing anything with git could leave its terminals open in a deleted folder)."""
    asked = orca(["status", "--json"])
    if asked.code == 124:  # it is there but didn't answer: it may well manage these worktrees
        return None, merge.gh_reason(asked)
    status, _ = orca_answer(asked)
    if merge.json_at(status, "runtime", "reachable") is not True:  # not installed, not started, or not reachable
        return set(), ""
    answer, error = orca_answer(orca(["worktree", "list", "--json"]))
    listed = merge.json_at(answer, "worktrees")
    if not isinstance(listed, list):
        return None, error or "its answer had no worktree list"
    paths = [item.get("path") for item in listed if isinstance(item, dict)]
    return {os.path.realpath(p) for p in paths if isinstance(p, str)}, ""


def orca_answer(result: GhResult) -> tuple[dict[str, object] | None, str]:
    """An orca --json call's result object, or None and Orca's error message (it can exit 0 with ok: false)."""
    body = merge.json_body(result)
    if result.code == 0 and body.get("ok") is True:
        found = body.get("result")
        return (found if isinstance(found, dict) else {}), ""
    message = merge.json_at(body, "error", "message")
    return None, str(message) if message else merge.gh_reason(result)


def managed_by_orca(target: Target, managed: set[str]) -> bool:
    """Whether Orca manages target's worktree (never, once it is gone)."""
    return target.worktree is not None and os.path.realpath(target.worktree.path) in managed


def plan(target: Target, via_orca: bool) -> str:
    """What removing target does, for --dry-run: through Orca when it manages the worktree, else with git; only the
    branch and card steps when the worktree is already gone."""
    branch = target.branch
    card = f", and archive its card {target.card.name}" if target.card else ""
    if target.worktree is None:
        return f"delete {branch} here and on GitHub (when that's safe){card} (its worktree is already gone)"
    how = "close its terminals and remove it through Orca" if via_orca else "remove it with git worktree remove"
    if target.idle_sessions and not via_orca:
        how += f" (Orca doesn't manage it, so session(s) {', '.join(target.idle_sessions)} stay open there)"
    return (
        f"{how} ({target.worktree.path}, git-ignored files included), delete {branch} here and on GitHub (when"
        f" that's safe){card}"
    )


def remove(
    target: Target,
    main: Path,
    ledger_folder: Path | None,
    via_orca: bool,
    gh: Gh,
    orca: Run,
    say: Say,
    sleep: Callable[[float], None],
    clock: Callable[[], float],
) -> tuple[bool, bool]:
    """Remove one worktree that passed every check (through Orca when via_orca), saying each step: whether the
    worktree was removed (or already gone), and whether every step worked. When the worktree can't be removed, nothing
    after it runs."""
    label, branch = target.label, str(target.branch)
    if target.worktree is not None and not remove_worktree(target.worktree, main, via_orca, orca, say, sleep, clock):
        return False, False
    worked = True
    try:
        pull = merge.find_pull(target.pr_number, gh)
    except merge.MergeError as error:
        say(f"{label}: left {branch} on GitHub: {error}")
        worked = False
    else:
        worked = merge.delete_head_branch(pull, gh, lambda line: say(f"{label}: {line}"), sleep, clock) and worked
    if git(main, "update-ref", "-d", f"refs/heads/{branch}", str(target.head)) is None:
        if git(main, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}") is None:  # Orca may delete it too
            say(f"{label}: the local branch is already gone")
        else:
            say(f"{label}: kept the local branch {branch}: it no longer points at {str(target.head)[:7]}")
            worked = False
    else:
        git(main, "config", "--remove-section", f"branch.{branch}")  # its upstream; fails only when it has none
        say(f"{label}: deleted the local branch {branch}")
    card_archived = True
    if target.card is not None and ledger_folder is not None:
        try:
            say(f"{label}: archived its card to {ledger.archive(target.card, ledger_folder)}")
        except OSError as error:
            say(f"{label}: couldn't archive its card {target.card} ({error.strerror or type(error).__name__})")
            worked = card_archived = False
    if card_archived and ledger_folder is not None:  # a log stays with a card that stays; one with no card goes too
        card_file = target.card or ledger_folder / ledger.card_name(branch, str(target.head), main)
        try:
            log = ledger.archive_decisions(card_file)
        except OSError as error:
            say(f"{label}: couldn't archive its decisions log ({error.strerror or type(error).__name__})")
            worked = False
        else:
            if log is not None:
                say(f"{label}: archived its decisions log to {log}")
    if target.worktree is not None and target.idle_sessions and not via_orca:
        say(
            f"{label}: session(s) {', '.join(target.idle_sessions)} still open in {target.worktree.path}, which is"
            " gone: close them"
        )
    return True, worked


def busy_now(path: str) -> str:
    """Why the worktree at path can't lose its terminals right now: a live session there is busy or waiting, or live
    sessions can't be read; "" when neither."""
    listed, live_note = sessions.read_agents()
    if listed is None:
        return f"{live_note}, so a session busy or waiting there can't be ruled out"
    found = state.live_sessions(listed, [os.path.realpath(path)]).get(os.path.realpath(path), [])
    return next((blocker for blocker in map(session_blocker, found) if blocker), "")


def remove_worktree(
    worktree: GitWorktree,
    main: Path,
    via_orca: bool,
    orca: Run,
    say: Say,
    sleep: Callable[[float], None],
    clock: Callable[[], float],
) -> bool:
    """Close the worktree's terminals and remove it through Orca when Orca manages it, else with git worktree
    remove; neither is forced, so either refuses a worktree that changed since the checks. True once git no longer
    lists it: Orca's worktree rm answers before the worktree is gone, so after it git is asked again every
    ORCA_REMOVE_POLL_SECONDS for up to ORCA_REMOVE_WAIT_SECONDS."""
    label, path = worktree.label, worktree.path
    busy = busy_now(path)
    if busy:
        say(f"{label}: left as it is: {busy} now")
        return False
    if via_orca:
        _, error = orca_answer(orca(["terminal", "close", "--worktree", f"path:{path}", "--all", "--json"]))
        if error:
            say(f"{label}: left as it is: Orca couldn't close its terminals ({error})")
            return False
        say(f"{label}: closed its terminals")
        _, error = orca_answer(orca(["worktree", "rm", "--worktree", f"path:{path}", "--json"]))
        if error:
            say(
                f"{label}: Orca closed its terminals but couldn't remove the worktree ({error}), so nothing else"
                " was removed"
            )
            return False
        how, wait = "through Orca", ORCA_REMOVE_WAIT_SECONDS
    else:
        removed = run_program("git", main, GIT_REMOVE_TIMEOUT_SECONDS)(["worktree", "remove", path])
        if removed.code != 0:
            say(f"{label}: git couldn't remove the worktree ({merge.gh_reason(removed)}), so nothing else was removed")
            return False
        how, wait = "with git (Orca isn't running or doesn't manage it)", 0
    deadline = clock() + wait
    while still_listed(path, main):
        if clock() >= deadline:
            finish = (
                f": once git no longer lists it, tower clean {worktree.branch} finishes the rest"
                if worktree.branch
                else ""
            )
            say(f"{label}: the worktree is still listed by git after removing it, so nothing else was removed{finish}")
            return False
        sleep(ORCA_REMOVE_POLL_SECONDS)
    say(f"{label}: removed the worktree {path} {how}")
    return True


def still_listed(path: str, main: Path) -> bool:
    """Whether git still lists a worktree at path; True when git can't list them (Orca may be mid-removal), so the
    wait goes on rather than calling it gone."""
    try:
        listed = list_worktrees(main)
    except NotARepo:
        return True
    return any(os.path.realpath(w.path) == os.path.realpath(path) for w in listed)


def pull_main(main: Path, say: Say) -> None:
    """git pull --ff-only in the main checkout, so it has the merged work. A pull that fails (local changes in the way,
    no network) is said, but doesn't fail the cleanup, which is done by then."""
    if git(main, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}") is None:
        say(f"the main checkout's branch has no upstream, so nothing was pulled into {main}")
        return
    pulled = run_program("git", main, PULL_TIMEOUT_SECONDS)(["pull", "--ff-only"])
    if pulled.code != 0:
        say(f"git pull --ff-only in {main} failed ({merge.gh_reason(pulled)}); the cleanup itself is done")
        return
    say(f"pulled the merged work into {main} (git pull --ff-only)")
