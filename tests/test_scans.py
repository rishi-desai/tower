"""Tests for the scans ported from bash: `tower card-path`, `tower rules`, `tower adopt-scan` and `tower scan`.

Each runs on a real throwaway repo (conftest.py's `repo`) with the cases the bash scripts were checked on: a dirty main
checkout, worktrees nested in the main checkout's worktrees/ folder, a merged worktree, a detached HEAD, a "/" in a
branch name, and a parked branch. The expected facts are the bash version's on the same repos, except where a test says
it's the #4 fix. `tower scan`'s GROUPS are also checked against the pane's fixture in test_state.py, through the
plugin's run-tower.py.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

import pytest
from conftest import Fixture, git, interactive

from tower import cli, gitrepo, scan

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugin"
RUN_TOWER = PLUGIN / "scripts" / "run-tower.py"


def run_plugin(*args: str) -> subprocess.CompletedProcess[str]:
    """Run one tower command the way the skills do: `python3 -I <plugin>/scripts/run-tower.py ...`."""
    return subprocess.run(["python3", "-I", str(RUN_TOWER), *args], capture_output=True, text=True, check=False)


def run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    """Run one tower command in-process: (exit code, stdout, stderr)."""
    code = cli.main(list(args))
    out, err = capsys.readouterr()
    return code, out, err


def fake_gh(tmp_path: Path, script: str) -> None:
    """Put a `gh` on PATH (the fixture's bin folder comes first) that runs this sh case body on "$1 $2"."""
    gh = tmp_path / "bin" / "gh"
    gh.write_text(f'#!/bin/sh\ncase "$1 $2" in\n{script}\nesac\n', encoding="utf-8")
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)


# ---------- tower card-path ----------


def test_card_path_names_a_slash_branch_and_a_detached_head(repo: Fixture, capsys: pytest.CaptureFixture[str]) -> None:
    slash = repo.add_worktree("feat/42-x")
    detached = repo.add_worktree("feat-1")
    git(detached, "checkout", "-q", "--detach")
    short = git(detached, "rev-parse", "--short", "HEAD").strip()

    assert run(capsys, "card-path", str(slash))[1] == f"{repo.ledger / 'feat__42-x.md'}\n"
    assert run(capsys, "card-path", str(detached))[1] == f"{repo.ledger / f'detached-{short}.md'}\n"
    assert run(capsys, "card-path")[1] == f"{repo.ledger / 'main.md'}\n"


def test_card_path_creates_the_ledger_folder_of_a_mapped_project(
    repo: Fixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    conf = tmp_path / "tower.conf"
    conf.write_text(conf.read_text(encoding="utf-8") + "project.repo=Poot\n", encoding="utf-8")

    assert run(capsys, "card-path")[1] == f"{tmp_path / 'vault' / 'Poot' / 'Ledger' / 'main.md'}\n"
    assert (tmp_path / "vault" / "Poot" / "Ledger").is_dir()


def test_card_path_fails_loudly_rather_than_guess_where_cards_go(
    repo: Fixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    conf = tmp_path / "tower.conf"
    conf.write_text("wip_limit=3\n", encoding="utf-8")
    assert run(capsys, "card-path") == (1, "", f"tower: no projects_dir in {conf} (run setup.sh on this machine)\n")

    conf.write_text("projects_dir=/no/such/vault\n", encoding="utf-8")
    assert run(capsys, "card-path") == (1, "", f"tower: projects_dir does not exist: /no/such/vault (check {conf})\n")


def test_card_path_outside_a_repo_exits_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(capsys, "card-path", str(tmp_path)) == (1, "", f"tower: not a git repo: {tmp_path}\n")


# ---------- tower rules ----------


@pytest.fixture
def home(repo: Fixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake HOME with setup.sh's rules folder, and a global git ignore (in a fake global config) that ignores
    CLAUDE.local.md, as setup.sh leaves a machine."""
    home = tmp_path / "home"
    (home / ".claude" / "tower" / "rules").mkdir(parents=True)
    ignore = tmp_path / "global-ignore"
    ignore.write_text("CLAUDE.local.md\n", encoding="utf-8")
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text(f"[core]\n\texcludesFile = {ignore}\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    return home


def test_rules_links_the_project_rules_once_and_says_when_the_file_is_missing(
    repo: Fixture, home: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    worktree = repo.add_worktree("feat-1")
    (worktree / "sub").mkdir()
    monkeypatch.chdir(worktree / "sub")  # it finds the worktree's top folder from anywhere inside
    rules_file = home / ".claude" / "tower" / "rules" / "repo.md"

    assert run(capsys, "rules") == (
        0,
        f"{rules_file}\n",
        f"tower: {rules_file} doesn't exist yet; create it to add the first rule.\n",
    )
    rules_file.write_text("- a rule\n", encoding="utf-8")
    assert run(capsys, "rules") == (0, f"{rules_file}\n", "")
    assert (worktree / "CLAUDE.local.md").read_text(encoding="utf-8") == "@~/.claude/tower/rules/repo.md\n"


def test_rules_never_glues_its_line_to_an_unended_last_line(
    repo: Fixture, home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The bash version appended straight after `my note`, giving `my note@~/...`, then added it again next time."""
    (repo.main / "CLAUDE.local.md").write_text("my note", encoding="utf-8")

    run(capsys, "rules")
    run(capsys, "rules")

    assert (repo.main / "CLAUDE.local.md").read_text(encoding="utf-8") == "my note\n@~/.claude/tower/rules/repo.md\n"


def test_rules_writes_nothing_a_commit_could_pick_up(
    repo: Fixture, home: Path, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    (tmp_path / "global-ignore").write_text("", encoding="utf-8")

    code, out, err = run(capsys, "rules")

    assert (code, out) == (1, "")
    assert f"tower: {repo.main / 'CLAUDE.local.md'} would NOT be git-ignored" in err
    assert not (repo.main / "CLAUDE.local.md").exists()


def test_rules_refuses_a_project_name_with_a_space_and_a_machine_without_setup(
    repo: Fixture, home: Path, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    conf = tmp_path / "tower.conf"
    original = conf.read_text(encoding="utf-8")
    conf.write_text(original + "project.repo=My Repo\n", encoding="utf-8")
    code, _, err = run(capsys, "rules")
    assert (code, err.splitlines()[0]) == (1, "tower: project name 'My Repo' has a space, which can break the @import.")

    conf.write_text(original, encoding="utf-8")
    (home / ".claude" / "tower" / "rules").rmdir()
    assert run(capsys, "rules")[::2] == (
        1,
        f"tower: {home / '.claude/tower/rules'} is missing — run setup.sh on this machine\n",
    )
    assert not (repo.main / "CLAUDE.local.md").exists()


# ---------- tower adopt-scan ----------


def messy(repo: Fixture) -> dict[str, Path]:
    """The bash version's test repo: dirty main, worktrees nested in main's (not excluded) worktrees/ folder, a merged
    worktree, a detached HEAD, a "/" branch with a card, and a parked branch with no worktree."""
    (repo.main / ".git" / "info" / "exclude").write_text("", encoding="utf-8")
    (repo.main / "a.txt").write_text("one\n", encoding="utf-8")
    git(repo.main, "add", "a.txt")
    git(repo.main, "commit", "-q", "-m", "a")
    slash = repo.add_worktree("feat/12-slash", commits=2)
    repo.write_card("feat/12-slash", "running", ticket="#12")
    merged = repo.add_worktree("merged")
    git(repo.main, "merge", "-q", "--ff-only", "merged")
    detached = repo.add_worktree("to-detach")
    git(detached, "checkout", "-q", "--detach")
    git(repo.main, "branch", "-q", "-D", "to-detach")
    (detached / "new.txt").write_text("x\n", encoding="utf-8")
    git(repo.main, "branch", "parked/x")
    git(repo.main, "checkout", "-q", "parked/x")
    git(repo.main, "commit", "-q", "--allow-empty", "-m", "parked work")
    git(repo.main, "checkout", "-q", "main")
    (repo.main / "a.txt").write_text("two\n", encoding="utf-8")
    (repo.main / "notes.txt").write_text("n\n", encoding="utf-8")
    return {"slash": slash, "merged": merged, "detached": detached}


def adopt_rows(capsys: pytest.CaptureFixture[str]) -> list[str]:
    """`tower adopt-scan`'s WORKTREES section, each row's path dropped (rows end in it)."""
    code, out, _ = run(capsys, "adopt-scan")
    assert code == 0
    section = out.split("== WORKTREES ==\n")[1].split("\n\n")[0]
    return [line.rsplit(" | ", 1)[0] if line.startswith("- ") else line for line in section.splitlines()]


def test_adopt_scan_reports_each_worktree_like_the_bash_version(
    repo: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = messy(repo)
    repo.sessions(interactive("hub", repo.main, "idle"), interactive("w1", paths["slash"], "busy"))
    short = git(paths["detached"], "rev-parse", "HEAD")[:7]

    code, out, err = run(capsys, "adopt-scan")

    assert (code, err) == (0, "")
    assert out.startswith(
        f"== CONFIG ==\nrepo: repo | ledger: {repo.ledger} | default branch: main (compared with main)"
        " | WIP limit: 3 | GitHub: no\n"
    )
    rows = [line.rsplit(" | last: ", 1)[0] if line.startswith("- ") else line for line in adopt_rows(capsys)]
    assert rows == [
        "- [MAIN CHECKOUT] main | no card | uncommitted: 2 | ahead 0 / behind 0 of main",
        "     M a.txt",
        "    ?? notes.txt",
        (
            f"    note: worktrees/ holds only git worktrees, so it isn't counted; to hide it from git status, add the line"
            f" /worktrees/ to {os.path.realpath(repo.main / '.git' / 'info' / 'exclude')}"
        ),
        "- [worktree] feat/12-slash | card: running | uncommitted: 0 | ahead 2 / behind 1 of main",
        "- [worktree] merged | no card | uncommitted: 0 | ahead 0 / behind 0 of main",
        f"- [worktree] (detached {short}) | no card | uncommitted: 1 | ahead 1 / behind 0 of main",
        "    ?? new.txt",
    ]
    assert "- [worktree] feat/12-slash | card: running | uncommitted: 0" in out
    assert "| ticket guess: #12 |" in out
    sessions = out.split("== LIVE SESSIONS (agent view) ==\n")[1].split("\n\n")[0]
    assert sessions == "- main | 1 open: hub(int:idle)\n- feat/12-slash | 1 open: w1(int:busy)"
    branches = out.split("== BRANCHES WITH UNMERGED WORK AND NO WORKTREE ==\n")[1]
    assert branches.startswith("- parked/x | 1 commit(s) not on main | last: ")
    assert branches.count("\n") == 1  # nothing else: main is the default, the others have worktrees


@pytest.mark.parametrize(
    ("branch", "ticket"),
    [
        ("issue-42", "#42"),  # #76: an issue word names the ticket as a type does
        ("gh-42", "#42"),
        ("rishi-fix-64", "#64"),  # #76: a user prefix joined with - instead of /
        ("v2-fix-42", "#42"),  # #76: a version prefix, whose 2 isn't the ticket
        ("release-2026-11", "none"),  # #64: a year in a release name isn't a ticket
        ("2026-11-notes", "none"),  # #76: nor is a date at the start
        ("notes/2026-11-recap", "none"),
        ("feat/42-x", "#42"),
        ("rishi-desai/fix-64-x", "#64"),
        ("42-x", "#42"),
        ("fix/7", "#7"),
        ("main", "none"),
    ],
)
def test_the_ticket_guess_reads_the_number_after_an_issue_word_or_a_type(branch: str, ticket: str) -> None:
    assert gitrepo.guess_ticket(branch) == ticket


def test_adopt_scan_guesses_tickets_like_a_card_does(repo: Fixture, capsys: pytest.CaptureFixture[str]) -> None:
    """#64: adopt-scan makes the guess `tower card write` makes, so a year or a version isn't taken for the ticket."""
    branches = {
        "release-2026-11": "none",
        "v2-fix-42": "#42",
        "fix/7-x": "#7",
        "issue-43": "#43",
        "gh-44": "#44",
        "rishi-fix-64": "#64",
    }
    for branch in branches:
        repo.add_worktree(branch)

    guesses = {
        row.split(" | ")[0]: row.rsplit(" | ticket guess: ", 1)[1] for row in adopt_rows(capsys) if row.startswith("- ")
    }

    for branch, ticket in branches.items():
        assert guesses[f"- [worktree] {branch}"] == ticket, branch


def test_an_untracked_file_next_to_the_nested_worktrees_still_counts(
    repo: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    """#4's check: the worktrees aren't work on main, but a real file beside them is, and only it is listed."""
    messy(repo)
    (repo.main / "worktrees" / "stray.txt").write_text("x\n", encoding="utf-8")

    rows = adopt_rows(capsys)

    assert rows[0].startswith("- [MAIN CHECKOUT] main | no card | uncommitted: 3 |")
    assert rows[1:4] == ["     M a.txt", "    ?? notes.txt", "    ?? worktrees/stray.txt"]
    assert not any("note:" in row for row in rows)


def test_an_untracked_folder_next_to_the_nested_worktrees_counts_once_as_git_shows_it(
    repo: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    """As `git status` shows an untracked folder: one entry, however many files it holds; ignored files don't count."""
    messy(repo)
    cache = repo.main / "worktrees" / "cache" / "deep"
    cache.mkdir(parents=True)
    for n in range(10):
        (cache / f"{n}.bin").write_text("x\n", encoding="utf-8")
    (repo.main / "worktrees" / "logs").mkdir()
    (repo.main / "worktrees" / "logs" / "run.log").write_text("x\n", encoding="utf-8")
    (repo.main / ".git" / "info" / "exclude").write_text("*.log\n", encoding="utf-8")

    rows = adopt_rows(capsys)

    assert rows[0].startswith("- [MAIN CHECKOUT] main | no card | uncommitted: 3 |")
    assert rows[1:4] == ["     M a.txt", "    ?? notes.txt", "    ?? worktrees/cache/"]


def test_adopt_scan_lists_eight_changes_and_counts_the_rest(repo: Fixture, capsys: pytest.CaptureFixture[str]) -> None:
    for n in range(10):
        (repo.main / f"file {n}.txt").write_text("x\n", encoding="utf-8")
    git(repo.main, "add", "file 0.txt")
    git(repo.main, "commit", "-q", "-m", "f0")
    git(repo.main, "mv", "file 0.txt", "renamed.txt")

    rows = adopt_rows(capsys)

    assert rows[0].startswith("- [MAIN CHECKOUT] main | no card | uncommitted: 10 |")
    assert rows[1] == "    R  file 0.txt -> renamed.txt"  # paths unquoted, unlike `git status --porcelain`
    assert rows[2:9] == [f"    ?? file {n}.txt" for n in range(1, 8)]
    assert rows[9] == "    ... and 2 more"


def test_adopt_scan_shows_each_branchs_pr_and_gh_failures(
    repo: Fixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    messy(repo)
    fake_gh(
        tmp_path,
        """  "auth status"|"repo view") exit 0 ;;
  "pr list") case "$*" in
    *feat/12-slash*) printf '#5 OPEN slash work' ;;
    *parked/x*) echo "HTTP 502" >&2; exit 1 ;;
  esac ;;""",
    )

    code, out, _ = run(capsys, "adopt-scan")

    assert code == 0
    assert "| GitHub: yes\n" in out
    assert "feat/12-slash | card: running" in out.split("    PR: #5 OPEN slash work\n")[0].splitlines()[-1]
    assert "    PR: none\n" in out  # merged has no PR; the main checkout and the detached HEAD aren't asked
    assert out.count("    PR: ") == 2
    assert "- parked/x | 1 commit(s) not on main | PR: gh failed: HTTP 502 | last: " in out


def test_the_plugin_runs_adopt_scan(repo: Fixture) -> None:
    proc = run_plugin("adopt-scan")
    assert proc.returncode == 0  # the bash version exited 1 whenever it found a parked branch
    assert proc.stdout.startswith("== CONFIG ==\nrepo: repo | ")


# ---------- tower scan ----------


def adopt_line(capsys: pytest.CaptureFixture[str]) -> str:
    """The ADOPT: line of `tower scan --no-github`."""
    code, out, _ = run(capsys, "scan", "--no-github")
    assert code == 0
    return next(line for line in out.splitlines() if line.startswith("ADOPT: "))


def test_scan_says_an_adopted_repo_needs_no_adopting(repo: Fixture, capsys: pytest.CaptureFixture[str]) -> None:
    repo.add_worktree("feat-1")
    repo.write_card("feat-1", "running")
    repo.add_worktree("feat-2")  # one worktree with no card isn't a reason: Housekeeping lists it
    repo.write_card("feat-3", "running")
    repo.add_worktree("feat-3")
    repo.sessions(interactive("hub", repo.main, "idle"))

    assert adopt_line(capsys) == "ADOPT: no"


def test_one_worktree_without_a_card_isnt_a_reason_to_adopt(repo: Fixture, capsys: pytest.CaptureFixture[str]) -> None:
    """A worktree just made by hand, before its first card: 1 of 1 is most, but Housekeeping's "no card" covers it."""
    repo.add_worktree("feat-1")

    assert adopt_line(capsys) == "ADOPT: no"


def test_an_unreadable_card_isnt_a_missing_one(repo: Fixture, capsys: pytest.CaptureFixture[str]) -> None:
    """A card the vault won't hand over (permissions, an iCloud file not downloaded) is unknown, not "no card"."""
    for branch in ("feat-1", "feat-2"):
        repo.add_worktree(branch)
        repo.write_card(branch, "running").chmod(0)

    assert adopt_line(capsys) == "ADOPT: no"


def test_scan_says_adopt_when_most_worktrees_have_no_card(repo: Fixture, capsys: pytest.CaptureFixture[str]) -> None:
    repo.add_worktree("feat-1")
    repo.add_worktree("feat-2")
    repo.add_worktree("feat-3")
    repo.write_card("feat-3", "running")

    assert adopt_line(capsys) == "ADOPT: yes — 2 of 3 worktrees have no card"


def test_scan_says_adopt_when_the_main_checkout_has_uncommitted_work_or_shared_sessions(
    repo: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo.main / "notes.txt").write_text("work in progress\n", encoding="utf-8")
    repo.sessions(interactive("hub", repo.main, "idle"), interactive("other", repo.main, "busy"))

    assert adopt_line(capsys) == (
        "ADOPT: yes — the main checkout has 1 uncommitted change;"
        " 2 interactive sessions share the main checkout (one is the hub)"
    )


def test_scan_doesnt_count_a_folder_of_worktrees_as_work_on_main(
    repo: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    """Orca's ./worktrees folder shows as untracked in the main checkout; it isn't a reason to adopt (#4)."""
    (repo.main / ".git" / "info" / "exclude").write_text("", encoding="utf-8")
    repo.add_worktree("feat-1")
    repo.write_card("feat-1", "running")

    assert adopt_line(capsys) == "ADOPT: no"


def test_scan_lists_all_issues_on_your_own_repo(
    repo: Fixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    fake_gh(
        tmp_path,
        """  "auth status") exit 0 ;;
  "repo view") echo "me/repo me" ;;
  "api user") echo me ;;
  "pr list") printf -- '- #5 slash work [feat/12-slash] review:none\\n' ;;
  "issue list") case "$*" in *--assignee*) echo "- #9 assigned only" ;; *) printf -- '- #1 one [bug]\\n- #2 two\\n' ;; esac ;;""",
    )
    (repo.ledger / "_backlog.md").write_text("- an idea", encoding="utf-8")

    code, out, _ = run(capsys, "scan")

    assert code == 0
    assert out.split("== GITHUB ==\n")[1] == (
        "repo: me/repo | you: me\n-- my open PRs --\n- #5 slash work [feat/12-slash] review:none\n"
        "-- open issues (all — your repo) --\n- #1 one [bug]\n- #2 two\n\n== BACKLOG (parked ideas) ==\n- an idea"
    )


def test_scan_marks_each_issue_in_progress_with_where_it_runs(
    repo: Fixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """An issue a card names (hand-edited without its #) is marked after its labels, so the hub skips it; the pane
    still reads the row's title and labels."""
    repo.add_worktree("feat-2-x")
    card = repo.write_card("feat-2-x", "running", ticket="#2")
    card.write_text(card.read_text(encoding="utf-8").replace('ticket: "#2"', "ticket: 2"), encoding="utf-8")
    fake_gh(
        tmp_path,
        """  "auth status") exit 0 ;;
  "repo view") echo "me/repo me" ;;
  "api user") echo me ;;
  "pr list") ;;
  "issue list") printf -- '- #12 twelve [ready-for-agent]\\n- #2 two [ready-for-agent] [bug]\\n- #1 one\\n' ;;""",
    )

    issues = run(capsys, "scan")[1].split("-- open issues (all — your repo) --\n")[1].split("\n\n")[0]

    assert issues == (
        "- #12 twelve [ready-for-agent]\n- #2 two [ready-for-agent] [bug] [in progress: running in feat-2-x]\n- #1 one"
    )


def test_a_bracket_in_a_branch_name_cant_end_the_in_progress_mark_early() -> None:
    marked = scan.marked_in_progress("- #2 two [bug]", {"#2": "running in feat-2-a]b"})

    assert marked == "- #2 two [bug] [in progress: running in feat-2-ab]"
    assert scan.marked_in_progress("- #20 twenty", {"#2": "running in feat-2-x"}) == "- #20 twenty"


def test_scan_lists_only_your_issues_on_someone_elses_repo_and_shows_gh_errors(
    repo: Fixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    fake_gh(
        tmp_path,
        """  "auth status") exit 0 ;;
  "repo view") echo "team/repo team" ;;
  "api user") echo "rate limited" >&2; exit 1 ;;
  "pr list") ;;
  "issue list") case "$*" in *--assignee*) echo "- #9 assigned only" ;; *) echo "- #1 everyone's" ;; esac ;;""",
    )

    github = run(capsys, "scan")[1].split("== GITHUB ==\n")[1].split("\n\n")[0]

    assert github == (
        "repo: team/repo | you: unknown (rate limited)\n-- my open PRs --\n(none)\n"
        "-- open issues assigned to you --\n- #9 assigned only"
    )


@pytest.mark.parametrize(
    ("gh_script", "says"),
    [
        ('  "auth status") echo "not logged in" >&2; exit 1 ;;', "(gh not logged in — run: gh auth login)"),
        ('  "auth status") exit 0 ;;\n  "repo view") echo "no git remotes" >&2; exit 1 ;;',
         "(no GitHub repo for this checkout: no git remotes)"),
    ],
)  # fmt: skip
def test_scan_says_why_github_is_missing(
    repo: Fixture, capsys: pytest.CaptureFixture[str], tmp_path: Path, gh_script: str, says: str
) -> None:
    fake_gh(tmp_path, gh_script)
    assert f"== GITHUB ==\n{says}\n\n" in run(capsys, "scan")[1]


def test_scan_without_a_ledger_fails_instead_of_reporting_nothing_needs_you(
    repo: Fixture, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "tower.conf").write_text("wip_limit=3\n", encoding="utf-8")
    proc = run_plugin("scan", "--no-github")
    assert (proc.returncode, proc.stdout) == (1, "")
    assert proc.stderr == f"tower: no projects_dir in {tmp_path / 'tower.conf'} (run setup.sh on this machine)\n"


def test_scan_with_a_tower_conf_that_isnt_utf8_says_so_instead_of_crashing(
    repo: Fixture, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "tower.conf").write_bytes(b"\xff")

    code, out, err = run(capsys, "scan", "--no-github")

    assert (code, out) == (1, "")
    assert err == f"tower: {tmp_path / 'tower.conf'} isn't UTF-8 text (line 1)\n"


def test_a_tower_conf_saved_with_a_byte_order_mark_still_finds_the_ledger(
    repo: Fixture, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    conf = tmp_path / "tower.conf"
    conf.write_bytes(b"\xef\xbb\xbf" + conf.read_bytes())  # as an editor saving "UTF-8 with BOM" writes it

    assert run(capsys, "card-path")[:2] == (0, f"{repo.ledger / 'main.md'}\n")


# ---------- the plugin's entry points ----------


def test_the_plugin_runs_card_path_and_rules(repo: Fixture, home: Path) -> None:
    assert run_plugin("card-path").stdout == f"{repo.ledger / 'main.md'}\n"
    assert run_plugin("rules").stdout == f"{home / '.claude' / 'tower' / 'rules' / 'repo.md'}\n"


def test_every_script_a_skill_names_exists() -> None:
    """The skills call `<plugin>/scripts/...` and their own `scripts/...`; a deleted wrapper must not stay named."""
    for skill in sorted((PLUGIN / "skills").glob("*/*.md")):
        text = skill.read_text(encoding="utf-8")
        for name in re.findall(r"<plugin>/scripts/([\w.-]+)", text):
            assert (PLUGIN / "scripts" / name).is_file(), f"{skill} names <plugin>/scripts/{name}"
        for name in re.findall(r"(?<![\w>/])scripts/([\w.-]+)", text):
            assert (skill.parent / "scripts" / name).is_file(), f"{skill} names scripts/{name}"


def test_adopt_scan_says_unknown_not_clean_when_git_status_fails(
    repo: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    worktree = repo.add_worktree("feat-1")
    index = Path(git(worktree, "rev-parse", "--path-format=absolute", "--git-path", "index").strip())
    index.write_bytes(b"not an index")

    row = next(r for r in adopt_rows(capsys) if r.startswith("- [worktree] feat-1"))

    assert row.startswith("- [worktree] feat-1 | no card | uncommitted: ? |")


def test_adopt_scan_compares_with_the_main_checkouts_branch_when_there_is_no_main_or_master(
    repo: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    git(repo.main, "branch", "-m", "main", "trunk")
    repo.add_worktree("feat-1")

    code, out, _ = run(capsys, "adopt-scan")

    assert code == 0
    assert "| default branch: trunk (compared with trunk) |" in out
    assert "- [worktree] feat-1 | no card | uncommitted: 0 | ahead 1 / behind 0 of trunk |" in out
    assert out.endswith("== BRANCHES WITH UNMERGED WORK AND NO WORKTREE ==\n(none)\n")


def test_an_unreadable_backlog_is_reported_and_the_scan_goes_on(
    repo: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo.ledger / "_backlog.md").mkdir()  # reading a folder fails like an unreadable file

    code, out, _ = run(capsys, "scan", "--no-github")

    assert code == 0
    assert "== WORKTREES ==" in out
    assert out.endswith(
        f"== BACKLOG (parked ideas) ==\n(couldn't read {repo.ledger / '_backlog.md'}: Is a directory)\n"
    )


def test_an_unreadable_config_is_an_error_not_a_fresh_machine(
    repo: Fixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    conf = tmp_path / "tower.conf"
    conf.chmod(0o000)
    try:
        assert run(capsys, "card-path") == (1, "", f"tower: can't read {conf} (Permission denied)\n")
    finally:
        conf.chmod(0o644)
