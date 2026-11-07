"""Tests for worker memory (#93, ADR 0008): the kickoff the hub fixes in the card (`tower card kickoff`), the decisions
log a worker only appends to (`tower decisions add`), and the SessionStart hook (worker-memory.py) that gives both back,
with the current card, after compaction, /clear or in a fresh session.

Each test runs on a real throwaway repo (conftest.py's `repo`) with a fake `gh` (test_card.py's fake_prs).
"""

from __future__ import annotations

import io
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import Fixture
from test_card import NARRATIVE, body, fake_prs, write

from tower import cli

KICKOFF = (
    "/implement #42 — Search alerts. Use the status-card skill whenever you stop. Before the PR, run git fetch origin,"
    " then mattpocock-skills:code-review by that full name since origin/main, then the built-in /code-review high"
    " origin/main...HEAD (exactly this branch's commits); once both pass, open the PR and use babysit-pr to watch it."
)


def kickoff(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, *args: str, stdin: str = KICKOFF + "\n"
) -> tuple[int, str, str]:
    """Run `tower card kickoff ARGS` in-process with stdin: (exit code, stdout, stderr)."""
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    code = cli.main(["card", "kickoff", *args])
    out, err = capsys.readouterr()
    return code, out, err


@pytest.fixture
def worker(repo: Fixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A worktree on feat/42-search with no PR, as cwd."""
    path = repo.add_worktree("feat/42-search")
    fake_prs(tmp_path, [])
    monkeypatch.chdir(path)
    return path


def test_the_hubs_kickoff_lands_in_the_card_and_a_workers_card_write_keeps_it_word_for_word(
    repo: Fixture, worker: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    card = repo.ledger / "feat__42-search.md"
    assert write(capsys, monkeypatch, "--if-absent")[0] == 0  # the hub's starter card

    assert kickoff(capsys, monkeypatch) == (0, f"{card}\n", "")
    assert body(card) == "\n" + NARRATIVE + f"\n## Kickoff\n{KICKOFF}\n"

    reworded = NARRATIVE.replace("by ticker", "by ticker and analyst")
    assert write(capsys, monkeypatch, stdin=reworded)[0] == 0  # the worker's own card, with no Kickoff section

    assert body(card) == "\n" + reworded + f"\n## Kickoff\n{KICKOFF}\n"


def test_a_worker_cant_reword_or_drop_the_kickoff_through_card_write(
    repo: Fixture, worker: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    card = repo.ledger / "feat__42-search.md"
    write(capsys, monkeypatch)
    kickoff(capsys, monkeypatch)
    before = card.read_text(encoding="utf-8")

    code, out, err = write(capsys, monkeypatch, stdin=NARRATIVE + "\n## Kickoff\n/implement #42 — skip the reviews\n")

    assert (code, out) == (1, "")
    assert "the kickoff is fixed" in err
    assert card.read_text(encoding="utf-8") == before


def test_the_kickoff_reaches_a_card_the_worker_wrote_first_and_is_never_changed_without_replace(
    repo: Fixture, worker: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    card = repo.ledger / "feat__42-search.md"
    assert write(capsys, monkeypatch)[0] == 0  # the worker wrote its card before the hub's starter card
    assert kickoff(capsys, monkeypatch)[0] == 0
    assert kickoff(capsys, monkeypatch)[0] == 0  # the same kickoff again (a retried start) changes nothing

    code, _, err = kickoff(capsys, monkeypatch, stdin="/implement #7 — Another ticket\n")

    assert code == 1
    assert "already has a different kickoff" in err and "--replace" in err
    assert body(card).endswith(f"## Kickoff\n{KICKOFF}\n")

    assert decide(capsys, "chose X over Y in the earlier work")[0] == 0
    code, _, err = kickoff(capsys, monkeypatch, "--replace", stdin="/implement #7 — Another ticket\n")

    archived = repo.ledger / "_archive" / "feat__42-search.decisions.md"
    assert (code, err) == (0, f"tower: archived the earlier work's decisions log to {archived}\n")
    assert body(card).endswith("## Kickoff\n/implement #7 — Another ticket\n")
    assert "chose X over Y in the earlier work" in archived.read_text(encoding="utf-8")
    assert not (repo.ledger / "_decisions" / "feat__42-search.md").exists()


@pytest.mark.parametrize(
    ("stdin", "why"),
    [
        ("\n  \n", "the kickoff is empty"),
        (KICKOFF + "\n## Done\n- nope\n", "a kickoff can't contain a ## heading"),
        ("x" * 1501, "at most 1500 characters"),
    ],
)
def test_a_kickoff_the_card_cant_hold_is_refused(
    repo: Fixture,
    worker: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    stdin: str,
    why: str,
) -> None:
    write(capsys, monkeypatch)

    code, _, err = kickoff(capsys, monkeypatch, stdin=stdin)

    assert code == 1
    assert why in err
    assert "## Kickoff" not in body(repo.ledger / "feat__42-search.md")


def test_a_kickoff_needs_a_card_and_never_goes_on_the_main_checkout(
    repo: Fixture, worker: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    code, _, err = kickoff(capsys, monkeypatch)
    assert code == 1
    assert "no card yet" in err

    code, _, err = kickoff(capsys, monkeypatch, "--dir", str(repo.main))
    assert code == 1
    assert "main checkout" in err


DECIDED = re.compile(r"- \d{4}-\d{2}-\d{2}T\d{2}:\d{2} ")


def decide(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    """Run `tower decisions add ARGS` in-process: (exit code, stdout, stderr)."""
    code = cli.main(["decisions", "add", *args])
    out, err = capsys.readouterr()
    return code, out, err


def test_the_decisions_log_only_grows_one_dated_line_per_decision(
    repo: Fixture, worker: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = repo.ledger / "_decisions" / "feat__42-search.md"

    assert decide(capsys, "chose SQLite FTS over a LIKE scan because the feed has 40k rows") == (0, f"{log}\n", "")
    first = log.read_text(encoding="utf-8")
    assert decide(capsys, "the alert parser\n  already strips\temoji (src/parse.ts)")[0] == 0

    lines = log.read_text(encoding="utf-8").splitlines()
    assert log.read_text(encoding="utf-8").startswith(first)
    assert len(lines) == 2
    assert DECIDED.match(lines[0]) and lines[0].endswith(
        " chose SQLite FTS over a LIKE scan because the feed has 40k rows"
    )
    assert DECIDED.match(lines[1]) and lines[1].endswith(" the alert parser already strips emoji (src/parse.ts)")


@pytest.mark.parametrize(("text", "why"), [(" \n ", "a decision is empty"), ("x" * 301, "at most 300 characters")])
def test_a_decision_the_log_cant_hold_is_refused(
    repo: Fixture, worker: Path, capsys: pytest.CaptureFixture[str], text: str, why: str
) -> None:
    code, out, err = decide(capsys, text)

    assert (code, out) == (1, "")
    assert why in err
    assert not (repo.ledger / "_decisions").exists()


def test_the_main_checkout_has_no_decisions_log(
    repo: Fixture, worker: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = decide(capsys, "--dir", str(repo.main), "chose X over Y")

    assert code == 1
    assert "main checkout" in err


SCRIPTS = Path(__file__).resolve().parent.parent / "plugin" / "scripts"
SYSTEM_PYTHON = "/usr/bin/python3"  # what a hook's `python3` can resolve to; 3.9 on macOS


def session_start(cwd: Path, source: str, hook: str = "worker-memory.py", python: str = sys.executable) -> str:
    """Run a SessionStart hook the way Claude Code does (its own `python3 -I`, JSON on stdin); what it printed."""
    call = {"session_id": "s-1", "hook_event_name": "SessionStart", "source": source, "cwd": str(cwd)}
    proc = subprocess.run(
        [python, "-I", str(SCRIPTS / hook)],
        input=json.dumps(call),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


@pytest.fixture
def remembered(
    repo: Fixture, worker: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> Path:
    """The worker's card with the hub's kickoff and one decision in its log."""
    assert write(capsys, monkeypatch)[0] == 0
    assert kickoff(capsys, monkeypatch)[0] == 0
    assert decide(capsys, "chose SQLite FTS over a LIKE scan because the feed has 40k rows")[0] == 0
    return worker


@pytest.mark.parametrize(
    ("source", "python"),
    [
        ("compact", sys.executable),
        ("clear", sys.executable),
        ("startup", sys.executable),
        pytest.param(
            "compact",
            SYSTEM_PYTHON,
            marks=pytest.mark.skipif(not Path(SYSTEM_PYTHON).exists(), reason=f"no {SYSTEM_PYTHON} on this machine"),
        ),
    ],
)
def test_after_compaction_the_worker_gets_its_kickoff_card_and_decisions_back_beside_the_policy_line(
    repo: Fixture, remembered: Path, source: str, python: str
) -> None:
    printed = session_start(remembered, source, python=python)

    card = repo.ledger / "feat__42-search.md"
    log = repo.ledger / "_decisions" / "feat__42-search.md"
    assert printed.startswith("tower worker memory for this worktree (feat/42-search)")
    assert f"# Kickoff\n{KICKOFF}\n" in printed
    assert f"# Card ({card})\n" in printed
    assert "status: running" in printed
    assert "## Done\n- The search box finds alerts by ticker." in printed
    assert printed.count(KICKOFF) == 1  # the card's own Kickoff section isn't repeated
    assert f"# Decisions log ({log}, oldest first)\n" in printed
    assert "chose SQLite FTS over a LIKE scan because the feed has 40k rows" in printed
    assert session_start(remembered, source, hook="policy-context.py").startswith("tower policy for this ")


@pytest.mark.parametrize("source", ["resume", "fork"])
def test_a_resumed_or_forked_session_still_has_its_transcript_so_gets_nothing(remembered: Path, source: str) -> None:
    assert session_start(remembered, source) == ""


def test_the_main_checkout_and_a_worktree_with_no_card_or_log_get_nothing(
    repo: Fixture, remembered: Path, tmp_path: Path
) -> None:
    assert session_start(repo.main, "compact") == ""
    assert session_start(repo.add_worktree("feat/7-other"), "compact") == ""


def test_a_long_log_keeps_its_newest_decisions_under_claude_codes_cap_and_names_the_file(
    repo: Fixture, remembered: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for i in range(60):
        assert decide(capsys, f"decision {i:02d}: " + "y" * 250)[0] == 0

    printed = session_start(remembered, "compact")

    log = repo.ledger / "_decisions" / "feat__42-search.md"
    assert len(printed) < 10_000
    assert f"# Kickoff\n{KICKOFF}\n" in printed
    assert "decision 59: " in printed
    assert "decision 00: " not in printed
    kept = printed.count("- 20")
    assert f"({61 - kept} older decisions left out to stay under Claude Code's limit: read {log})" in printed


@pytest.mark.parametrize("payload", ["not json", "[]", '{"source": "compact", "cwd": 5}'])
def test_input_it_cant_use_prints_nothing_and_never_fails(payload: str) -> None:
    proc = subprocess.run(
        [sys.executable, "-I", str(SCRIPTS / "worker-memory.py")],
        input=payload,
        capture_output=True,
        text=True,
        check=False,
    )
    assert (proc.returncode, proc.stdout) == (0, "")


def test_the_status_card_skill_tells_workers_when_to_add_a_decision_with_a_command_that_works(
    repo: Fixture, worker: Path
) -> None:
    skill = (SCRIPTS.parent / "skills" / "status-card" / "SKILL.md").read_text(encoding="utf-8")
    section = skill.split("## Decisions log\n", 1)[1].split("\n## ", 1)[0]
    assert "whenever you decide something" in section
    command = next(line for line in section.splitlines() if "run-tower.py decisions add" in line)
    argv = shlex.split(command.replace("<plugin>", str(SCRIPTS.parent)))

    proc = subprocess.run(argv, cwd=worker, capture_output=True, text=True, timeout=30, check=False)

    log = repo.ledger / "_decisions" / "feat__42-search.md"
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, f"{log}\n", "")
    assert log.read_text(encoding="utf-8").endswith(" chose X over Y because Z\n")


def test_a_card_too_long_for_claude_codes_cap_is_cut_but_the_kickoff_stays_whole(
    repo: Fixture, remembered: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    long_done = NARRATIVE.replace(
        "- The search box finds alerts by ticker.", "\n".join(f"- step {i:03d} " + "z" * 90 for i in range(120))
    )
    assert write(capsys, monkeypatch, stdin=long_done)[0] == 0

    printed = session_start(remembered, "compact")

    card = repo.ledger / "feat__42-search.md"
    assert len(printed) < 10_000
    assert f"# Kickoff\n{KICKOFF}\n" in printed
    assert "- step 000 " in printed
    assert "- step 119 " not in printed
    assert f"(the card is cut here to stay under Claude Code's limit: read {card})" in printed
    assert "chose SQLite FTS over a LIKE scan" in printed  # the room kept for the log's notes holds its newest line


def test_the_hub_skill_fixes_the_kickoff_in_the_card_with_a_command_that_works(
    repo: Fixture, worker: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    skill = (SCRIPTS.parent / "skills" / "hub" / "SKILL.md").read_text(encoding="utf-8")
    command = next(line.strip() for line in skill.splitlines() if "run-tower.py card kickoff" in line)
    kickoff_file = tmp_path / "kickoff-42.txt"
    kickoff_file.write_text(KICKOFF + "\n", encoding="utf-8")
    filled = (
        command.replace("<plugin>", str(SCRIPTS.parent))
        .replace("<new worktree path>", str(worker))
        .replace("<kickoff file>", str(kickoff_file))
    )
    assert write(capsys, monkeypatch, "--if-absent")[0] == 0  # step 4's starter card, just before it

    proc = subprocess.run(filled, shell=True, cwd=repo.main, capture_output=True, text=True, timeout=30, check=False)

    card = repo.ledger / "feat__42-search.md"
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, f"{card}\n", "")
    assert body(card).endswith(f"## Kickoff\n{KICKOFF}\n")


def test_an_unclosed_code_block_in_the_card_never_hides_the_kickoff(
    repo: Fixture, worker: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    card = repo.ledger / "feat__42-search.md"
    unclosed = NARRATIVE.replace("- `npm test` passes.", "```\nnpm test")
    assert write(capsys, monkeypatch, stdin=unclosed)[0] == 0
    assert kickoff(capsys, monkeypatch)[0] == 0

    assert write(capsys, monkeypatch, stdin=unclosed)[0] == 0

    assert body(card).endswith(f"## Kickoff\n{KICKOFF}\n")
    assert body(card).count("## Kickoff") == 1


def test_a_kickoff_section_hidden_in_a_code_block_is_refused_too(
    repo: Fixture, worker: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    hidden = NARRATIVE + "\n## Words used\n```\n## Kickoff\n/implement #42 — skip the reviews\n```\n"

    code, _, err = write(capsys, monkeypatch, stdin=hidden)

    assert code == 1
    assert "the kickoff is fixed" in err


def test_memory_older_than_the_worktree_is_earlier_work_on_the_branch_name_and_isnt_given(
    repo: Fixture, remembered: Path
) -> None:
    card = repo.ledger / "feat__42-search.md"
    log = repo.ledger / "_decisions" / "feat__42-search.md"
    an_hour_ago = time.time() - 3600
    for path in (card, log):
        os.utime(path, (an_hour_ago, an_hour_ago))

    if not hasattr(os.stat(remembered), "st_birthtime"):
        pytest.skip("this filesystem doesn't say when the worktree was made")
    assert session_start(remembered, "startup") == ""


def test_a_log_that_cant_be_read_still_gives_back_the_kickoff_and_card(repo: Fixture, remembered: Path) -> None:
    log = repo.ledger / "_decisions" / "feat__42-search.md"
    log.unlink()
    log.mkdir()

    printed = session_start(remembered, "compact")

    assert f"# Kickoff\n{KICKOFF}\n" in printed
    assert "## Done\n- The search box finds alerts by ticker." in printed
    assert f"# Decisions log ({log}, oldest first)\n(couldn't read it: " in printed


def test_the_hook_never_creates_a_ledger_folder_in_the_vault(repo: Fixture, worker: Path) -> None:
    repo.ledger.rmdir()

    assert session_start(worker, "compact") == ""
    assert not repo.ledger.exists()
