"""Tests for plugin/scripts/confirm-public.py, the PreToolUse hook that forces an approval prompt.

Each test runs the hook the way Claude Code does: a separate `python3 -I` process with the tool call
as JSON on stdin. The hook either prints an "ask" decision or prints nothing (Claude Code's normal
permission rules then decide). TOWER_CONF points at a temporary tower.conf holding the mode under test.

Cases are grouped by how the hook should treat them:
  ALWAYS_ASK  - GitHub writes other people see (comments, reviews, issues, releases, labels, gh api writes)
  PR_CHANGES  - gh pr writes a worker needs for its own PR; ask unless pr_approval=none
  PUSHES      - plain pushes; ask only when push_approval=every
  FORCE       - force pushes, deletes and +refspecs; ask unless push_approval=none
  READS       - read-only commands; never ask
HANDOFF.md recorded only the counts of the original manual run, not the commands, so these cases are
rebuilt from the hook's docstring and cover every rule it states.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent.parent / "plugin" / "scripts" / "confirm-public.py"
PUSH_MODES = ["every", "force", "none"]

ALWAYS_ASK = [
    'gh pr comment 5 --body "looks good"',
    "gh pr review 5 --approve",
    "gh pr review 5 --request-changes -b 'see inline'",
    "gh -R owner/repo pr comment 5 -b hi",
    "gh --repo owner/repo issue comment 3 -b hi",
    "gh issue comment 3 --body x",
    "gh issue create --title t --body b",
    "gh issue edit 3 --add-label bug",
    "gh issue close 3",
    "gh issue reopen 3",
    "gh issue delete 3 --yes",
    "gh issue transfer 3 owner/other",
    "gh release create v1.0.0 --notes x",
    "gh release upload v1.0.0 dist.zip",
    "gh label create triage",
    "gh api repos/a/b/issues -f title=x",
    "gh api repos/a/b/issues --input body.json",
    "gh api -X PATCH repos/a/b/issues/1",
    "gh api --method=DELETE repos/a/b/git/refs/heads/x",
    "gh api graphql -f query='mutation { addStar(input: {starrableId: \"x\"}) { clientMutationId } }'",
    "gh api graphql -F query=@query.graphql",
    "git status && gh issue comment 3 -b x",
]
PR_CHANGES = [
    "gh pr create --fill",
    "gh pr edit 5 --base main",
    "gh pr merge 5 --squash --delete-branch",
    "gh pr close 5",
    "gh pr reopen 5",
    "gh pr ready 5",
    "gh pr update-branch 5",
]
PUSHES = [
    "git push",
    "git push origin feature",
    "git -C /tmp/repo push origin x",
    "git status && git push -u origin HEAD",
]
FORCE = [
    "git push --force origin x",
    "git push -f",
    "git push --force-with-lease",
    "git push origin --delete old",
    "git push origin +main",
    "git push origin :old",
]
READS = [
    "gh pr view 5",
    "gh pr list --state open",
    "gh pr checks 5",
    "gh pr diff 5",
    "gh issue view 3 --comments",
    "gh api repos/a/b/pulls",
    "gh api -X GET repos/a/b/issues -f per_page=100",
    "gh api graphql -f query='query { viewer { login } }'",
    "git status",
    "git log --oneline -5",
    "ls -la",
]


def run_hook(
    tmp_path: Path, call: object, push: str = "every", pr: str = "every", write_conf: bool = True
) -> dict[str, object] | None:
    """Run the hook on CALL with a tower.conf holding PUSH and PR (none at all if not WRITE_CONF).

    Returns the hook's decision, or None if it printed nothing.
    """
    conf = tmp_path / "tower.conf"
    if write_conf:
        conf.write_text(f"push_approval={push}\npr_approval={pr}\n", encoding="utf-8")
    stdin = call if isinstance(call, str) else json.dumps(call)
    proc = subprocess.run(
        [sys.executable, "-I", str(HOOK)],
        input=stdin,
        capture_output=True,
        text=True,
        env={"TOWER_CONF": str(conf), "HOME": str(tmp_path)},
        check=True,
        timeout=10,
    )
    if not proc.stdout.strip():
        return None
    decision: dict[str, object] = json.loads(proc.stdout)["hookSpecificOutput"]
    return decision


def asks(decision: dict[str, object] | None) -> bool:
    """Whether the hook's output forces an approval prompt."""
    return decision is not None and decision["permissionDecision"] == "ask"


def bash(command: str) -> dict[str, object]:
    """A Bash tool call as Claude Code sends it."""
    return {"tool_name": "Bash", "tool_input": {"command": command}}


@pytest.mark.parametrize("push", PUSH_MODES)
@pytest.mark.parametrize("command", ALWAYS_ASK)
def test_github_writes_always_ask(tmp_path: Path, command: str, push: str) -> None:
    """Comments, reviews, issue/release/label writes and gh api writes ask in every mode."""
    assert asks(run_hook(tmp_path, bash(command), push=push, pr="none"))


@pytest.mark.parametrize("push", PUSH_MODES)
@pytest.mark.parametrize("command", PR_CHANGES)
def test_pr_changes_ask_when_pr_approval_is_every(tmp_path: Path, command: str, push: str) -> None:
    """A worker's own PR changes ask while pr_approval=every."""
    assert asks(run_hook(tmp_path, bash(command), push=push, pr="every"))


@pytest.mark.parametrize("push", PUSH_MODES)
@pytest.mark.parametrize("command", PR_CHANGES)
def test_pr_changes_pass_when_pr_approval_is_none(tmp_path: Path, command: str, push: str) -> None:
    """pr_approval=none lets a worker open and merge its own PR without a prompt."""
    assert not asks(run_hook(tmp_path, bash(command), push=push, pr="none"))


@pytest.mark.parametrize("push", PUSH_MODES)
@pytest.mark.parametrize("command", PUSHES)
def test_plain_push_asks_only_when_push_approval_is_every(tmp_path: Path, command: str, push: str) -> None:
    """A plain push asks under push_approval=every and passes under force and none."""
    assert asks(run_hook(tmp_path, bash(command), push=push)) == (push == "every")


@pytest.mark.parametrize("push", PUSH_MODES)
@pytest.mark.parametrize("command", FORCE)
def test_force_push_asks_unless_push_approval_is_none(tmp_path: Path, command: str, push: str) -> None:
    """Force pushes, deletes and +refspecs ask under every and force."""
    assert asks(run_hook(tmp_path, bash(command), push=push)) == (push != "none")


@pytest.mark.parametrize("push", PUSH_MODES)
@pytest.mark.parametrize("command", READS)
def test_reads_pass(tmp_path: Path, command: str, push: str) -> None:
    """Read-only commands print nothing, leaving the decision to Claude Code's rules."""
    assert run_hook(tmp_path, bash(command), push=push) is None


@pytest.mark.parametrize("value", ["sometimes", ""])
def test_unknown_push_approval_is_treated_as_every(tmp_path: Path, value: str) -> None:
    """A push_approval value the hook doesn't know falls back to every, the safest mode."""
    assert asks(run_hook(tmp_path, bash("git push"), push=value))


def test_unknown_pr_approval_is_treated_as_every(tmp_path: Path) -> None:
    """A pr_approval value the hook doesn't know falls back to every."""
    assert asks(run_hook(tmp_path, bash("gh pr merge 5"), pr="whenever"))


def test_missing_config_is_treated_as_every(tmp_path: Path) -> None:
    """With no tower.conf at all, a push still asks."""
    assert asks(run_hook(tmp_path, bash("git push"), write_conf=False))


RULE_FILE_EDITS = [
    ("Edit", "CLAUDE.local.md"),
    ("Write", "/repo/worktrees/x/CLAUDE.local.md"),
    ("MultiEdit", "~/.claude/tower/rules/Poot.md"),
    ("Edit", "/Users/someone/.claude/tower/rules/tower.md"),
]
RULE_FILE_COMMANDS = [
    "echo 'use pytest' >> CLAUDE.local.md",
    "cat ~/.claude/tower/rules/Poot.md",
    "sed -i '' s/a/b/ ~/.claude/tower/rules/Poot.md",
    "ln -sf ~/.claude/tower/rules/Poot.md CLAUDE.local.md",
]


@pytest.mark.parametrize("push", PUSH_MODES)
@pytest.mark.parametrize(("tool", "file_path"), RULE_FILE_EDITS)
def test_edit_of_rule_file_asks(tmp_path: Path, tool: str, file_path: str, push: str) -> None:
    """Edit/Write/MultiEdit of CLAUDE.local.md or ~/.claude/tower/rules/ always asks."""
    call = {"tool_name": tool, "tool_input": {"file_path": file_path}}
    assert asks(run_hook(tmp_path, call, push=push, pr="none"))


@pytest.mark.parametrize("push", PUSH_MODES)
@pytest.mark.parametrize("command", RULE_FILE_COMMANDS)
def test_command_naming_rule_file_asks(tmp_path: Path, command: str, push: str) -> None:
    """Any Bash command naming a rule file asks, even one that only reads it."""
    assert asks(run_hook(tmp_path, bash(command), push=push, pr="none"))


@pytest.mark.parametrize("file_path", ["CLAUDE.md", "/repo/myCLAUDE.local.md", "/repo/.claude/settings.json"])
def test_edit_of_other_files_passes(tmp_path: Path, file_path: str) -> None:
    """Edits of files that only look like rule files pass."""
    assert run_hook(tmp_path, {"tool_name": "Edit", "tool_input": {"file_path": file_path}}) is None


@pytest.mark.parametrize(
    "call",
    [
        {"tool_name": "Read", "tool_input": {"file_path": "CLAUDE.local.md"}},
        {"tool_name": "Bash", "tool_input": {}},
        {"tool_name": "Bash"},
        "not json",
    ],
)
def test_passthrough(tmp_path: Path, call: object) -> None:
    """Other tools, Bash calls without a command, and unparseable input pass silently."""
    assert run_hook(tmp_path, call) is None


# Known bypasses, with push_approval and pr_approval both every: each gets no approval prompt today.
# T04 (#21, harden the approval hook) must make them ask. xfail_strict in pyproject.toml fails the suite
# the moment one starts passing, so T04 removes the marker as it fixes each. raises=AssertionError keeps a
# crashing hook from counting as an expected failure.
BYPASSES = [
    pytest.param('git -C "/tmp/my repo" push origin x', id="git -C with a quoted path"),
    pytest.param("gh pr -R owner/repo comment 5 -b hi", id="gh -R after the subcommand"),
    pytest.param("gh pr --repo owner/repo merge 3", id="gh --repo after the subcommand"),
    pytest.param("gh api -XPOST repos/a/b/issues", id="gh api -X attached to its method"),
    pytest.param("echo x > ~/.claude/tower.conf", id="write to tower.conf"),
    pytest.param("git --no-pager push origin x", id="git global option before push"),
    pytest.param("gh api repos/a/b/issues -ftitle=x", id="gh api -f attached to its value"),
]


@pytest.mark.xfail(raises=AssertionError, reason="known bypass, fixed by T04 (#21)")
@pytest.mark.parametrize("command", BYPASSES)
def test_known_bypass_asks(tmp_path: Path, command: str) -> None:
    """Each known bypass should ask; today it doesn't."""
    assert asks(run_hook(tmp_path, bash(command), push="every", pr="every"))


@pytest.mark.xfail(raises=AssertionError, reason="known bypass, fixed by T04 (#21)")
def test_known_bypass_write_tool_on_tower_conf_asks(tmp_path: Path) -> None:
    """A Write of tower.conf could set push_approval=none silently; it should ask like a rule-file edit."""
    call = {"tool_name": "Write", "tool_input": {"file_path": "~/.claude/tower.conf"}}
    assert asks(run_hook(tmp_path, call))


# The hook fails open today: input it doesn't expect makes it crash (exit 1), which Claude Code treats as a
# non-blocking error, so the command runs with no prompt. It should ask instead.
@pytest.mark.xfail(raises=subprocess.CalledProcessError, reason="hook fails open on a crash, fixed by T04 (#21)")
@pytest.mark.parametrize(
    "call",
    [
        pytest.param([], id="JSON that is not an object"),
        pytest.param(
            {"tool_name": "Bash", "tool_input": {"command": ["git", "push"]}}, id="command that is not a string"
        ),
    ],
)
def test_unexpected_input_asks(tmp_path: Path, call: object) -> None:
    """Input the hook can't read should end in a prompt, not a crash that lets the command through."""
    assert asks(run_hook(tmp_path, call))
