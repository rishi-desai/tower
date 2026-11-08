"""cli.py: the `tower` command.

tower --version            print tower's version
tower events tail [-n N]   print the last N events (default 10) of this month's log, as JSON lines
tower state [--repo PATH | --all] [--json] [--pr]
                           each worktree's effective state (see state.py), as text or JSON; with --all, every repo
                           in the event log, this one, and any with a Ledger folder in the vault (see multirepo.py)
tower scan [--no-github]   the hub's view of this repo: state, live sessions, GitHub, backlog (see scan.py)
tower adopt-scan           facts for bringing an in-flight repo under tower (see adopt.py)
tower card-path [DIR]      where the worktree holding DIR (default: here) keeps its status card (see ledger.py)
tower card write [--status running|done|needs-you|blocked] [--reason TEXT] [--ticket N] [--dir DIR] [--if-absent]
                           render this worktree's card from facts and the narrative sections on stdin (see card.py)
tower card kickoff [--dir DIR] [--replace]
                           put the hub's kickoff (stdin) into the worktree's card, where card write keeps it (see card.py)
tower decisions add TEXT [--dir DIR]
                           add one dated line to this worktree's append-only decisions log (see memory.py)
tower rules                link this project's personal rules into this worktree; prints the rules file (see rules.py)
tower env [--dir DIR]      the worktree's runtime slot and the variables its project config derives (PORT, ...), as
                           `export` lines for eval (see runtime.py)
tower stats [--since 14d] [--repo [PATH]]
                           metrics per PR merged in the window, from the event log and GitHub (see stats.py);
                           every repo in the log, or only PATH's (default: this one) with --repo
tower merge [PR]           merge PR (default: this branch's), stacked or not, by the method its shape calls for, and
                           delete its head branch when that's safe; exit 1 when nothing was merged, 3 when it isn't
                           merged yet but may be: GitHub still merging, a merge queue, or GitHub unreachable (see
                           merge.py)
tower clean [WORKTREE ...|--all] [--dry-run]
                           clean up finished workers (worktree, branch here and on GitHub, terminals; the card is
                           archived) once every check proves nothing would be lost; naming the branch of a worktree
                           already gone finishes a half-done cleanup; --dry-run only checks and says what would go;
                           exit 1 when a check failed or a step didn't work (see clean.py)
tower policy               the approval profile for this repo, why it applies, and what each key says (see policy.py)
tower policy use PROFILE [--repo]
                           make PROFILE this machine's default, or with --repo this repo's; for you to run in your own
                           terminal (the approval hook asks before an agent runs it)
tower doctor               check each thing tower depends on (Orca, gh, the vault, the plugin, git-spice, mise, the
                           policy, `claude agents --json`, the hooks), naming the fix for each problem; exit 1 when a
                           check fails, 0 when there are only warnings (see doctor.py)
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tower import (
    __version__,
    adopt,
    card,
    clean,
    doctor,
    events,
    github,
    hook_events,
    ledger,
    memory,
    merge,
    multirepo,
    policy,
    rules,
    runtime,
    scan,
    state,
    stats,
)
from tower.gitrepo import NotARepo

SINCE_SPAN = re.compile(r"(\d+)([hdw])")
SINCE_UNITS = {"h": "hours", "d": "days", "w": "weeks"}
# Merges this far before the window are fetched too, only to cut a reused branch's events at its earlier merge.
BOUNDARY_LOOKBACK = timedelta(days=30)
PR_NUMBER = re.compile(r"[1-9][0-9]*")
MAX_SINCE = timedelta(days=3650)  # a longer --since overflows datetime arithmetic, and no log is that old


def non_negative_int(text: str) -> int:
    """argparse type for -n: a whole number, 0 or more."""
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError(f"must be 0 or more, got {value}")
    return value


def pr_number(text: str) -> int:
    """argparse type for a PR: its number, digits only (the approval hook reads the same form)."""
    if not PR_NUMBER.fullmatch(text):
        raise argparse.ArgumentTypeError(f"{text!r} isn't a PR number")
    return int(text)


def since_span(text: str) -> timedelta:
    """argparse type for --since: a count and a unit, h(ours), d(ays) or w(eeks), like 14d."""
    match = SINCE_SPAN.fullmatch(text)
    if match is None:
        raise argparse.ArgumentTypeError(f"{text!r} isn't a span like 14d, 2w or 36h")
    try:
        span = timedelta(**{SINCE_UNITS[match.group(2)]: int(match.group(1))})
    except OverflowError:
        span = MAX_SINCE + timedelta(days=1)
    if span > MAX_SINCE:
        raise argparse.ArgumentTypeError(f"{text!r} is longer than {MAX_SINCE.days} days")
    return span


def parser() -> argparse.ArgumentParser:
    """The argument parser for every tower subcommand."""
    top = argparse.ArgumentParser(prog="tower", description="tower's core: facts about your parallel Claude sessions.")
    top.add_argument("--version", action="version", version=f"tower {__version__}")
    commands = top.add_subparsers(dest="command", required=True)
    events_parser = commands.add_parser("events", help="the event log tower's hooks write")
    events_commands = events_parser.add_subparsers(dest="events_command", required=True)
    tail = events_commands.add_parser("tail", help="print the last events of this month's log as JSON lines")
    tail.add_argument("-n", type=non_negative_int, default=10, help="how many events (default 10)")
    state_parser = commands.add_parser("state", help="each worktree's effective state: one truth for hub, pane and WIP")
    repo_scope = state_parser.add_mutually_exclusive_group()
    repo_scope.add_argument("--repo", type=Path, default=Path("."), help="any folder in the repo (default: here)")
    repo_scope.add_argument(
        "--all", action="store_true", help="every repo in the event log or with a Ledger folder in the vault"
    )
    state_parser.add_argument("--json", action="store_true", help="print JSON instead of the hub's text sections")
    state_parser.add_argument("--pr", action="store_true", help="also ask GitHub (gh) for each branch's PR")
    scan_parser = commands.add_parser("scan", help="the hub's view of this repo: state, sessions, GitHub, backlog")
    scan_parser.add_argument("--no-github", action="store_true", help="skip GitHub (for frequent refreshes: the pane)")
    commands.add_parser("adopt-scan", help="read-only facts for bringing an in-flight repo under tower")
    card_parser = commands.add_parser("card-path", help="where this worktree's status card lives")
    card_parser.add_argument("dir", nargs="?", type=Path, default=Path("."), help="a folder in the worktree")
    add_card_parser(commands)
    decisions_parser = commands.add_parser("decisions", help="this worktree's append-only decisions log")
    decisions_commands = decisions_parser.add_subparsers(dest="decisions_command", required=True)
    add = decisions_commands.add_parser("add", help="add one dated line: what you chose over what and why, or found")
    add.add_argument("text", help=f"the decision, one line of at most {memory.MAX_DECISION} characters")
    add.add_argument("--dir", type=Path, default=Path("."), help="a folder in the worktree (default: here)")
    commands.add_parser("rules", help="link this project's personal rules file into this worktree")
    env_parser = commands.add_parser("env", help="this worktree's runtime slot and variables, as export lines")
    env_parser.add_argument("--dir", type=Path, default=Path("."), help="a folder in the worktree (default: here)")
    stats_parser = commands.add_parser("stats", help="metrics per merged PR, from the event log and GitHub")
    stats_parser.add_argument(
        "--since", type=since_span, default=timedelta(days=14), help="how far back merges count (default 14d)"
    )
    stats_parser.add_argument(
        "--repo", nargs="?", const=".", metavar="PATH", help="only this checkout's repo (default with no PATH: here)"
    )
    merge_parser = commands.add_parser(
        "merge", help="merge a PR, stacked or not, and delete its head branch when that's safe"
    )
    merge_parser.add_argument("pr", nargs="?", type=pr_number, help="the PR's number (default: this branch's PR)")
    clean_parser = commands.add_parser(
        "clean", help="clean up finished workers, once every check proves nothing would be lost"
    )
    clean_parser.add_argument("worktrees", nargs="*", help="a worktree's path, branch or folder name")
    clean_parser.add_argument("--all", action="store_true", help="every worktree whose PR is merged")
    clean_parser.add_argument("--dry-run", action="store_true", help="only check, and say what would be removed")
    commands.add_parser("doctor", help="check each thing tower depends on, and name the fix for each problem")
    policy_parser = commands.add_parser("policy", help="the approval profile for this repo, and why it applies")
    policy_commands = policy_parser.add_subparsers(dest="policy_command")
    use = policy_commands.add_parser("use", help="switch the default profile, or this repo's (run it yourself)")
    use.add_argument("profile", help="work, personal, sandbox, or a profile your policy file defines")
    use.add_argument("--repo", action="store_true", help="for this repo only (its origin's owner/repo)")
    return top


def add_card_parser(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add the `card` command and its `write` subcommand to commands."""
    card_parser = commands.add_parser("card", help="this worktree's status card, rendered by the core")
    card_commands = card_parser.add_subparsers(dest="card_command", required=True)
    write = card_commands.add_parser(
        "write", help="render the card from facts and the narrative sections on stdin (## Done, ## Needs you, ...)"
    )
    write.add_argument(
        "--status",
        choices=card.REQUESTS,
        help="ask for a status: needs-you or blocked (with --reason), running while fixing an open PR, or done when"
        " the PR is open and only waits on the user's merge; otherwise it comes from the PR (done, waiting-ci, running)",
    )
    write.add_argument("--reason", default="", help="why needs-you or blocked, in one line")
    write.add_argument(
        "--ticket", help="the ticket, like 42 or #42, or none (default: the last card's, else the branch's)"
    )
    write.add_argument("--dir", type=Path, default=Path("."), help="a folder in the worktree (default: here)")
    write.add_argument(
        "--if-absent", action="store_true", help="write only if the worktree has no card yet (exit 3 if it has)"
    )
    kickoff = card_commands.add_parser(
        "kickoff", help="put the hub's kickoff prompt (stdin) into the worktree's card, which card write then keeps"
    )
    kickoff.add_argument("--dir", type=Path, default=Path("."), help="a folder in the worktree (default: here)")
    kickoff.add_argument(
        "--replace",
        action="store_true",
        help="the card is an orphan from earlier work on this branch name: replace its kickoff, archive its decisions"
        " log",
    )


def run_card_write(args: argparse.Namespace) -> int:
    """Run `tower card write`: print the card's path and status, warnings on stderr. Exit 1 (writing nothing) when the
    narrative, a flag, the repo or the ledger is wrong, or the card can't be written; 3 when --if-absent finds a card."""
    if sys.stdin.isatty():
        print(
            "tower: pipe the card's sections (## Done, ## Why it matters, ...) into tower card write", file=sys.stderr
        )
        return 1
    try:
        written = card.write_card(
            args.dir, sys.stdin.read(), args.status, args.reason, args.ticket, if_absent=args.if_absent
        )
    except card.CardExists as error:
        print(f"tower: {error}", file=sys.stderr)
        return 3
    except (NotARepo, ledger.LedgerError, card.CardError) as error:
        print(f"tower: {error}", file=sys.stderr)
        return 1
    except OSError as error:
        print(
            f"tower: couldn't write the card ({error.strerror or type(error).__name__}: {error.filename})",
            file=sys.stderr,
        )
        return 1
    for warning in written.warnings:
        print(f"tower: {warning}", file=sys.stderr)
    print(written.path)
    print(f"status: {written.status} ({written.why})")
    return 0


def run_card_kickoff(args: argparse.Namespace) -> int:
    """Run `tower card kickoff`: print the card's path (and where an earlier decisions log was archived). Exit 1,
    writing nothing, when the kickoff, the worktree, the ledger or the card is wrong."""
    if sys.stdin.isatty():
        print("tower: pipe the kickoff prompt into tower card kickoff", file=sys.stderr)
        return 1
    try:
        path, archived = card.set_kickoff(args.dir, sys.stdin.read(), replace=args.replace)
    except (NotARepo, ledger.LedgerError, card.CardError) as error:
        print(f"tower: {error}", file=sys.stderr)
        return 1
    except OSError as error:
        print(
            f"tower: couldn't write the kickoff ({error.strerror or type(error).__name__}: {error.filename})",
            file=sys.stderr,
        )
        return 1
    if archived is not None:
        print(f"tower: archived the earlier work's decisions log to {archived}", file=sys.stderr)
    print(path)
    return 0


def run_decisions_add(directory: Path, text: str) -> int:
    """Run `tower decisions add`: print the log's path. Exit 1, writing nothing, when the decision or the worktree is
    wrong or the log can't be written."""
    try:
        print(memory.add_decision(directory, text))
    except (NotARepo, ledger.LedgerError, memory.DecisionError) as error:
        print(f"tower: {error}", file=sys.stderr)
        return 1
    except OSError as error:
        print(
            f"tower: couldn't add the decision ({error.strerror or type(error).__name__}: {error.filename})",
            file=sys.stderr,
        )
        return 1
    return 0


def show_env(directory: Path) -> int:
    """Print the worktree's `export` lines. With no runtime config, print nothing and say where it looked (exit 0);
    exit 1, saying why, outside a repo or with a bad config."""
    try:
        env, config = runtime.worktree_env(directory)
    except (NotARepo, ledger.LedgerError, runtime.RuntimeConfigError) as error:
        print(f"tower: {error}", file=sys.stderr)
        return 1
    if env is None:
        print(f"tower: no runtime config at {config}, so nothing to export", file=sys.stderr)
        return 0
    sys.stdout.write(runtime.render_exports(env))
    return 0


def events_tail(n: int) -> int:
    """Print the last n events, oldest first; with no log yet, say where it looked."""
    directory = events.default_dir()
    lines = events.tail(directory, n)
    if not lines and not (directory / events.LOG_NAME).exists():
        print(f"tower: no events yet in {directory / events.LOG_NAME}", file=sys.stderr)
    for line in lines:
        print(line)
    return 0


def show_state(repo: Path, as_json: bool, with_pr: bool) -> int:
    """Print the repo's effective state; exit 1 (and say why) outside a git repo."""
    try:
        report = state.gather(repo, with_pr=with_pr)
    except NotARepo as error:
        print(f"tower: {error}", file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps(state.to_json(report), indent=2))
    else:
        sys.stdout.write(state.render_text(report))
    return 0


def show_all_states(as_json: bool, with_pr: bool) -> int:
    """Print every known repo's effective state; never fails for one bad repo (see multirepo.gather_all)."""
    report = multirepo.gather_all(Path("."), with_pr=with_pr)
    if as_json:
        print(json.dumps(multirepo.to_json(report), indent=2))
    else:
        sys.stdout.write(multirepo.render_text(report))
    return 0


def stats_command(since: timedelta, repo_path: str | None) -> int:
    """Print the metrics table for PRs merged in the last since, for repo_path's repo or every repo in the log.
    Exit code: 2 if repo_path isn't in a git repo, 1 if gh failed for some repo, else 0. A repo whose folder is gone,
    and a ticket whose reopens can't be read, are skipped with a warning only."""
    directory = events.default_dir()
    log, damaged = events.read_all(directory)
    if not log:
        print(f"tower: no events yet in {directory}", file=sys.stderr)
        return 0
    cutoff = datetime.now(timezone.utc) - since
    if repo_path is not None:
        try:
            only = hook_events.place(repo_path)[0]
        except subprocess.TimeoutExpired:
            print(f"tower: git timed out in {repo_path}", file=sys.stderr)
            return 2
        if only is None:
            print(f"tower: {repo_path} isn't inside a git repo", file=sys.stderr)
            return 2
        repos = [only]
    else:
        repos = sorted({e.repo for e in log if e.repo is not None})
    prs: list[stats.MergedPr] = []
    reopens: dict[tuple[str, int], list[datetime]] = {}
    failed = False
    for repo in repos:
        if not Path(repo).is_dir():
            print(f"tower: skipped {repo}: the folder is gone", file=sys.stderr)
            continue
        try:
            repo_prs = github.merged_prs(repo, cutoff - BOUNDARY_LOOKBACK)
        except github.GhError as error:
            print(f"tower: skipped {repo}: {error}", file=sys.stderr)
            failed = True
            continue
        prs += repo_prs
        # Only tickets of PRs in the window: an earlier PR's reopens never show.
        tickets = {ticket for p in repo_prs if p.merged_at >= cutoff for ticket in p.closes}
        repo_reopens, warnings = github.ticket_reopens(repo, tickets)
        reopens.update({(repo, ticket): times for ticket, times in repo_reopens.items()})
        for warning in warnings:
            print(f"tower: {repo}: {warning}", file=sys.stderr)
    result = stats.compute(log, prs, reopens, merged_since=cutoff)
    print(stats.render(result))
    left_out = damaged + result.unreadable
    if left_out:
        print(f"tower: left out {left_out} damaged line(s) of the event log", file=sys.stderr)
    return 1 if failed else 0


def merge_command(pr: int | None) -> int:
    """Run `tower merge`: say each step on stdout; exit 1 (saying why) when nothing was merged, 3 when the PR isn't
    merged yet but may be (still merging, queued, or GitHub couldn't be asked)."""
    try:
        merge.merge_pr(pr, merge.run_gh(Path(".")), lambda line: print(f"tower: {line}", flush=True))
    except merge.MergeError as error:
        print(f"tower: {error}", file=sys.stderr)
        return 1
    except merge.StillMerging as error:
        print(f"tower: {error}", file=sys.stderr)
        return 3
    return 0


def clean_command(names: list[str], every: bool, dry_run: bool) -> int:
    """Run `tower clean`: say each check that failed and each step on stdout; exit 1 when a check failed, a step
    didn't work, or the worktrees can't be named (saying why, touching nothing)."""
    here = Path.cwd()
    try:
        done = clean.clean(
            here,
            names,
            every=every,
            dry_run=dry_run,
            gh=merge.run_gh(here),
            orca=clean.run_orca(here),
            say=lambda line: print(f"tower: {line}", flush=True),
            here=here,
        )
    except (NotARepo, clean.CleanError) as error:
        print(f"tower: {error}", file=sys.stderr)
        return 1
    return 0 if done else 1


def run_doctor() -> int:
    """Print `tower doctor`'s report; exit 1 when a check failed, 0 when every check passed or only warned."""
    checks = doctor.run_all(Path("."), datetime.now(timezone.utc))
    sys.stdout.write(doctor.render(checks))
    return 1 if doctor.failed(checks) else 0


def show_policy() -> int:
    """Print the policy for the repo holding the current folder."""
    loaded = policy.load()
    repo_policy, slug = loaded.for_folder(Path("."))
    print("\n".join(policy.describe(repo_policy, slug, sorted(loaded.profiles))))
    return 0


def use_profile(profile: str, for_repo: bool) -> int:
    """Switch the default profile (or, with for_repo, this repo's) and print the result; exit 1, saying why, when
    it can't be done (nothing is changed then)."""
    try:
        slug = policy.remote_slugs(Path("."))[0] if for_repo else None
        if for_repo and slug is None:
            raise policy.PolicyError("this folder isn't in a repo with a GitHub origin, so --repo can't name it")
        print(f"tower: {policy.use(profile, slug)}")
    except (policy.PolicyError, policy.RepoUnknown) as error:
        print(f"tower: {error}", file=sys.stderr)
        return 1
    return show_policy()


def run_or_explain(action: Callable[[], object]) -> int:
    """Run one scan-family command; exit 1, saying why, when the repo or the ledger can't be found or the rules
    can't be linked safely (nothing is written then)."""
    try:
        action()
    except (NotARepo, ledger.LedgerError, rules.RulesError) as error:
        print(f"tower: {error}", file=sys.stderr)
        return 1
    return 0


def link_rules() -> None:
    """Link this project's rules into this worktree and print the rules file, after any warnings."""
    rules_file, warnings = rules.link_rules(Path("."))
    for warning in warnings:
        print(f"tower: {warning}", file=sys.stderr)
    print(rules_file)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the tower command with argv (default: the process's arguments); returns the exit code."""
    top = parser()
    args = top.parse_args(argv)
    if args.command == "clean" and args.all and args.worktrees:
        top.error("tower clean: name worktrees or pass --all, not both")
    if args.command == "events" and args.events_command == "tail":
        return events_tail(args.n)
    if args.command == "state":
        return show_all_states(args.json, args.pr) if args.all else show_state(args.repo, args.json, args.pr)
    if args.command == "scan":
        return run_or_explain(lambda: sys.stdout.write(scan.hub_scan(Path("."), github=not args.no_github)))
    if args.command == "adopt-scan":
        return run_or_explain(lambda: sys.stdout.write(adopt.adopt_scan(Path("."))))
    if args.command == "card-path":
        return run_or_explain(lambda: print(ledger.card_path(args.dir)))
    if args.command == "card" and args.card_command == "write":
        return run_card_write(args)
    if args.command == "card" and args.card_command == "kickoff":
        return run_card_kickoff(args)
    if args.command == "decisions" and args.decisions_command == "add":
        return run_decisions_add(args.dir, args.text)
    if args.command == "rules":
        return run_or_explain(link_rules)
    if args.command == "env":
        return show_env(args.dir)
    if args.command == "stats":
        return stats_command(args.since, args.repo)
    if args.command == "merge":
        return merge_command(args.pr)
    if args.command == "clean":
        return clean_command(args.worktrees, args.all, args.dry_run)
    if args.command == "doctor":
        return run_doctor()
    if args.command == "policy":
        return use_profile(args.profile, args.repo) if args.policy_command == "use" else show_policy()
    raise AssertionError(f"unhandled command: {args}")  # argparse's required subcommands make this unreachable
