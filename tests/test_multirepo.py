"""Tests for `tower state --all`: every repo this machine knows (a Ledger folder in the vault, or events in the log),
each with its own effective state, so the hub can answer "what needs me across everything?".

Builds on conftest's `repo` fixture (a repo named `repo`, its ledger in a fake vault, a fake `claude`) and adds a
second repo, `other`, beside it in the same vault and event log.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from conftest import Fixture, git, interactive

from tower import cli, events


def second_repo(repo: Fixture, name: str = "other") -> Path:
    """Another repo next to the fixture's, with its own Ledger folder in the same vault; its main checkout."""
    main = repo.main.parent / name
    main.mkdir()
    git(main, "init", "-q", "-b", "main")
    git(main, "commit", "-q", "--allow-empty", "-m", "start")
    (repo.ledger.parent.parent / name / "Ledger").mkdir(parents=True)
    return main


def log_event(repo: Fixture, repo_path: Path, worktree: Path | None = None) -> None:
    """One event in the machine's log from a session in repo_path (a stop, so it doesn't stand in for a live one)."""
    where = str(worktree or repo_path)
    event = events.Event("2026-11-06T04:12:33.120Z", "stop", "s-1", str(repo_path), where, "main", {})
    events.append(event, repo.tower_dir)


def state_all(capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    """Run `tower state --all --json` in-process and parse what it prints."""
    assert cli.main(["state", "--all", "--json"]) == 0
    result: dict[str, object] = json.loads(capsys.readouterr().out)
    return result


def listed(result: dict[str, object], key: str) -> list[Any]:
    """One of the report's lists."""
    value = result[key]
    assert isinstance(value, list)
    return value


def test_two_repos_in_the_event_log_are_both_listed(repo: Fixture, capsys: pytest.CaptureFixture[str]) -> None:
    other = second_repo(repo)
    log_event(repo, repo.main)
    log_event(repo, other)

    result = state_all(capsys)

    assert sorted(r["repo"] for r in listed(result, "repos")) == ["other", "repo"]


def test_a_ledger_with_no_logged_checkout_is_listed_as_cards_only(
    repo: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    log_event(repo, repo.main)
    work = repo.ledger.parent.parent / "work-proj" / "Ledger"
    work.mkdir(parents=True)
    (work / "feat__9.md").write_text("---\nstatus: needs-you\n---\n", encoding="utf-8")
    (work / "_backlog.md").write_text("- [ ] an idea\n", encoding="utf-8")

    result = state_all(capsys)

    assert [r["repo"] for r in listed(result, "repos")] == ["repo"]
    assert result["cards_only"] == [
        {
            "project": "work-proj",
            "ledger": str(work),
            "cards": [{"name": "feat__9", "status": "needs-you", "path": str(work / "feat__9.md")}],
        }
    ]


def test_a_repo_mapped_to_a_renamed_project_claims_that_ledger(
    repo: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    """project.<repo>= in tower.conf points the repo at another project folder: that Ledger is the repo's, not cards
    only, and the repo's report reads its cards."""
    renamed = repo.ledger.parent.parent / "Renamed" / "Ledger"
    renamed.mkdir(parents=True)
    (renamed / "feat-1.md").write_text("---\nstatus: running\n---\n", encoding="utf-8")
    (repo.ledger / "old.md").write_text("---\nstatus: done\n---\n", encoding="utf-8")
    conf = repo.ledger.parents[2] / "tower.conf"
    conf.write_text(conf.read_text(encoding="utf-8") + "project.repo=Renamed\n", encoding="utf-8")
    log_event(repo, repo.main)

    result = state_all(capsys)

    assert [r["ledger"] for r in listed(result, "repos")] == [str(renamed)]
    assert [c["project"] for c in listed(result, "cards_only")] == ["repo"]


def test_a_logged_repo_whose_folder_is_gone_is_skipped_with_a_note(
    repo: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    log_event(repo, repo.main)
    log_event(repo, repo.main.parent / "deleted")

    result = state_all(capsys)

    assert [r["repo"] for r in listed(result, "repos")] == ["repo"]
    gone = repo.main.parent / "deleted"
    assert f"skipped 1 repo from the event log whose folder is gone: {gone}" in listed(result, "notes")


def test_repo_and_all_cant_be_given_together(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["state", "--all", "--repo", "."])
    assert exit_info.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err


def section(text: str, start: str) -> list[str]:
    """The lines of text from the line starting with start up to the next `== REPO`/`== CARDS ONLY` line."""
    lines = text.splitlines()
    first = next(i for i, line in enumerate(lines) if line.startswith(start))
    rest = lines[first + 1 :]
    end = next((i for i, line in enumerate(rest) if line.startswith(("== REPO:", "== CARDS ONLY:"))), len(rest))
    return rest[:end]


def group(lines: list[str], title: str) -> list[str]:
    """The rows under one GROUPS title (up to the next title)."""
    start = lines.index(title) + 1
    end = next((i for i in range(start, len(lines)) if not lines[i].startswith(("-", "("))), len(lines))
    return lines[start:end]


def test_the_hub_report_groups_each_repo_and_lists_the_urgent_one_first(
    repo: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    building = repo.add_worktree("feat-1")
    repo.write_card("feat-1", "running")
    other = second_repo(repo)
    git(other, "worktree", "add", "-q", "-b", "fix-2", str(other.parent / "other-fix-2"))
    (repo.ledger.parent.parent / "other" / "Ledger" / "fix-2.md").write_text(
        '---\nstatus: running\nticket: "#2"\n---\n', encoding="utf-8"
    )
    repo.sessions(
        interactive("builder", building, "busy"),
        interactive("asker", other.parent / "other-fix-2", "waiting", waitingFor="input needed"),
    )
    log_event(repo, repo.main, building)
    log_event(repo, other)

    assert cli.main(["state", "--all"]) == 0
    text = capsys.readouterr().out

    summary = section(text, "== ALL REPOS")
    assert summary[0].startswith("- other | needs-you: 1 | blocked: 0 | running: 0 |")
    assert summary[1].startswith("- repo | needs-you: 0 | blocked: 0 | running: 1 |")
    assert "WIP across repos: 2 (the limit counts per repo)" in summary

    other_ledger = repo.ledger.parent.parent / "other" / "Ledger"
    other_report = section(text, f"== REPO: other | {other} | {other_ledger} ==")
    assert group(other_report, "NEEDS YOU") == [
        "- fix-2 | ticket: #2 | session asker is waiting on you (input needed); the card says running"
    ]
    assert group(other_report, "RUNNING") == ["(none)"]
    repo_report = section(text, f"== REPO: repo | {repo.main} | {repo.ledger} ==")
    assert group(repo_report, "NEEDS YOU") == ["(none)"]
    assert group(repo_report, "RUNNING") == ["- feat-1 | ticket: #42 | session builder is busy"]
    assert text.index("== REPO: other") < text.index("== REPO: repo")


def test_live_sessions_are_read_once_for_every_repo(repo: Fixture, capsys: pytest.CaptureFixture[str]) -> None:
    calls = repo.agents_json.parent / "calls"
    fake_claude = repo.agents_json.parent / "bin" / "claude"
    fake_claude.write_text(f'#!/bin/sh\necho x >> "{calls}"\nexec cat "{repo.agents_json}"\n', encoding="utf-8")
    log_event(repo, repo.main)
    log_event(repo, second_repo(repo))

    state_all(capsys)

    assert calls.read_text(encoding="utf-8").count("x") == 1


def test_with_no_log_and_no_vault_it_says_there_is_nothing(
    repo: Fixture, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TOWER_CONF", str(repo.agents_json.parent / "missing.conf"))
    monkeypatch.chdir(repo.agents_json.parent)  # outside any repo

    assert cli.main(["state", "--all"]) == 0
    out = capsys.readouterr().out

    assert "(none: no repo in the event log and no Ledger folder with cards in the vault)" in out
    assert "NOTE: no projects_dir in" in out


def test_the_repo_it_runs_in_is_known_even_before_any_event(repo: Fixture, capsys: pytest.CaptureFixture[str]) -> None:
    """The hub runs `tower state --all` from its main checkout: that repo gets its worktree facts even on a machine
    whose event log is still empty, instead of showing as cards only. Run from a worktree, it's the same repo."""
    building = repo.add_worktree("feat-1")
    log_event(repo, repo.main)  # logged under the main checkout's path: not listed twice
    os.chdir(building)

    result = state_all(capsys)

    assert [(r["repo"], r["main_checkout"]) for r in listed(result, "repos")] == [("repo", str(repo.main))]
    assert listed(result, "cards_only") == []
    (repo.tower_dir / "events.jsonl").unlink()

    result = state_all(capsys)

    assert [r["repo"] for r in listed(result, "repos")] == ["repo"]


def test_a_needs_you_card_with_no_checkout_ranks_above_a_calm_repo_and_empty_ledgers_are_left_out(
    repo: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    log_event(repo, repo.main)
    work = repo.ledger.parents[1] / "work-proj" / "Ledger"
    work.mkdir(parents=True)
    (work / "feat__9.md").write_text("---\nstatus: needs-you\n---\n", encoding="utf-8")
    (repo.ledger.parents[1] / "empty-proj" / "Ledger").mkdir(parents=True)

    assert cli.main(["state", "--all"]) == 0
    text = capsys.readouterr().out

    summary = section(text, "== ALL REPOS")
    assert summary[0] == f"- work-proj | cards only: 1 card, no checkout tower could read | needs-you: 1 | {work}"
    assert summary[1].startswith("- repo | needs-you: 0 |")
    assert "empty-proj" not in text
    assert text.index("== CARDS ONLY: work-proj") < text.index("== REPO: repo")


def test_an_unreadable_config_is_noted_once(
    repo: Fixture, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    folder_as_conf = repo.agents_json.parent / "conf-dir"
    folder_as_conf.mkdir()
    monkeypatch.setenv("TOWER_CONF", str(folder_as_conf))

    notes = listed(state_all(capsys), "notes")

    assert sum("Ledger folders weren't looked for" in note for note in notes) == 1


def test_a_tower_conf_that_isnt_utf8_is_noted_and_every_repo_still_listed(
    repo: Fixture, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    conf = repo.agents_json.parent / "latin1.conf"
    conf.write_bytes(b"projects_dir=/Users/caf\xe9/vault\n")
    monkeypatch.setenv("TOWER_CONF", str(conf))
    other = second_repo(repo)
    log_event(repo, other)

    result = state_all(capsys)

    assert f"{conf} isn't UTF-8 text (line 1), so Ledger folders weren't looked for" in listed(result, "notes")
    assert {report["repo"] for report in listed(result, "repos")} == {"repo", "other"}


def test_a_project_folder_differing_only_in_case_is_the_repos_ledger(
    repo: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    """On a case-insensitive disk (macOS's default) vault/Repo/Ledger is the folder repo `repo` reads its cards from:
    its cards aren't listed again as cards only."""
    vault = repo.ledger.parents[1]
    (vault / "repo").rename(vault / "Repo")
    if not (vault / "repo").exists():
        pytest.skip("case-sensitive file system: Repo and repo are different folders here")
    (vault / "Repo" / "Ledger" / "old.md").write_text("---\nstatus: needs-you\n---\n", encoding="utf-8")
    log_event(repo, repo.main)

    result = state_all(capsys)

    assert [r["repo"] for r in listed(result, "repos")] == ["repo"]
    assert listed(result, "cards_only") == []


def test_a_note_about_the_machine_prints_once_not_once_per_repo(
    repo: Fixture, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo.agents_json.parent / "bin" / "claude").unlink()
    monkeypatch.setenv("PATH", str(repo.agents_json.parent / "bin") + os.pathsep + "/usr/bin:/bin")
    log_event(repo, repo.main)
    log_event(repo, second_repo(repo))

    assert cli.main(["state", "--all"]) == 0
    out = capsys.readouterr().out

    assert out.count("claude CLI not on PATH") == 1


def test_the_gone_note_names_five_folders_and_counts_the_rest(
    repo: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    for n in range(7):
        log_event(repo, repo.main.parent / f"deleted-{n}")

    notes = listed(state_all(capsys), "notes")

    gone = [note for note in notes if "whose folder is gone" in note]
    assert len(gone) == 1
    assert gone[0].startswith("skipped 7 repos from the event log whose folder is gone: ")
    assert gone[0].endswith("deleted-4 and 2 more")


def test_each_repo_heading_names_its_ledger_so_the_hub_can_open_a_rows_card_file(
    repo: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    """Each WORKTREES row names its card_file; the hub opens <Ledger>/<card_file>, so the heading names the Ledger."""
    repo.add_worktree("feat-1")
    repo.write_card("feat-1", "needs-you")
    log_event(repo, repo.main)

    assert cli.main(["state", "--all"]) == 0
    text = capsys.readouterr().out

    heading = next(line for line in text.splitlines() if line.startswith("== REPO: repo |"))
    assert heading == f"== REPO: repo | {repo.main} | {repo.ledger} =="
    ledger_folder = Path(heading.removesuffix(" ==").split(" | ")[2])
    row = next(r for r in section(text, heading) if r.startswith("- feat-1 |"))
    card_file = next(f.removeprefix("card_file: ") for f in row.split(" | ") if f.startswith("card_file: "))
    assert "status: needs-you" in (ledger_folder / card_file).read_text(encoding="utf-8")


def test_a_repo_with_no_ledger_folder_says_so_in_its_heading(
    repo: Fixture, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TOWER_CONF", str(repo.agents_json.parent / "missing.conf"))
    os.chdir(repo.main)

    assert cli.main(["state", "--all"]) == 0
    text = capsys.readouterr().out

    assert f"== REPO: repo | {repo.main} | Ledger folder unknown (see NOTE) ==" in text.splitlines()
    assert any(line.startswith("NOTE: no projects_dir in") for line in text.splitlines())
