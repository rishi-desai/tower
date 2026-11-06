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
`orca worktree rm`, never forced), or with `git worktree remove` (never forced) when Orca isn't running or doesn't
manage it; the local branch is deleted only if it still points at the checked commit (`git update-ref -d`); its branch
on GitHub is deleted by tower merge's rules (merge.delete_head_branch: never while open PRs are based on it, never an
integration branch, never once it moved); its card moves to the Ledger's _archive folder. Once every worktree is done,
the main checkout runs `git pull --ff-only`. A step that fails stops that worktree's later steps, and is said.

--all takes every worktree but the main checkout whose branch's PR is merged, and says why each other one was left.
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
from datetime import datetime, timezone
from pathlib import Path

from tower import ledger, merge, sessions, state
from tower.gitrepo import GitWorktree, git, list_worktrees, repo_name
from tower.merge import Gh, GhResult, Say

ORCA_TIMEOUT_SECONDS = 60  # one orca call; removing a worktree is the slowest
PULL_TIMEOUT_SECONDS = 120
WORK_GOES_ON = ("running", "needs-you", "blocked")
ARCHIVE = "_archive"

# A program run with a list of arguments, answering with its exit code, stdout and stderr (Orca's CLI here).
Run = Callable[[list[str]], GhResult]


class CleanError(Exception):
    """tower clean can't start: no worktree named, a name that matches none or several, or the main checkout."""


@dataclass(frozen=True)
class Target:
    """One worktree tower clean was asked about, with what its checks found. failures is empty when every check
    passed. pr_number and head are the merged PR's, None when there is none."""

    worktree: GitWorktree
    pr_number: int | None
    head: str | None
    card: Path | None  # the card file, when there is one
    idle_sessions: tuple[str, ...]
    failures: tuple[str, ...]

    @property
    def label(self) -> str:
        """How tower names the worktree: its branch, or (detached <sha>)."""
        return self.worktree.label


def run_program(program: str, cwd: Path, timeout: float) -> Run:
    """A Run for program in cwd with a timeout, never prompting; one that can't start or times out is exit 1."""

    def run(args: list[str]) -> GhResult:
        """Run program with args."""
        try:
            proc = subprocess.run(
                [program, *args],
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
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
    """Clean up the worktrees names names (paths, branches or folder names), or with every, each one whose PR is
    merged, in the repo holding repo_dir, saying each check that failed and each step taken. With dry_run, only
    check and say what would be removed. True when every worktree asked about passed its checks and every step
    worked. Raises CleanError (touching nothing) when the names can't be resolved, NotARepo outside a repo."""
    worktrees = list_worktrees(repo_dir)
    main = worktrees[0]
    chosen = worktrees[1:] if every else resolve(worktrees, names, here)
    if not chosen:
        say("no worktrees besides the main checkout, so nothing to clean up")
        return True
    listed, live_note = sessions.read_agents()
    live = None if listed is None else state.live_sessions(listed, [os.path.realpath(w.path) for w in chosen])
    ledger_folder, ledger_note = ledger_for(Path(main.path))
    targets = [check(w, gh, live, live_note, ledger_folder, ledger_note, here) for w in chosen]
    if every:
        targets = [t for t in targets if t.pr_number is not None or skip(t, say)]
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
            say(f"{target.label}: every check passed (PR #{target.pr_number} merged at {str(target.head)[:7]})")
    ready = [t for t in targets if not t.failures]
    if not ready:
        return False
    use_orca = orca_running(orca)
    if dry_run:
        for target in ready:
            say(f"{target.label}: would {plan(target, use_orca)}")
        say(f"would then run git pull --ff-only in {main.path}")
        return all_passed
    worked = True
    for target in ready:  # every one, even after one fails: each worktree is independent
        worked = remove(target, Path(main.path), ledger_folder, use_orca, gh, orca, say, sleep, clock) and worked
    pulled = pull_main(Path(main.path), say)
    return all_passed and worked and pulled


def resolve(worktrees: list[GitWorktree], names: list[str], here: Path) -> list[GitWorktree]:
    """The worktrees names names, each once, in the order named: by path (from here), branch, label or folder name.
    Raises CleanError for no name, a name matching none or several, or the main checkout."""
    if not names:
        raise CleanError("name the worktrees to clean up (a path, branch or folder name), or pass --all")
    chosen: list[GitWorktree] = []
    for name in names:
        path = os.path.realpath(here / name)
        matches = [
            w
            for w in worktrees
            if os.path.realpath(w.path) == path or name in (w.branch, w.label, os.path.basename(w.path))
        ]
        if not matches:
            known = ", ".join(w.label for w in worktrees[1:]) or "none"
            raise CleanError(f"no worktree is {name!r} (the worktrees besides the main checkout: {known})")
        if len(matches) > 1:
            raise CleanError(f"{name!r} names several worktrees ({', '.join(w.path for w in matches)}): use its path")
        if matches[0] == worktrees[0]:
            raise CleanError(f"{name!r} is the main checkout, which is never cleaned up")
        if matches[0] not in chosen:
            chosen.append(matches[0])
    return chosen


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
    failures: list[str] = []
    path = Path(worktree.path)
    pr_number, head, pr_failure = merged_pr(gh, worktree.branch)
    if pr_failure:
        failures.append(pr_failure)
    current = git(path, "rev-parse", "HEAD")
    if head is not None and current != head:
        failures.append(
            f"its HEAD ({str(current)[:7] if current else 'unreadable'}) isn't PR #{pr_number}'s head ({head[:7]}),"
            " so the merge may not have every commit"
        )
    status = git(path, "status", "--porcelain")
    if status is None:
        failures.append("git couldn't say whether it has uncommitted changes")
    elif status:
        changed = status.splitlines()
        failures.append(f"it has {state.plural(len(changed), 'uncommitted or untracked file')}")
    stashes = stash_entries(path, worktree.branch)
    if stashes is None:
        failures.append("git couldn't list the stash")
    elif stashes:
        failures.append(f"{state.plural(len(stashes), 'stash entry')} {'was' if len(stashes) == 1 else 'were'} made"
                        f" on {worktree.branch}: {stashes[0]}")  # fmt: skip
    card, card_failure = card_of(worktree, path, ledger_folder, ledger_note)
    if card_failure:
        failures.append(card_failure)
    idle: list[str] = []
    if live is None:
        failures.append(f"{live_note}, so a session busy or waiting there can't be ruled out")
    else:
        for session in live.get(os.path.realpath(worktree.path), []):
            if session.activity == "idle":
                idle.append(session.name)
            else:
                doing = "is busy" if session.activity == "busy" else "is waiting on you"
                failures.append(f"session {session.name} {doing} there")
    if sessions.owning_worktree(os.path.realpath(here), [os.path.realpath(worktree.path)]):
        failures.append("tower clean is running inside it: run it from the main checkout")
    return Target(worktree, pr_number, head, card, tuple(idle), tuple(failures))


def merged_pr(gh: Gh, branch: str | None) -> tuple[int | None, str | None, str]:
    """The branch's merged PR (its number and head commit), or why there is none to go by. An open PR on the branch
    means work goes on; of several merged ones (a reused branch), the newest counts."""
    if branch is None:
        return None, None, "it is on a detached HEAD, so it has no branch or PR"
    listed = gh(
        ["pr", "list", "--head", branch, "--state", "all", "--json", "number,state,headRefOid,isCrossRepository"]
    )
    try:
        found = json.loads(listed.out) if listed.code == 0 else None
    except ValueError:
        found = None
    if not isinstance(found, list):
        return None, None, f"couldn't read {branch}'s PR from GitHub ({merge.gh_reason(listed)})"
    pulls = [p for p in found if isinstance(p, dict) and isinstance(p.get("number"), int)]
    pulls = [p for p in pulls if not p.get("isCrossRepository")]  # a fork's same-named branch isn't this one
    opened = [p["number"] for p in pulls if p.get("state") == "OPEN"]
    if opened:
        return None, None, f"PR #{opened[0]} on {branch} is open, not merged"
    merged = sorted((p for p in pulls if p.get("state") == "MERGED"), key=lambda p: int(p["number"]))
    if not merged:
        return None, None, f"{branch} has no merged PR"
    newest = merged[-1]
    head = newest.get("headRefOid")
    if not isinstance(head, str) or not state.COMMIT_ID.fullmatch(head):
        return None, None, f"GitHub didn't give PR #{newest['number']}'s head commit"
    return int(newest["number"]), head, ""


def stash_entries(path: Path, branch: str | None) -> list[str] | None:
    """The subjects of the stash entries made on branch (git names it in each: "WIP on BRANCH: ..." or "On BRANCH:
    ..."); None if git can't list them. A detached HEAD's entries say "(no branch)", which can't be told apart."""
    out = git(path, "stash", "list", "--format=%gs")
    if out is None:
        return None
    if branch is None:
        return []
    return [line for line in out.splitlines() if line.startswith((f"WIP on {branch}: ", f"On {branch}: "))]


def card_of(worktree: GitWorktree, path: Path, ledger_folder: Path | None, ledger_note: str) -> tuple[Path | None, str]:
    """The worktree's card file (None when it has none), and why it fails the check: it says work goes on, or it
    can't be read."""
    if ledger_folder is None:
        return None, f"{ledger_note}, so its card couldn't be checked"
    card_file = ledger_folder / ledger.card_name(worktree.branch, worktree.sha, path)
    try:
        card = state.read_card(card_file, time.time())
    except OSError as error:
        return card_file, f"its card {card_file} couldn't be read ({error.strerror or type(error).__name__})"
    if card is None:
        return None, ""
    if card.status in WORK_GOES_ON:
        return card_file, f"its card says {card.status}, so work may go on there (a reused branch?)"
    return card_file, ""


def skip(target: Target, say: Say) -> bool:
    """Say why --all leaves a worktree with no merged PR; always False, to drop it from the targets."""
    say(f"{target.label}: skipped, {target.failures[0]}")
    return False


def orca_running(orca: Run) -> bool:
    """Whether Orca's runtime answers (`orca status --json`: ok, and result.runtime.reachable)."""
    answer, _ = orca_answer(orca(["status", "--json"]))
    return merge.json_at(answer, "runtime", "reachable") is True


def orca_answer(result: GhResult) -> tuple[dict[str, object] | None, str]:
    """An orca --json call's result object, or None and Orca's error message (it can exit 0 with ok: false)."""
    body = merge.json_body(result)
    if result.code == 0 and body.get("ok") is True:
        found = body.get("result")
        return (found if isinstance(found, dict) else {}), ""
    message = merge.json_at(body, "error", "message")
    return None, str(message) if message else merge.gh_reason(result)


def orca_manages(orca: Run, path: str) -> bool:
    """Whether Orca lists path among its worktrees."""
    answer, _ = orca_answer(orca(["worktree", "list", "--json"]))
    listed = merge.json_at(answer, "worktrees")
    paths = [item.get("path") for item in listed if isinstance(item, dict)] if isinstance(listed, list) else []
    return os.path.realpath(path) in {os.path.realpath(p) for p in paths if isinstance(p, str)}


def plan(target: Target, use_orca: bool) -> str:
    """What removing target does, for --dry-run."""
    branch = target.worktree.branch
    how = "close its terminals and remove it through Orca" if use_orca else "remove it with git worktree remove"
    card = f", and archive its card {target.card.name}" if target.card else ""
    return f"{how} ({target.worktree.path}), delete {branch} here and on GitHub (when that's safe){card}"


def remove(
    target: Target,
    main: Path,
    ledger_folder: Path | None,
    use_orca: bool,
    gh: Gh,
    orca: Run,
    say: Say,
    sleep: Callable[[float], None],
    clock: Callable[[], float],
) -> bool:
    """Remove one worktree that passed every check, saying each step; True when every step worked. A failed step
    stops the steps after it."""
    label, path, branch = target.label, target.worktree.path, str(target.worktree.branch)
    if not remove_worktree(target, main, use_orca, orca, say):
        return False
    worked = True
    tip = git(main, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
    if tip is None:
        say(f"{label}: the local branch is already gone")
    elif git(main, "update-ref", "-d", f"refs/heads/{branch}", str(target.head)) is None:
        say(f"{label}: kept the local branch {branch}: it no longer points at {str(target.head)[:7]}")
        worked = False
    else:
        say(f"{label}: deleted the local branch {branch}")
    try:
        pull = merge.find_pull(target.pr_number, gh)
    except merge.MergeError as error:
        say(f"{label}: left {branch} on GitHub: {error}")
        worked = False
    else:
        merge.delete_head_branch(pull, gh, lambda line: say(f"{label}: {line}"), sleep, clock)
    if target.card is not None and ledger_folder is not None:
        archived = archive_card(target.card, ledger_folder)
        if archived is None:
            say(f"{label}: couldn't archive its card {target.card}")
            worked = False
        else:
            say(f"{label}: archived its card to {archived}")
    if target.idle_sessions and not use_orca:
        say(f"{label}: session(s) {', '.join(target.idle_sessions)} still open in {path}, which is gone: close them")
    return worked


def remove_worktree(target: Target, main: Path, use_orca: bool, orca: Run, say: Say) -> bool:
    """Close the worktree's terminals and remove it through Orca when Orca manages it, else with git worktree
    remove; neither is forced, so either refuses a worktree that changed since the checks. True once git no longer
    lists it."""
    label, path = target.label, target.worktree.path
    if use_orca and orca_manages(orca, path):
        _, error = orca_answer(orca(["terminal", "close", "--worktree", f"path:{path}", "--all", "--json"]))
        if error:
            say(f"{label}: left as it is: Orca couldn't close its terminals ({error})")
            return False
        say(f"{label}: closed its terminals")
        _, error = orca_answer(orca(["worktree", "rm", "--worktree", f"path:{path}", "--json"]))
        if error:
            say(f"{label}: Orca couldn't remove the worktree ({error}), so nothing else was removed")
            return False
        how = "through Orca"
    else:
        removed = run_program("git", main, PULL_TIMEOUT_SECONDS)(["worktree", "remove", path])
        if removed.code != 0:
            say(f"{label}: git couldn't remove the worktree ({merge.gh_reason(removed)}), so nothing else was removed")
            return False
        how = "with git (Orca isn't running or doesn't manage it)"
    if any(os.path.realpath(w.path) == os.path.realpath(path) for w in list_worktrees(main)):
        say(f"{label}: the worktree is still listed by git after removing it, so nothing else was removed")
        return False
    say(f"{label}: removed the worktree {path} {how}")
    return True


def archive_card(card: Path, ledger_folder: Path) -> Path | None:
    """Move card into the Ledger's _archive folder, under a timestamped name when one by its name is already there (a
    reused branch); where it went, or None if it couldn't be moved."""
    archive = ledger_folder / ARCHIVE
    target = archive / card.name
    if target.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = archive / f"{card.stem}.{stamp}{card.suffix}"
    try:
        archive.mkdir(exist_ok=True)
        card.rename(target)
    except OSError:
        return None
    return target


def pull_main(main: Path, say: Say) -> bool:
    """git pull --ff-only in the main checkout, so it has the merged work; True when it pulled or has nothing to pull
    from (no upstream)."""
    if git(main, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}") is None:
        say(f"the main checkout's branch has no upstream, so nothing was pulled into {main}")
        return True
    pulled = run_program("git", main, PULL_TIMEOUT_SECONDS)(["pull", "--ff-only"])
    if pulled.code != 0:
        say(f"git pull --ff-only in {main} failed: {merge.gh_reason(pulled)}")
        return False
    say(f"pulled the merged work into {main} (git pull --ff-only)")
    return True
