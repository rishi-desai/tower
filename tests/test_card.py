"""Tests for `tower card write` (src/tower/card.py) and the card-resume.py hook: cards as a view the core renders.

Each test runs on a real throwaway repo (conftest.py's `repo`) with a fake `gh` whose `pr list` prints the PRs the test
chose. The agent sends only the narrative sections on stdin; the frontmatter comes from git, GitHub and the request.
"""

from __future__ import annotations

import io
import json
import re
import shlex
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import Fixture, git

from tower import cli

SCRIPTS = Path(__file__).resolve().parent.parent / "plugin" / "scripts"
HOOK = SCRIPTS / "card-resume.py"
SYSTEM_PYTHON = "/usr/bin/python3"  # what a hook's `python3` can resolve to; 3.9 on macOS
# The plugin runs the core with no `tower` installed: the dev venv's base interpreter, and a stock Mac's 3.9.
INTERPRETERS = [
    pytest.param(str(Path(sys.base_prefix) / "bin" / "python3"), id="base-python"),
    pytest.param(
        SYSTEM_PYTHON,
        id="system-python3",
        marks=pytest.mark.skipif(not Path(SYSTEM_PYTHON).exists(), reason=f"no {SYSTEM_PYTHON} on this machine"),
    ),
]

NARRATIVE = """## Done
- The search box finds alerts by ticker.

## Why it matters
- You stop scrolling the whole feed.

## Needs you
- Nothing.

## Verify
- `npm test` passes.
"""


def fake_prs(tmp_path: Path, prs: list[dict[str, object]] | None) -> None:
    """Put a `gh` on PATH whose `pr list` prints prs as JSON, each from this repo unless it says isCrossRepository
    (gh always prints that field when asked); None makes it fail like a logged-out gh."""
    gh = tmp_path / "bin" / "gh"
    if prs is None:
        body = 'echo "gh: To get started with GitHub CLI, please run:  gh auth login" >&2; exit 4'
    else:
        body = f"cat <<'EOF'\n{json.dumps([{'isCrossRepository': False, **pr} for pr in prs])}\nEOF"
    gh.write_text(f'#!/bin/sh\n[ "$1 $2" = "pr list" ] || exit 9\n{body}\n', encoding="utf-8")
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)


def fake_prs_by_branch(tmp_path: Path, prs: dict[str, list[dict[str, object]]]) -> None:
    """Put a `gh` on PATH whose `pr list --head BRANCH` prints that branch's PRs, each from this repo."""
    listed = {head: [{"isCrossRepository": False, **pr} for pr in branch_prs] for head, branch_prs in prs.items()}
    program = (
        "import json, sys\n"
        "args = sys.argv[1:]\n"
        "sys.exit(9) if args[:2] != ['pr', 'list'] else None\n"
        f"print(json.dumps({listed!r}.get(args[args.index('--head') + 1], [])))\n"
    )
    gh = tmp_path / "bin" / "gh"
    gh.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} -c {shlex.quote(program)} "$@"\n', encoding="utf-8")
    gh.chmod(0o755)


def write(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, *args: str, stdin: str = NARRATIVE
) -> tuple[int, str, str]:
    """Run `tower card write ARGS` in-process with stdin: (exit code, stdout, stderr)."""
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    code = cli.main(["card", "write", *args])
    out, err = capsys.readouterr()
    return code, out, err


def frontmatter(card: Path) -> list[str]:
    """The card's frontmatter lines, between the two --- lines."""
    lines = card.read_text(encoding="utf-8").split("\n")
    assert lines[0] == "---"
    return lines[1 : lines.index("---", 1)]


def body(card: Path) -> str:
    """Everything after the frontmatter."""
    text = card.read_text(encoding="utf-8")
    return text.split("\n---\n", 1)[1]


UPDATED = re.compile(r"updated: \d{4}-\d{2}-\d{2}T\d{2}:\d{2}")


def test_a_card_with_no_pr_is_running_and_its_frontmatter_comes_from_facts(
    repo: Fixture, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = repo.add_worktree("feat/42-search")
    fake_prs(tmp_path, [])
    monkeypatch.chdir(path)

    code, out, err = write(capsys, monkeypatch)

    card = repo.ledger / "feat__42-search.md"
    assert (code, err) == (0, "")
    assert out == f"{card}\nstatus: running (no PR yet)\n"
    fields = frontmatter(card)
    assert UPDATED.fullmatch(fields[2])
    assert fields[:2] + fields[3:] == [
        "status: running",
        'ticket: "#42"',  # guessed from the branch, and quoted by the core, never the model
        'branch: "feat/42-search"',
        "pr: none",
        'base: "main"',
    ]
    assert body(card) == "\n" + NARRATIVE
    assert card.stat().st_mode & 0o777 == 0o644


def test_sections_are_rendered_in_the_cards_order_whatever_order_they_arrive_in(
    repo: Fixture, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo.add_worktree("feat-7")
    fake_prs(tmp_path, [])
    monkeypatch.chdir(repo.main / "worktrees" / "feat-7")
    shuffled = "## Words used\n- FDE: forward deployed engineer\n\n\n" + NARRATIVE.replace(
        "## Verify\n", "## Verify it yourself\n"
    )

    assert write(capsys, monkeypatch, stdin=shuffled)[0] == 0

    rendered = body(repo.ledger / "feat-7.md")
    assert rendered == "\n" + NARRATIVE + "\n## Words used\n- FDE: forward deployed engineer\n"


def test_a_heading_inside_a_code_block_is_code_and_files_touched_is_kept(
    repo: Fixture, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo.add_worktree("feat-7")
    fake_prs(tmp_path, [])
    monkeypatch.chdir(repo.main / "worktrees" / "feat-7")
    fenced = NARRATIVE.replace("- `npm test` passes.", "```\n## not a heading: a shell comment\nnpm test\n```")
    narrative = fenced + "\n## Files touched\n- src/search.ts\n"

    assert write(capsys, monkeypatch, stdin=narrative)[0] == 0

    assert body(repo.ledger / "feat-7.md") == "\n" + narrative


def test_a_pr_gh_gave_no_base_for_falls_back_to_the_repos_base(
    repo: Fixture, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """github.branch_pr leaves a missing baseRefName as "": the card says the repo's base, never an empty one."""
    path = repo.add_worktree("feat-42-search")
    fake_prs(tmp_path, [{"number": 57, "state": "OPEN", "headRefOid": git(path, "rev-parse", "HEAD").strip()}])
    monkeypatch.chdir(path)

    code, _, _ = write(capsys, monkeypatch)

    assert code == 0
    assert 'base: "main"' in frontmatter(repo.ledger / "feat-42-search.md")


def test_an_open_pr_makes_the_card_waiting_on_ci(
    repo: Fixture, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = repo.add_worktree("feat-42-search")
    head = git(path, "rev-parse", "HEAD").strip()
    fake_prs(
        tmp_path,
        [
            {"number": 50, "state": "CLOSED", "baseRefName": "main", "headRefOid": "0" * 40},
            {"number": 57, "state": "OPEN", "baseRefName": "integration/a-plus", "headRefOid": head},
        ],
    )
    monkeypatch.chdir(path)

    code, out, _ = write(capsys, monkeypatch)

    card = repo.ledger / "feat-42-search.md"
    assert code == 0
    assert out.endswith("status: waiting-ci (PR #57 is open)\n")
    assert [f for f in frontmatter(card) if not f.startswith("updated:")] == [
        "status: waiting-ci",
        'ticket: "#42"',
        'branch: "feat-42-search"',
        'pr: "#57"',
        'base: "integration/a-plus"',
    ]

    # Fixing a failed check with the PR open: the agent may say it's working again.
    assert write(capsys, monkeypatch, "--status", "running")[0] == 0
    assert frontmatter(card)[0] == "status: running"


def test_a_card_can_say_done_only_when_its_pr_merged(
    repo: Fixture, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = repo.add_worktree("feat-42-search")
    head = git(path, "rev-parse", "HEAD").strip()
    fake_prs(tmp_path, [{"number": 57, "state": "MERGED", "baseRefName": "main", "headRefOid": head}])
    monkeypatch.chdir(path)

    code, out, err = write(capsys, monkeypatch, "--status", "running")

    card = repo.ledger / "feat-42-search.md"
    assert code == 0
    assert out.endswith("status: done (PR #57 is merged)\n")
    assert "PR #57 is merged, so the card says done, not running" in err
    assert frontmatter(card)[0] == "status: done"
    assert 'pr: "#57"' in frontmatter(card)

    # waiting-ci isn't something an agent can ask for: only an open PR says it.
    with pytest.raises(SystemExit):
        write(capsys, monkeypatch, "--status", "waiting-ci")


def test_done_can_be_asked_for_only_with_an_open_pr_waiting_on_the_users_merge(
    repo: Fixture, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = repo.add_worktree("feat-42-search")
    head = git(path, "rev-parse", "HEAD").strip()
    monkeypatch.chdir(path)
    card = repo.ledger / "feat-42-search.md"

    for prs in ([], [{"number": 50, "state": "CLOSED", "baseRefName": "main", "headRefOid": head}], None):
        fake_prs(tmp_path, prs)
        code, out, err = write(capsys, monkeypatch, "--status", "done")
        assert (code, out) == (1, "")
        assert err.startswith("tower: done needs a PR, and ")
        assert not card.exists()

    fake_prs(tmp_path, [{"number": 57, "state": "OPEN", "baseRefName": "main", "headRefOid": head}])
    code, out, _ = write(capsys, monkeypatch, "--status", "done")
    assert (code, out) == (0, f"{card}\nstatus: done (you asked for it; PR #57 is open)\n")


def test_a_closed_pr_is_named_but_the_card_is_running_and_a_detached_head_has_no_branch(
    repo: Fixture, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = repo.add_worktree("feat-42-search")
    head = git(path, "rev-parse", "HEAD").strip()
    fake_prs(tmp_path, [{"number": 50, "state": "CLOSED", "baseRefName": "main", "headRefOid": head}])
    monkeypatch.chdir(path)

    assert write(capsys, monkeypatch)[1].endswith("status: running (PR #50 was closed without merging)\n")
    assert 'pr: "#50"' in frontmatter(repo.ledger / "feat-42-search.md")

    # Detached, the worktree keeps its card (the card follows its worktree), which now says it has no branch.
    git(path, "checkout", "-q", "--detach")
    code, out, _ = write(capsys, monkeypatch)
    fields = frontmatter(repo.ledger / "feat-42-search.md")
    assert (code, out.splitlines()[1]) == (0, "status: running (no PR yet)")  # gh isn't asked without a branch
    assert ("branch: none", 'base: "main"') == (fields[3], fields[5])


def test_a_merged_pr_whose_head_is_ahead_of_this_worktree_is_still_this_work(
    repo: Fixture, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = repo.add_worktree("feat-42-search")
    git(path, "commit", "-q", "--allow-empty", "-m", "pushed from elsewhere")
    pr_head = git(path, "rev-parse", "HEAD").strip()
    git(path, "reset", "-q", "--hard", "HEAD~1")  # this worktree is behind the PR's head, not past it
    fake_prs(tmp_path, [{"number": 57, "state": "MERGED", "baseRefName": "main", "headRefOid": pr_head}])
    monkeypatch.chdir(path)

    assert write(capsys, monkeypatch)[1].endswith("status: done (PR #57 is merged)\n")


def test_a_merged_pr_from_an_earlier_use_of_the_branch_isnt_this_work(
    repo: Fixture, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = repo.add_worktree("feat-42-search")
    old_head = git(repo.main, "rev-parse", "HEAD").strip()  # in this clone, but this worktree's commit isn't in it
    fake_prs(
        tmp_path,
        [
            {"number": 12, "state": "MERGED", "baseRefName": "main", "headRefOid": old_head},
            {"number": 99, "state": "OPEN", "baseRefName": "main", "headRefOid": old_head, "isCrossRepository": True},
        ],
    )
    monkeypatch.chdir(path)

    code, out, _ = write(capsys, monkeypatch)

    assert code == 0  # the fork's open PR #99 from a branch of the same name isn't this work either
    assert out.endswith("status: running (no PR yet; PR #12 merged earlier work on this branch name)\n")
    assert "pr: none" in frontmatter(repo.ledger / "feat-42-search.md")


def test_a_merged_pr_whose_head_isnt_in_this_clone_is_trusted_and_says_so(
    repo: Fixture, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = repo.add_worktree("feat-42-search")
    fake_prs(tmp_path, [{"number": 57, "state": "MERGED", "baseRefName": "main", "headRefOid": "f" * 40}])
    monkeypatch.chdir(path)

    out = write(capsys, monkeypatch)[1]

    assert out.endswith(
        "status: done (PR #57 is merged; its head fffffff isn't in this clone, so it wasn't checked against this worktree)\n"
    )


def test_needs_you_and_blocked_need_a_reason_which_the_card_keeps(
    repo: Fixture, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = repo.add_worktree("feat-42-search")
    fake_prs(tmp_path, [])
    monkeypatch.chdir(path)
    card = repo.ledger / "feat-42-search.md"

    code, _, err = write(capsys, monkeypatch, "--status", "needs-you")
    assert (code, err) == (1, "tower: needs-you and blocked need --reason\n")
    assert not card.exists()

    reason = 'which "search" library?\nA or B'
    assert write(capsys, monkeypatch, "--status", "needs-you", "--reason", reason)[0] == 0
    assert frontmatter(card)[0] == "status: needs-you"
    assert frontmatter(card)[-1] == 'reason: "which \\"search\\" library? A or B"'

    code, _, err = write(capsys, monkeypatch, "--reason", "just because")
    assert (code, err) == (1, "tower: --reason only goes with --status needs-you or blocked\n")


@pytest.mark.parametrize(
    ("stdin", "message"),
    [
        ("---\nstatus: done\n---\n" + NARRATIVE, "send only the sections"),
        ("Hello\n" + NARRATIVE, "text before the first ## heading"),
        (NARRATIVE.replace("## Verify\n- `npm test` passes.\n", ""), "missing section: Verify"),
        (NARRATIVE + "\n## Notes\n- a.py\n", "unknown section 'Notes'"),
        (NARRATIVE + "\n## Done\n- again\n", "section Done appears twice"),
        (NARRATIVE.replace("- Nothing.", ""), "section Needs you is empty"),
    ],
)
def test_narrative_the_core_cant_render_writes_nothing(
    repo: Fixture,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    stdin: str,
    message: str,
) -> None:
    path = repo.add_worktree("feat-42-search")
    fake_prs(tmp_path, [])
    monkeypatch.chdir(path)

    code, out, err = write(capsys, monkeypatch, stdin=stdin)

    assert (code, out) == (1, "")
    assert message in err
    assert not (repo.ledger / "feat-42-search.md").exists()


def test_without_github_the_card_says_pr_unknown_and_never_claims_done(
    repo: Fixture, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = repo.add_worktree("feat-42-search")
    fake_prs(tmp_path, None)
    monkeypatch.chdir(path)

    code, out, err = write(capsys, monkeypatch)

    assert code == 0
    assert out.endswith("status: running (GitHub couldn't be asked about a PR)\n")
    assert "couldn't read this branch's PR (gh: To get started" in err
    assert "pr: unknown" in frontmatter(repo.ledger / "feat-42-search.md")


def test_the_ticket_comes_from_the_flag_then_the_last_card_then_the_branch(
    repo: Fixture, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = repo.add_worktree("release-2026-11")
    fake_prs(tmp_path, [])
    monkeypatch.chdir(path)
    card = repo.ledger / "release-2026-11.md"

    assert write(capsys, monkeypatch)[0] == 0
    assert frontmatter(card)[1] == "ticket: none"  # a number in a branch isn't always a ticket

    assert write(capsys, monkeypatch, "--ticket", "51")[0] == 0
    assert frontmatter(card)[1] == 'ticket: "#51"'

    assert write(capsys, monkeypatch)[0] == 0
    assert frontmatter(card)[1] == 'ticket: "#51"'  # kept from the last card

    path2 = repo.add_worktree("feat-60-not-60")
    monkeypatch.chdir(path2)
    assert write(capsys, monkeypatch, "--ticket", "none")[0] == 0
    assert write(capsys, monkeypatch)[0] == 0
    assert frontmatter(repo.ledger / "feat-60-not-60.md")[1] == "ticket: none"  # an explicit none beats the guess

    monkeypatch.chdir(repo.add_worktree("rishi-fix-64"))
    assert write(capsys, monkeypatch)[0] == 0
    assert frontmatter(repo.ledger / "rishi-fix-64.md")[1] == 'ticket: "#64"'  # #76: behind a - joined user prefix

    code, _, err = write(capsys, monkeypatch, "--ticket", "#51 and #52")
    assert (code, err) == (1, "tower: a ticket is a number like 42 or #42, or none; got '#51 and #52'\n")


def test_the_hub_writes_a_starter_card_for_another_worktree_only_if_it_has_none(
    repo: Fixture, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = repo.add_worktree("rishi/feat-9-x")
    fake_prs(tmp_path, [])
    starter = (
        "## Done\n- Started from the hub; no report from the worker yet.\n\n## Why it matters\n- #9 (the x ticket).\n\n"
        "## Needs you\n- Nothing yet.\n\n## Verify\n- Nothing to verify yet.\n"
    )
    card = repo.ledger / "rishi__feat-9-x.md"

    code, out, _ = write(capsys, monkeypatch, "--dir", str(path), "--if-absent", stdin=starter)
    assert (code, out) == (0, f"{card}\nstatus: running (no PR yet)\n")
    assert 'ticket: "#9"' in frontmatter(card)
    assert body(card) == "\n" + starter

    card.write_text(card.read_text(encoding="utf-8").replace("Started", "Worker's own"), encoding="utf-8")
    code, out, err = write(capsys, monkeypatch, "--dir", str(path), "--if-absent", stdin=starter)
    assert (code, out, err) == (3, "", f"tower: {card} already exists, so nothing was written\n")
    assert "Worker's own" in card.read_text(encoding="utf-8")


def test_tower_state_reads_a_rendered_card(
    repo: Fixture, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = repo.add_worktree("feat-42-search")
    fake_prs(tmp_path, [])
    monkeypatch.chdir(path)
    assert write(capsys, monkeypatch, "--status", "blocked", "--reason", "waits on #41")[0] == 0

    assert cli.main(["state", "--json"]) == 0
    row = next(w for w in json.loads(capsys.readouterr().out)["worktrees"] if w["label"] == "feat-42-search")

    assert row["status"] == "blocked"
    assert {k: row["card"][k] for k in ("status", "ticket", "pr")} == {
        "status": "blocked",
        "ticket": "#42",
        "pr": "none",
    }


# ---------- card-resume.py ----------


def run_resume(
    cwd: Path, event: str = "UserPromptSubmit", python: str = sys.executable
) -> subprocess.CompletedProcess[str]:
    """Run the hook the way Claude Code does: its own process, python3 -I, the hook's JSON on stdin."""
    payload = json.dumps({"hook_event_name": event, "cwd": str(cwd), "session_id": "s-1"})
    return subprocess.run(
        [python, "-I", str(HOOK)], input=payload, capture_output=True, text=True, timeout=30, check=False
    )


@pytest.mark.parametrize("python", INTERPRETERS)
def test_the_skill_writes_its_card_through_the_plugin_under_any_python3(
    repo: Fixture, tmp_path: Path, python: str
) -> None:
    path = repo.add_worktree("feat-42-search")
    fake_prs(tmp_path, [])

    proc = subprocess.run(
        [python, "-I", str(SCRIPTS / "run-tower.py"), "card", "write", "--status", "blocked", "--reason", "#41 first"],
        input=NARRATIVE,
        cwd=path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    card = repo.ledger / "feat-42-search.md"
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, f"{card}\nstatus: blocked (you asked for it)\n", "")
    assert frontmatter(card)[-1] == 'reason: "#41 first"'


@pytest.mark.parametrize("python", INTERPRETERS)
def test_an_answer_re_renders_a_needs_you_card_as_running_and_keeps_its_body(
    repo: Fixture, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, python: str
) -> None:
    path = repo.add_worktree("feat-42-search")
    fake_prs(tmp_path, [])
    monkeypatch.chdir(path)
    assert write(capsys, monkeypatch, "--status", "needs-you", "--reason", "A or B?")[0] == 0
    card = repo.ledger / "feat-42-search.md"
    before = frontmatter(card)

    text = card.read_text(encoding="utf-8")
    card.write_text(text.replace("\n---\n", "\ntags:\n  - tower\n---\n", 1), encoding="utf-8")  # added in Obsidian
    before = frontmatter(card)

    proc = run_resume(path, "PostToolUse", python)

    assert proc.returncode == 0, proc.stderr
    after = frontmatter(card)
    assert after[0] == "status: running"
    assert UPDATED.fullmatch(after[2])
    # Same ticket, branch, pr, base and the user's tags; the reason is gone.
    assert after[1] == before[1] and after[3:] == before[3:6] + ["tags:", "  - tower"]
    assert body(card) == "\n" + NARRATIVE
    context = json.loads(proc.stdout)["hookSpecificOutput"]
    assert context["hookEventName"] == "PostToolUse"
    assert "switched from needs-you to running" in context["additionalContext"]


def test_an_answer_leaves_other_cards_alone_and_is_quiet_without_tower(
    repo: Fixture, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = repo.add_worktree("feat-42-search")
    fake_prs(tmp_path, [])
    monkeypatch.chdir(path)
    assert write(capsys, monkeypatch, "--status", "blocked", "--reason", "waits on #41")[0] == 0
    card = repo.ledger / "feat-42-search.md"
    before = card.read_text(encoding="utf-8")

    assert (run_resume(path).returncode, run_resume(path).stdout) == (0, "")
    assert card.read_text(encoding="utf-8") == before

    no_card = repo.add_worktree("feat-43")
    assert (run_resume(no_card).returncode, run_resume(no_card).stdout) == (0, "")
    assert not (repo.ledger / "feat-43.md").exists()

    (tmp_path / "tower.conf").write_text("wip_limit=3\n", encoding="utf-8")  # no projects_dir: not set up
    assert (run_resume(path).returncode, run_resume(path).stdout) == (0, "")
    assert (run_resume(tmp_path).returncode, run_resume(tmp_path).stdout) == (0, "")  # not a git repo


def test_an_answer_with_a_tower_conf_that_isnt_utf8_says_so_in_the_debug_log(repo: Fixture, tmp_path: Path) -> None:
    (tmp_path / "tower.conf").write_bytes(b"projects_dir=/Users/caf\xe9/vault\n")

    proc = run_resume(repo.main)

    assert (proc.returncode, proc.stdout) == (0, "")
    assert proc.stderr == f"tower: card-resume: {tmp_path / 'tower.conf'} isn't UTF-8 text (line 1)\n"


def test_a_stacked_branch_with_no_pr_yet_names_its_parent_as_its_base(
    repo: Fixture, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from conftest import track

    path = repo.add_worktree("feat-a")
    git(path, "checkout", "-q", "-b", "feat-b")
    track(repo.main, {"feat-a": ("main", None), "feat-b": ("feat-a", None)})
    fake_prs(tmp_path, [])
    monkeypatch.chdir(path)

    assert write(capsys, monkeypatch)[0] == 0

    assert 'base: "feat-a"' in frontmatter(repo.ledger / "feat-b.md")


def test_a_stack_worktrees_card_follows_it_to_its_next_branch_with_its_kickoff_and_log(
    repo: Fixture, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stack's worker creates each ticket's branch on top of the last (git-spice branch create switches the
    worktree to it): the card the hub started, with its kickoff and decisions log, stays this worktree's card."""
    path = repo.add_worktree("feat-a")
    fake_prs(tmp_path, [])
    monkeypatch.chdir(path)
    assert write(capsys, monkeypatch)[0] == 0
    monkeypatch.setattr(sys, "stdin", io.StringIO("/implement #1, #2 as a stack"))
    assert cli.main(["card", "kickoff"]) == 0
    assert cli.main(["decisions", "add", "chose X over Y because Z"]) == 0
    capsys.readouterr()

    git(path, "checkout", "-q", "-b", "feat-b")
    code, out, _ = write(capsys, monkeypatch)

    card = repo.ledger / "feat-a.md"
    assert (code, out.splitlines()[0]) == (0, str(card))
    assert not (repo.ledger / "feat-b.md").exists()
    assert 'branch: "feat-b"' in frontmatter(card)
    assert body(card).endswith("## Kickoff\n/implement #1, #2 as a stack\n")
    assert cli.main(["card-path"]) == 0
    assert capsys.readouterr().out.strip() == str(card)
    assert cli.main(["state", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    (row,) = [r for r in result["worktrees"] if r["path"] == str(path)]
    assert (row["card_file"], row["card"]["ticket"], result["orphan_cards"]) == ("feat-a.md", "none", [])
    assert "chose X over Y" in (repo.ledger / "_decisions" / "feat-a.md").read_text(encoding="utf-8")


def test_a_new_worktree_at_a_removed_ones_path_never_takes_over_that_orphan_card(
    repo: Fixture, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = repo.add_worktree("feat-a")
    fake_prs(tmp_path, [])
    monkeypatch.chdir(path)
    assert write(capsys, monkeypatch)[0] == 0
    old_card = repo.ledger / "feat-a.md"
    before = old_card.read_text(encoding="utf-8")
    monkeypatch.chdir(repo.main)
    git(repo.main, "worktree", "remove", "--force", str(path))  # removed outside tower clean: its card stays
    git(repo.main, "worktree", "add", "-q", "-b", "feat-b", str(path))
    monkeypatch.chdir(path)

    assert write(capsys, monkeypatch)[0] == 0

    assert (repo.ledger / "feat-b.md").exists()
    assert old_card.read_text(encoding="utf-8") == before


def test_an_answer_after_the_worktree_moved_branch_never_names_the_last_branchs_pr(
    repo: Fixture, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = repo.add_worktree("feat-a")
    head = git(path, "rev-parse", "HEAD").strip()
    fake_prs(tmp_path, [{"number": 11, "state": "OPEN", "baseRefName": "main", "headRefOid": head}])
    monkeypatch.chdir(path)
    assert write(capsys, monkeypatch, "--status", "needs-you", "--reason", "A or B?")[0] == 0
    git(path, "checkout", "-q", "-b", "feat-b")  # git-spice branch create, before the user answered

    proc = run_resume(path, "UserPromptSubmit", str(Path(sys.base_prefix) / "bin" / "python3"))

    assert proc.returncode == 0, proc.stderr
    fields = frontmatter(repo.ledger / "feat-a.md")
    assert ("status: running", 'branch: "feat-b"', "pr: unknown") == (fields[0], fields[3], fields[4])


def test_a_stack_worktrees_card_stays_running_when_its_bottom_pr_merged_and_the_rest_of_the_stack_is_open(
    repo: Fixture, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from conftest import track

    path = repo.add_worktree("feat-a")
    git(path, "branch", "feat-b")
    track(repo.main, {"feat-a": ("main", 11), "feat-b": ("feat-a", 12)})
    head = git(path, "rev-parse", "HEAD").strip()
    fake_prs_by_branch(
        tmp_path,
        {
            "feat-a": [{"number": 11, "state": "MERGED", "baseRefName": "main", "headRefOid": head}],
            "feat-b": [{"number": 12, "state": "OPEN", "baseRefName": "feat-a", "headRefOid": head}],
        },
    )
    monkeypatch.chdir(path)

    code, out, _ = write(capsys, monkeypatch)

    assert code == 0
    assert out.splitlines()[1] == "status: running (PR #11 is merged; its stack still has unmerged branches: feat-b)"

    # Squash-merged on GitHub (nothing of feat-b on main that git can see): the stack is finished.
    fake_prs_by_branch(
        tmp_path,
        {
            "feat-a": [{"number": 11, "state": "MERGED", "baseRefName": "main", "headRefOid": head}],
            "feat-b": [{"number": 12, "state": "MERGED", "baseRefName": "main", "headRefOid": head}],
        },
    )
    assert write(capsys, monkeypatch)[1].splitlines()[1] == "status: done (PR #11 is merged)"


def test_an_answer_during_a_conflicted_restack_keeps_the_cards_branch_and_pr(
    repo: Fixture, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = repo.add_worktree("feat-a")
    head = git(path, "rev-parse", "HEAD").strip()
    fake_prs(tmp_path, [{"number": 11, "state": "OPEN", "baseRefName": "main", "headRefOid": head}])
    monkeypatch.chdir(path)
    assert write(capsys, monkeypatch, "--status", "needs-you", "--reason", "Which side?")[0] == 0
    git(path, "checkout", "-q", "--detach")  # a rebase stopped on a conflict

    proc = run_resume(path, "UserPromptSubmit", str(Path(sys.base_prefix) / "bin" / "python3"))

    assert proc.returncode == 0, proc.stderr
    fields = frontmatter(repo.ledger / "feat-a.md")
    assert ("status: running", 'branch: "feat-a"', 'pr: "#11"') == (fields[0], fields[3], fields[4])


def test_a_record_of_the_card_that_cant_be_read_stops_the_write_instead_of_starting_a_new_card(
    repo: Fixture, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = repo.add_worktree("feat-a")
    fake_prs(tmp_path, [])
    monkeypatch.chdir(path)
    assert write(capsys, monkeypatch)[0] == 0
    git_dir = Path(git(path, "rev-parse", "--absolute-git-dir").strip())
    (git_dir / "tower-card").write_bytes(b"\xff\xfe not text")
    git(path, "checkout", "-q", "-b", "feat-b")

    code, _, err = write(capsys, monkeypatch)

    assert code == 1
    assert "tower-card (it isn't UTF-8)" in err
    assert not (repo.ledger / "feat-b.md").exists()


def test_a_merged_bottom_branch_whose_stack_cant_be_read_keeps_the_card_running(
    repo: Fixture, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from conftest import unreadable_tracking

    path = repo.add_worktree("feat-a")
    unreadable_tracking(repo.main, "feat-a")
    head = git(path, "rev-parse", "HEAD").strip()
    fake_prs(tmp_path, [{"number": 11, "state": "MERGED", "baseRefName": "main", "headRefOid": head}])
    monkeypatch.chdir(path)

    code, out, _ = write(capsys, monkeypatch)

    assert code == 0
    assert "status: running (PR #11 is merged; whether its stack is finished couldn't be checked)" in out
