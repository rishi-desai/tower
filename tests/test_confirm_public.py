"""Tests for plugin/scripts/confirm-public.py, the PreToolUse hook that forces an approval prompt.

Each test runs the hook the way Claude Code does: a separate `python3 -I` process with the tool call
as JSON on stdin. The hook either prints an "ask" decision or prints nothing (Claude Code's normal
permission rules then decide). HOME is a temporary folder, so the policy file the hook reads is the one a test
writes to <tmp>/.claude/tower/policy.toml, and TOWER_CONF points at a temporary tower.conf.

Most tests run under a profile written the way tower.conf's old push_approval and pr_approval worked (the
`push` and `pr` arguments of run_hook): comments and issue writes ask, PR writes and merges ask unless
pr="none". Cases are grouped by how the hook should treat them there:
  ALWAYS_ASK   - GitHub writes other people see (comments, reviews, issues, releases, labels, gh api writes)
  PR_CHANGES   - gh pr writes a worker needs for its own PR; ask unless pr="none"
  PUSHES       - plain pushes; ask only when push=every
  FORCE        - force pushes, deletes and +refspecs; ask unless push=none
  READS        - read-only commands; never ask
  UNCHECKABLE  - commands the hook can't split or see into; ask in every mode but one where nothing asks
The policy matrix after them covers every key in each built-in profile, chosen by default_profile or by a
repo override, and the safety floor, which asks in every profile.
Each group also holds the same actions written in other ordinary forms: global options before the
subcommand, flags attached to their values, wrappers (bash -c, env, xargs, ...), $( ), heredocs.
HANDOFF.md recorded only the counts of the original manual run, not the commands, so these cases are
rebuilt from the hook's docstring and cover every rule it states.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

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
    # -R/--repo anywhere, attached or not; any flag before the subcommand takes the next word, as in gh itself
    "gh pr -R owner/repo comment 5 -b hi",
    "gh pr comment 5 -b hi -R owner/repo",
    "gh pr --repo=owner/repo review 5 --approve",
    "gh issue -Rowner/repo comment 3 -b x",
    "gh --repo owner/repo issue close 3",
    "gh pr -b hi comment 5",
    # gh api methods and fields: attached, with =, clustered, or before `api`
    "gh api -XPOST repos/a/b/issues",
    "gh api -X=POST repos/a/b/issues",
    "gh api repos/a/b/issues -XPATCH",
    "gh api -X post repos/a/b/issues",
    "gh api --method POST repos/a/b/issues",
    "gh api --method=PUT repos/a/b/x",
    "gh -X POST api repos/a/b/issues",
    "gh api repos/a/b/issues -ftitle=x",
    "gh api repos/a/b/issues -iftitle=x",
    "gh api repos/a/b/issues --field=title=x",
    "gh api repos/a/b/issues --raw-field title=x",
    "gh api graphql --input query.json",
    # wrapped commands
    "bash -c 'gh pr comment 5 -b hi'",
    'sh -c "gh issue close 3"',
    "bash -lc 'gh pr review 5 --approve'",
    "env GH_DEBUG=1 gh issue comment 3 -b x",
    "env -u GH_TOKEN gh issue comment 3 -b x",
    "GH_DEBUG=1 gh pr comment 5 -b x",
    "command gh pr comment 5 -b hi",
    "echo 5 | xargs gh pr comment -b hi",
    "gh issue list -q '.[].number' | xargs -I{} gh issue close {}",
    "eval 'gh pr comment 5 -b hi'",
    "/opt/homebrew/bin/gh pr comment 5 -b hi",
    # inside $( ), backticks, a subshell or a heredoc fed to a shell; after a heredoc or a mid-word #
    "echo $(gh pr comment 5 -b hi)",
    "echo `gh issue close 3`",
    "(cd /tmp && gh pr comment 5 -b hi)",
    "bash <<'EOF'\ngh issue close 3\nEOF",
    "cat > notes.md <<'EOF'\nIt's done\nEOF\ngh pr comment 5 --body-file notes.md\n# that's all",
    "echo a#b; gh pr comment 5 -b hi",
    "cat <<EOF\n$(gh pr comment 5 -b hi)\nEOF",
    # << is a shift in arithmetic, not a heredoc, so the next line still runs; a case pattern's ) closes nothing
    "echo $(( 1 << 2 ))\ngh pr comment 5 -b hi",
    "(( n = 1 << 4 ))\ngh pr comment 5 -b hi",
    "echo $[1<<2]\ngh pr comment 5 -b hi",
    'echo "$(case a in a) gh pr comment 5 -b hi;; esac)"',
    # env -S, gh's built-in aliases, and program names macOS and zsh resolve to gh
    "env -S 'gh pr comment 5 -b hi'",
    "gh issue new -t x -b y",
    "gh release new v1",
    "GH pr comment 5 -b hi",
    '"$HOME/bin/gh" pr comment 5 -b hi',
    "=gh pr comment 5 -b hi",
    "{gh pr comment 5 -b hi}",
    # a case word that is quoted or only an argument isn't the keyword; an outer heredoc waits for its own line
    'echo "$(echo { case)"; gh pr comment 5 -b hi; : "; esac)"',
    'echo "$(\\case)"; gh pr comment 5 -b hi; : "; esac)"',
    "cat <<EOF; echo $(\ngh pr comment 5 -b hi\nEOF\n)",
    # shell code given to other shells and programs, and git/gh text inside another language's code
    "tcsh -c 'gh pr comment 5 -b hi'",
    "csh -c 'gh issue close 3'",
    "ssh host 'gh pr comment 5 -b hi'",
    "watch 'gh pr comment 5 -b hi'",
    "python3 -c \"import os; os.system('gh pr comment 5 -b hi')\"",
    "osascript -e 'do shell script \"gh issue close 3\"'",
    # part of a deciding word, or a GraphQL query, from a variable or command output
    "gh pr comm${x}ent 5 -b hi",
    "gh api -X PO$(echo ST) repos/a/b/issues",
    'gh api graphql -f query="$Q"',
    'gh api graphql -F query="$(cat q.graphql)"',
]
PR_CHANGES = [
    "gh pr create --fill",
    "gh pr edit 5 --base main",
    "gh pr merge 5 --squash --delete-branch",
    # tower merge (stacked PRs need it) and gh api's REST merges are merges too, judged like gh pr merge
    "python3 -I /opt/tower/plugin/scripts/run-tower.py merge 5",
    "tower merge",
    "bash -c 'tower merge 5'",
    "env NO_COLOR=1 tower merge 5",
    "gh api -X PUT repos/a/b/pulls/5/merge-async -f merge_method=squash -f merge_action=direct_merge",
    "gh api --method PUT /repos/a/b/pulls/5/merge -f merge_method=squash",
    "gh pr close 5",
    "gh pr reopen 5",
    "gh pr ready 5",
    "gh pr update-branch 5",
    "gh pr --repo owner/repo merge 3",
    "gh pr -R owner/repo create --fill",
    "gh -R owner/repo pr merge 3 --squash",
    "bash -c 'gh pr merge 5 --squash'",
    "env FOO=1 gh pr create --fill",
    "gh pr new --fill",
]
PUSHES = [
    "git push",
    "git push origin feature",
    "git -C /tmp/repo push origin x",
    "git status && git push -u origin HEAD",
    # git's global options before push, quoted or not, with = or a separate value
    'git -C "/tmp/my repo" push origin x',
    "git -C '/tmp/my repo' push",
    "git -c user.name=x push",
    'git -c "user.name=a b" push origin x',
    "git --git-dir=/tmp/r/.git push",
    "git --git-dir /tmp/r/.git push",
    "git --work-tree=/tmp/r push",
    "git --no-pager push origin x",
    "git -P push",
    "/usr/bin/git push",
    "'git' push",
    # wrapped commands
    "bash -c 'git push origin x'",
    'sh -c "git push"',
    "env GIT_TRACE=1 git push",
    "FOO=1 git push",
    "command git push",
    "echo x | xargs git push origin",
    "nohup git push",
    "timeout 60 git push",
    "eval git push",
    "bash -c 'env FOO=1 bash -c \"git push\"'",
    # newlines, subshells, compound commands and heredoc-fed shells
    "git commit -m wip\ngit push",
    "(cd /tmp && git push)",
    "if true; then git push; fi",
    "bash <<'EOF'\nset -e\ngit push\nEOF",
    "bash -s <<< 'git push'",
    # a subcommand from a variable could be push
    "git $sub origin x",
    "for c in fetch push; do git $c; done",
    "x=$(( 1 << 2 ))\ngit push",
    "GIT push",
    "env -S 'git push origin x'",
    "git pu$(echo sh) origin x",
    "git p$'ush'",
    "/opt/homebrew/opt/git/libexec/git-core/git-push origin x",
    "git -c alias.p=push p origin x",
    "su -c 'git push' bob",
    "tmux new -d 'git push'",
    # a heredoc or here-string feeding a shell's standard input is checked
    "bash <<<'git push' 3<<<'x'",
    "bash <<<'git push' <<<'true'",
    "source /dev/stdin <<< 'git push'",
    "bash /dev/stdin <<< 'git push'",
]
FORCE = [
    "git push --force origin x",
    "git push -f",
    "git push --force-with-lease",
    "git push origin --delete old",
    "git push origin +main",
    "git push origin :old",
    'git -C "/tmp/my repo" push --force',
    "git -c k=v push -f origin x",
    "git --git-dir=/r/.git push origin +main",
    "git push -uf origin x",
    "git push --prune origin 'refs/heads/*:refs/heads/*'",
    "bash -c 'git push --force-with-lease'",
    "env FOO=1 git push origin :old",
    # git accepts any unambiguous prefix of a long option
    "git push origin --del old",
    "git push --mir",
    "git push --forc",
    "git -c remote.origin.mirror=true push",
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
    # quoted text that mentions a write is not a command
    "echo 'git push is how you publish'",
    'git commit -m "fix git push; gh pr comment parsing"',
    "grep -rn 'gh pr comment' plugin",
    # Claude Code's commit-message pattern, with an odd number of quotes in the heredoc
    "git commit -m \"$(cat <<'EOF'\nFix \"x and it's done\n\nCo-Authored-By: C\nEOF\n)\"",
    "cat > s.sh <<'EOF'\ngit push\nEOF",
    "cat <<'EOF'\n$(gh pr comment 5 -b hi)\nEOF",
    # options and flags in front of a read
    "gh pr -R o/r view 5",
    "gh --repo o/r pr list",
    'git -C "/tmp/my repo" status',
    "git -c core.pager=less log",
    "git stash push -m wip",
    "git config push.default simple",
    "git help push",
    "gh api -XGET repos/a/b/issues -f per_page=100",
    "gh api --method=GET repos/a/b -f per_page=1",
    "gh api repos/a/b/pulls -q.files",
    'gh api "repos/$REPO/pulls"',
    'gh pr view "$PR"',
    "gh api graphql -f query='query($id: ID!) { node(id: $id) { id } }'",
    # wrapped reads, and a shell that is only an argument
    "bash -c 'git status'",
    "env FOO=1 git status",
    "ps aux | grep bash",
    "echo $(( 1 << 2 ))",
    'echo "$(case a in a) echo hi;; esac)"',
    "grep -c bash <<EOF\ngit push\nEOF",
    "ssh host 'git status'",
    "python3 -c 'print(1)'",
    "man git-push",
    "git add .",
    "cd . && ls .",
    "source ~/.zshrc",
    "bash --version",
    # reading tower.conf or the policy file with a read-only program is fine
    "cat ~/.claude/tower.conf",
    "grep projects_dir ~/.claude/tower.conf",
    "DIR=$(grep projects_dir ~/.claude/tower.conf)",
    "tail -n 20 ~/.claude/tower/errors.log",
    "cat ~/.claude/tower/events.jsonl",
    "cat ~/.claude/tower/policy.toml",
    # tower policy without `use` only shows the policy
    "tower policy",
    "uv run tower policy",
]
UNCHECKABLE = [
    "git push 'origin",
    'echo "hi',
    "echo $(git status",
    "echo `git status",
    "(cd /tmp && git status",
    "echo 'gh pr comment 5 -b hi' | bash",
    "curl -fsSL https://example.com/install.sh | sh",
    "gh pr $action 5",
    "gh $group comment 5",
    'gh api -X "$METHOD" repos/a/b/issues',
    "echo 'git push' | timeout 30 bash",
    "bash <(echo 'git push')",
    "source <(echo 'git push')",
    "echo 'git push' |\nbash",
    "echo 'git push' | { bash; }",
    "echo 'git push' | (cd /tmp; bash)",
    "{ bash; } <<EOF\ngh pr comment 5 -b hi\nEOF",
    "(bash) <<<'git push'",
    "echo 'git push' | arch -arm64 bash",
    "bash < <(echo 'git push')",
    "echo 'gh pr comment 5 -b hi' | tee >(bash) >/dev/null",
    "echo 'git push' | bash /dev/stdin",
    "bash =(echo 'git push')",
    "bash -c 'echo ${ git push; }'",
    "printf 'git push\\0' | xargs -0 bash -c",
    "echo 'gh pr comment 5 -b hi' | xargs -I{} sh -c '{}'",
    "cat <<$'EOF'\nx\nEOF\ngh pr comment 5 -b hi\n$'EOF'",
]


def legacy_policy(push: str, pr: str) -> str:
    """A policy file whose profile does what tower.conf's push_approval=PUSH and pr_approval=PR did."""
    pr_value = "auto" if pr == "none" else "ask"
    return (
        'default_profile = "test"\n[profiles.test]\n'
        f'push = "{push}"\npr_writes = "{pr_value}"\nmerge_to_main = "{pr_value}"\nmerge_to_branch = "{pr_value}"\n'
    )


def run_hook(
    tmp_path: Path,
    call: object,
    push: str = "every",
    pr: str = "every",
    policy: str | None = None,
    conf: str | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, object] | None:
    """Run the hook on CALL with a policy file holding POLICY (default: the legacy profile for PUSH and PR; "" for
    no file at all) and, if CONF is given, a tower.conf holding it; ENV adds to the hook's environment.

    Returns the hook's decision, or None if it printed nothing.
    """
    policy_file = tmp_path / ".claude" / "tower" / "policy.toml"
    policy_file.parent.mkdir(parents=True, exist_ok=True)
    text = legacy_policy(push, pr) if policy is None else policy
    if text:
        policy_file.write_text(text, encoding="utf-8")
    if conf is not None:
        (tmp_path / "tower.conf").write_text(conf, encoding="utf-8")
    stdin = call if isinstance(call, str) else json.dumps(call)
    proc = subprocess.run(
        [sys.executable, "-I", str(HOOK)],
        input=stdin,
        capture_output=True,
        text=True,
        env={"TOWER_CONF": str(tmp_path / "tower.conf"), "HOME": str(tmp_path), **(env or {})},
        check=True,
        timeout=20,
    )
    if not proc.stdout.strip():
        return None
    decision: dict[str, object] = json.loads(proc.stdout)["hookSpecificOutput"]
    return decision


def load_hook() -> ModuleType:
    """The hook as a module, for tests that call its functions in this process."""
    spec = importlib.util.spec_from_file_location("confirm_public", HOOK)
    assert spec is not None and spec.loader is not None
    hook = importlib.util.module_from_spec(spec)
    sys.modules["confirm_public"] = hook  # dataclasses look their module up here
    spec.loader.exec_module(hook)
    return hook


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
def test_pr_changes_ask_when_pr_writes_and_merges_ask(tmp_path: Path, command: str, push: str) -> None:
    """A worker's own PR changes ask while pr_writes and both merge keys ask."""
    assert asks(run_hook(tmp_path, bash(command), push=push, pr="every"))


@pytest.mark.parametrize("push", PUSH_MODES)
@pytest.mark.parametrize("command", PR_CHANGES)
def test_pr_changes_pass_when_pr_writes_and_merges_are_auto(tmp_path: Path, command: str, push: str) -> None:
    """With pr_writes and both merge keys auto, a worker opens and merges its own PR without a prompt."""
    assert not asks(run_hook(tmp_path, bash(command), push=push, pr="none"))


@pytest.mark.parametrize("push", PUSH_MODES)
@pytest.mark.parametrize("command", PUSHES)
def test_plain_push_asks_only_when_push_is_every(tmp_path: Path, command: str, push: str) -> None:
    """A plain push asks under push=every and passes under force and none."""
    assert asks(run_hook(tmp_path, bash(command), push=push)) == (push == "every")


@pytest.mark.parametrize("push", PUSH_MODES)
@pytest.mark.parametrize("command", FORCE)
def test_force_push_asks_unless_push_is_none(tmp_path: Path, command: str, push: str) -> None:
    """Force pushes, deletes and +refspecs ask under every and force."""
    assert asks(run_hook(tmp_path, bash(command), push=push)) == (push != "none")


@pytest.mark.parametrize("push", PUSH_MODES)
@pytest.mark.parametrize("command", READS)
def test_reads_pass(tmp_path: Path, command: str, push: str) -> None:
    """Read-only commands print nothing, leaving the decision to Claude Code's rules."""
    assert run_hook(tmp_path, bash(command), push=push) is None


@pytest.mark.parametrize("push", PUSH_MODES)
@pytest.mark.parametrize("command", UNCHECKABLE)
def test_commands_that_cant_be_checked_ask(tmp_path: Path, command: str, push: str) -> None:
    """Commands the hook can't split, or can't see into (a shell fed from a pipe or <( ), a gh subcommand or
    method from a variable), ask in every mode."""
    assert asks(run_hook(tmp_path, bash(command), push=push, pr="none"))


@pytest.mark.parametrize(
    ("conf", "command", "expect_ask"),
    [
        ("push_approval=sometimes\n", "git push", True),
        ("push_approval=\n", "git push", True),
        ("pr_approval=whenever\n", "gh pr merge 5", True),
        ("push_approval=none\npr_approval=none\n", "git push --force", False),
        ("push_approval=none\npr_approval=none\n", "gh pr merge 5 --squash", False),
        ("push_approval=none\npr_approval=none\n", "gh pr comment 5 -b hi", True),
        ("push_approval=none\npr_approval=none\n", "gh issue create -t x", True),
        ("push_approval=force\n", "git push", False),
        ("push_approval=force\n", "git push -f", True),
    ],
)
def test_old_tower_conf_approvals_still_apply_through_migration(
    tmp_path: Path, conf: str, command: str, expect_ask: bool
) -> None:
    """With no policy file, the first run migrates tower.conf's push_approval/pr_approval into one that does what
    they did: an unknown value is every, and comments and issue writes still ask."""
    assert asks(run_hook(tmp_path, bash(command), policy="", conf=conf)) == expect_ask
    assert (tmp_path / ".claude" / "tower" / "policy.toml").exists()


def test_missing_config_is_treated_as_the_strictest_profile(tmp_path: Path) -> None:
    """With no policy file and no tower.conf, a push still asks."""
    assert asks(run_hook(tmp_path, bash("git push"), policy=""))


RULE_FILE_EDITS = [
    ("Edit", "CLAUDE.local.md"),
    ("Write", "/repo/worktrees/x/CLAUDE.local.md"),
    ("MultiEdit", "~/.claude/tower/rules/Poot.md"),
    ("Edit", "/Users/someone/.claude/tower/rules/tower.md"),
    ("Write", "~/.claude/tower.conf"),
    ("Edit", "/Users/someone/.claude/tower.conf"),
    ("Write", "~/.claude/tower/policy.toml"),
    ("Write", "~/.claude/TOWER.conf"),
    ("Edit", "/repo/worktrees/x/claude.LOCAL.md"),
    ("Write", "/Users/x/.claude/./tower/rules/a.md"),
    ("Write", "/Users/x/.claude//tower/policy.toml"),
]
RULE_FILE_COMMANDS = [
    "echo 'use pytest' >> CLAUDE.local.md",
    "cat ~/.claude/tower/rules/Poot.md",
    "sed -i '' s/a/b/ ~/.claude/tower/rules/Poot.md",
    "ln -sf ~/.claude/tower/rules/Poot.md CLAUDE.local.md",
    "echo push_approval=none >> ~/.claude/tower.conf",
    "sed -i '' s/every/none/ ~/.claude/tower.conf",
    'cp /tmp/x "$HOME/.claude/tower.conf"',
    "echo x | tee ~/.claude/tower/policy.toml",
    "cd ~/.claude/tower/rules && echo x >> Poot.md",
    "echo x >> ~/.claude//tower/rules/a.md",
    'echo x >> ~/.claude/to""wer/rules/a.md',
    "cat /tmp/x > ~/.claude/tower.conf",
    "echo '{}' >> ~/.claude/tower/events.jsonl",
    "python3 - <<'EOF'\nopen('/Users/x/.claude/tower.conf', 'w').write('push_approval=none')\nEOF",
]


@pytest.mark.parametrize("push", PUSH_MODES)
@pytest.mark.parametrize(("tool", "file_path"), RULE_FILE_EDITS)
def test_edit_of_rule_file_asks(tmp_path: Path, tool: str, file_path: str, push: str) -> None:
    """Edit/Write/MultiEdit of CLAUDE.local.md, anything under ~/.claude/tower/, or tower.conf always asks."""
    call = {"tool_name": tool, "tool_input": {"file_path": file_path}}
    assert asks(run_hook(tmp_path, call, push=push, pr="none"))


@pytest.mark.parametrize("push", PUSH_MODES)
@pytest.mark.parametrize("command", RULE_FILE_COMMANDS)
def test_command_naming_rule_file_asks(tmp_path: Path, command: str, push: str) -> None:
    """A Bash command naming a rule file asks even when it only reads it; one that could write tower.conf or
    another file in ~/.claude/tower asks."""
    assert asks(run_hook(tmp_path, bash(command), push=push, pr="none"))


@pytest.mark.parametrize(
    "file_path",
    [
        "CLAUDE.md",
        "/repo/myCLAUDE.local.md",
        "/repo/.claude/settings.json",
        "/repo/mytower.conf",
        "/repo/tower.conf.example",
    ],
)
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


# T02 (#19) recorded these as bypasses: with push_approval and pr_approval both every, none got an approval
# prompt. T04 (#21) fixed them; they stay as regression tests, and the forms they stand for are covered in every
# push mode in the lists above.
FORMER_BYPASSES = [
    pytest.param('git -C "/tmp/my repo" push origin x', id="git -C with a quoted path"),
    pytest.param("gh pr -R owner/repo comment 5 -b hi", id="gh -R after the subcommand"),
    pytest.param("gh pr --repo owner/repo merge 3", id="gh --repo after the subcommand"),
    pytest.param("gh api -XPOST repos/a/b/issues", id="gh api -X attached to its method"),
    pytest.param("echo x > ~/.claude/tower.conf", id="write to tower.conf"),
    pytest.param("git --no-pager push origin x", id="git global option before push"),
    pytest.param("gh api repos/a/b/issues -ftitle=x", id="gh api -f attached to its value"),
]


@pytest.mark.parametrize("command", FORMER_BYPASSES)
def test_former_bypass_asks(tmp_path: Path, command: str) -> None:
    """Each former bypass asks."""
    assert asks(run_hook(tmp_path, bash(command), push="every", pr="every"))


def test_write_tool_on_tower_conf_asks(tmp_path: Path) -> None:
    """A Write of tower.conf could set push_approval=none silently, so it asks like a rule-file edit."""
    call = {"tool_name": "Write", "tool_input": {"file_path": "~/.claude/tower.conf"}}
    assert asks(run_hook(tmp_path, call))


# The hook used to crash (exit 1) on input it didn't expect, which Claude Code treats as a non-blocking error,
# so the command ran with no prompt. Now it asks.
@pytest.mark.parametrize(
    "call",
    [
        pytest.param([], id="JSON that is not an object"),
        pytest.param(
            {"tool_name": "Bash", "tool_input": {"command": ["git", "push"]}}, id="command that is not a string"
        ),
        pytest.param({"tool_name": "Bash", "tool_input": "git push"}, id="tool_input that is not an object"),
        pytest.param({"tool_name": "Write", "tool_input": {"file_path": ["x"]}}, id="file_path that is not a string"),
    ],
)
def test_unexpected_input_asks(tmp_path: Path, call: object) -> None:
    """Input the hook can't read ends in a prompt, not a crash that lets the command through."""
    assert asks(run_hook(tmp_path, call))


def test_deep_nesting_asks(tmp_path: Path) -> None:
    """$( ) nested past Python's recursion limit asks instead of crashing."""
    assert asks(run_hook(tmp_path, bash("echo " + "$(" * 600 + "x" + ")" * 600)))


def test_crash_asks(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """A crash inside the hook asks instead of letting the command run."""
    hook = load_hook()

    def crash(call: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(hook, "decide", crash)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(bash("git status"))))
    assert hook.main() == 0
    decision = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "ask"
    assert "RuntimeError: boom" in decision["permissionDecisionReason"]


def test_a_policy_file_that_cant_be_read_gives_the_strictest_profile(tmp_path: Path) -> None:
    """A policy file that isn't UTF-8 (or can't be parsed) makes every gate ask, as in work."""
    policy_file = tmp_path / ".claude" / "tower" / "policy.toml"
    policy_file.parent.mkdir(parents=True)
    policy_file.write_bytes(b'default_profile = "\xff"\n')
    decision = run_hook(tmp_path, bash("gh issue create -t x"), policy="")
    assert asks(decision)
    assert decision is not None and "tower policy: work" in str(decision["permissionDecisionReason"])
    assert run_hook(tmp_path, bash("git status"), policy="") is None


def test_command_too_big_to_check_asks_in_time(tmp_path: Path) -> None:
    """A command too big to check within the hook's work budget asks quickly, instead of running past Claude
    Code's hook timeout, which would let it through unchecked."""
    assert asks(run_hook(tmp_path, bash("echo " + "gh api " * 50000)))


# Runs in a child process, so a hang fails the test on its timeout instead of stalling the whole run.
FUZZ_SCRIPT = r"""
import importlib.util, random, sys
spec = importlib.util.spec_from_file_location("confirm_public", sys.argv[1])
hook = importlib.util.module_from_spec(spec)
sys.modules["confirm_public"] = hook  # dataclasses look their module up here
spec.loader.exec_module(hook)
pieces = list(" \t\n;&|()<>'\"\\$`{}#=-+") + [
    "git", "gh", "push", "pr", "comment", "api", "-X", "-R", "bash", "-c", "eval", "xargs",
    "<<EOF", "<<'EOF'", "EOF", "$(", "${", "<(",
]
rng = random.Random(21)
for _ in range(3000):
    text = "".join(rng.choice(pieces) for _ in range(rng.randint(0, 60)))
    checker = hook.CommandChecker(hook.SessionPolicy(hook.STRICTEST_GATES, "fuzz"), "/")
    assert isinstance(checker.reasons(text), list), repr(text)
"""


def test_random_commands_never_crash_or_hang() -> None:
    """Checking never crashes or hangs on random shell syntax (3,000 seeded strings). A crash would only make
    the hook ask, but a hang runs past Claude Code's hook timeout, which lets the command through."""
    subprocess.run([sys.executable, "-I", "-c", FUZZ_SCRIPT, str(HOOK)], check=True, timeout=60)


# --- The policy matrix: every key the hook enforces, in each built-in profile, chosen two ways ------------

# Whether each action asks in work, personal and sandbox, written out from the issue (#26) rather than read from
# the code: work asks for everything; personal asks for force pushes, merges into the default branch (and edits
# that can retarget a PR there) and comments; sandbox asks for nothing. Writes no single key covers (gh api,
# releases, labels) and commands that can't be checked ask unless nothing else does.
RUN_TOWER = "/Users/x/.claude/plugins/tower/plugin/scripts/run-tower.py"
T = f"python3 -I {RUN_TOWER}"
M = MERGE_ASYNC = "repos/acme/widget/pulls/5/merge-async"
ASKS = (True, True, False)  # asks in work and personal, not sandbox
POLICY_MATRIX: list[tuple[str, str, str | tuple[str, str] | None, bool, bool, bool]] = [
    # (id, command, base gh reports for a merge (with its stack's base for a stacked PR) or None, asks in work,
    # personal, sandbox)
    ("push: plain", "git push origin feat", None, True, False, False),
    ("push: force", "git push --force origin feat", None, True, True, False),
    ("push: delete", "git push origin --delete old", None, True, True, False),
    ("comments: PR comment", "gh pr comment 5 -b hi", None, True, True, False),
    ("comments: review", "gh pr review 5 --approve", None, True, True, False),
    ("comments: issue comment", "gh issue comment 3 -b hi", None, True, True, False),
    ("issue_writes: create", "gh issue create -t x -b y", None, True, False, False),
    ("issue_writes: close", "gh issue close 3", None, True, False, False),
    ("pr_writes: create", "gh pr create --fill", None, True, False, False),
    ("pr_writes: ready", "gh pr ready 5", None, True, False, False),
    ("pr_writes + merge_to_main: edit --base", "gh pr edit 5 --base main", None, True, True, False),
    ("merge_to_main", "gh pr merge 5 --squash --delete-branch", "main", True, True, False),
    ("merge_to_branch", "gh pr merge 5 --squash --delete-branch", "integration/a-plus", True, False, False),
    ("merge_to_branch: current branch's PR", "gh pr merge -sd", "stack/base", True, False, False),
    ("merge: gh can't say", "gh pr merge 5 --squash", "", True, True, False),
    ("merge: not the whole command", "gh pr checks 5 && gh pr merge 5 --squash", "feat-base", True, True, False),
    ("merge: an unknown flag", "gh pr merge 5 --squash --frobnicate", "feat-base", True, True, False),
    ("merge: PR from a variable", 'gh pr merge "$PR" --squash', "feat-base", True, True, False),
    # tower merge, the workers' merge path (T = python3 -I <plugin>/scripts/run-tower.py), is judged like gh pr merge
    ("merge_to_main: tower merge", f"{T} merge 5", "main", True, True, False),
    ("merge_to_branch: tower merge", f"{T} merge 5", "integration/a-plus", True, False, False),
    ("merge_to_branch: tower merge, this branch's PR", "tower merge", "integration/a-plus", True, False, False),
    ("merge: tower merge not the whole command", f"git fetch && {T} merge 5", "feat-base", True, True, False),
    ("merge: tower merge through another launcher", "uv run tower merge 5", "feat-base", True, True, False),
    ("merge: tower merge of a PR from a variable", f'{T} merge "$PR"', "feat-base", True, True, False),
    ("merge: tower with a subcommand from a variable", 'tower "$CMD" 5', "feat-base", True, True, False),
    ("merge: tower merge in python code", "python3 -c 'from tower import cli; cli.main([\"merge\"])'", "x", *ASKS),
    # a stacked PR lands on its stack's base (GitHub merges every PR below it too), whatever its own base says
    ("merge_to_main: a stacked PR", f"{T} merge 5", ("feat1", "main"), True, True, False),
    ("merge_to_main: gh pr merge of a stacked PR", "gh pr merge 5 --auto -s", ("feat1", "main"), True, True, False),
    ("merge_to_branch: a stack on another branch", "tower merge 5", ("feat1", "release"), True, False, False),
    # gh api's merge endpoints (M = repos/acme/widget/pulls/5/merge-async): merge-async and the synchronous merge
    ("merge_to_main: gh api merge-async", f"gh api -X PUT {M} -f merge_method=squash", "main", True, True, False),
    ("merge_to_branch: gh api merge-async", f"gh api -X PUT {M} -f merge_method=rebase", "dev", True, False, False),
    ("merge_to_main: gh api merge-async, stacked", f"gh api -XPUT {M}", ("feat1", "main"), True, True, False),
    ("merge_to_branch: gh api merge", "gh api --method=put repos/acme/widget/pulls/5/merge", "dev", True, False, False),
    ("merge_to_branch: gh api merge, fields (POST)", f"gh api {M} -f merge_method=squash", "dev", True, False, False),
    ("merge: gh api merge, gh's placeholders", "gh api -X PUT repos/{owner}/{repo}/pulls/5/merge", "x", *ASKS),
    ("merge: gh api merge on another host", f"gh api --hostname ghe.example.com -X PUT {M}", "x", True, True, False),
    ("unchecked: gh api merge, PR from a variable", 'gh api -X PUT "repos/acme/widget/pulls/$N/merge"', None, *ASKS),
    ("reads never ask: a merge-async's status", f"gh api {M}/0b5e-uuid", None, False, False, False),
    ("no key: gh api write", "gh api -X POST repos/acme/widget/issues -f title=x", None, True, True, False),
    ("no key: release", "gh release create v1.0.0", None, True, True, False),
    ("no key: label", "gh label create triage", None, True, True, False),
    ("no key: gh subcommand from a variable", "gh pr $action 5", None, True, True, False),
    ("unchecked: a shell reading a pipe", "echo 'git push' | bash", None, True, True, False),
    ("unchecked: an unclosed quote", "git push 'origin", None, True, True, False),
    ("reads never ask", "gh pr view 5 && git status", None, False, False, False),
]
PROFILES = ["work", "personal", "sandbox"]
UNCHECKED_IN_MATRIX = {command for case, command, *_ in POLICY_MATRIX if case.startswith("unchecked")}
FAKE_GH = """#!{python}
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_GH_LOG"], "a") as log:
    log.write(json.dumps(args) + "\\n")
base, stack = os.environ.get("FAKE_GH_BASE"), os.environ.get("FAKE_GH_STACK") or None
if args[:2] == ["pr", "view"] and base:
    print("https://github.com/acme/widget/pull/5")
elif args[:4] == ["api", "--hostname", "github.com", "repos/acme/widget/pulls/5"] and base:
    print(json.dumps([base, "main", stack]))
else:
    sys.exit(1)
"""


@pytest.fixture
def widget(tmp_path: Path) -> Path:
    """A checkout of acme/widget (its origin), with a fake gh on PATH that logs its arguments; returns its path."""
    checkout = tmp_path / "widget"
    checkout.mkdir()
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    subprocess.run(
        ["git", "-C", str(checkout), "remote", "add", "origin", "git@github.com:Acme/Widget.git"], check=True
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH.format(python=sys.executable), encoding="utf-8")
    gh.chmod(0o755)
    return checkout


def in_widget(widget: Path, command: str) -> dict[str, object]:
    """A Bash tool call from a session working in the widget checkout."""
    return {**bash(command), "cwd": str(widget)}


def widget_env(widget: Path, base: str | tuple[str, str] | None) -> dict[str, str]:
    """The hook's environment for the widget fixture: the fake gh first on PATH, reporting BASE for a PR, given as
    (its base, its stack's base) for a stacked PR."""
    base, stack = base if isinstance(base, tuple) else (base, None)
    return {
        "PATH": f"{widget.parent / 'bin'}{os.pathsep}/usr/bin{os.pathsep}/bin",
        "FAKE_GH_LOG": str(widget.parent / "gh.log"),
        "FAKE_GH_BASE": base or "",
        "FAKE_GH_STACK": stack or "",
    }


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize(
    ("command", "base", "expected"),
    [pytest.param(command, base, dict(zip(PROFILES, row)), id=case) for case, command, base, *row in POLICY_MATRIX],
)
def test_each_key_in_each_profile_chosen_by_default_profile(
    widget: Path, profile: str, command: str, base: str | tuple[str, str] | None, expected: dict[str, bool]
) -> None:
    policy = f'default_profile = "{profile}"\n'
    decision = run_hook(widget.parent, in_widget(widget, command), policy=policy, env=widget_env(widget, base))
    assert asks(decision) == expected[profile]


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize(
    ("command", "base", "expected"),
    [pytest.param(command, base, dict(zip(PROFILES, row)), id=case) for case, command, base, *row in POLICY_MATRIX],
)
def test_each_key_in_each_profile_chosen_by_a_repo_override(
    widget: Path, profile: str, command: str, base: str | tuple[str, str] | None, expected: dict[str, bool]
) -> None:
    """The session's repo (acme/widget, from its origin) picks the profile over a different default_profile. A
    command that can't be checked asks even in sandbox here: it could reach a repo the policy makes stricter."""
    default = "work" if profile == "sandbox" else "sandbox"
    policy = f'default_profile = "{default}"\n\n[repos]\n"acme/*" = "personal"\n"acme/widget" = "{profile}"\n'
    decision = run_hook(widget.parent, in_widget(widget, command), policy=policy, env=widget_env(widget, base))
    assert asks(decision) == (expected[profile] or command in UNCHECKED_IN_MATRIX)


@pytest.mark.parametrize(
    ("table", "command", "expect_ask"),
    [
        ('[profiles.personal]\ncomments = "auto"', "gh pr comment 5 -b hi", False),
        ('[profiles.personal]\nissue_writes = "ask"', "gh issue create -t x", True),
        ('[profiles.personal]\npush = "none"', "git push --force", False),
        ('[profiles.personal]\npush = "every"', "git push", True),
        ('[profiles.personal]\nmerge_to_main = "auto"', "gh pr merge 5 --squash", False),
        ('[profiles.personal]\nmerge_to_branch = "ask"', "gh pr merge 5 --squash", True),
        ('[profiles.personal]\npr_writes = "ask"', "gh pr create --fill", True),
        ('[profiles.personal]\npush = "sometimes"', "git push", True),  # invalid: the strictest
    ],
)
def test_a_profile_table_changes_what_its_keys_let_through(
    widget: Path, table: str, command: str, expect_ask: bool
) -> None:
    policy = f'default_profile = "personal"\n\n{table}\n'
    decision = run_hook(widget.parent, in_widget(widget, command), policy=policy, env=widget_env(widget, "main"))
    assert asks(decision) == expect_ask


def test_a_merge_asks_gh_about_the_same_pr_and_repo_it_merges(widget: Path) -> None:
    policy = 'default_profile = "personal"\n'
    command = "gh pr merge 7 -R acme/widget --squash"
    assert not asks(run_hook(widget.parent, in_widget(widget, command), policy=policy, env=widget_env(widget, "dev")))

    calls = [json.loads(line) for line in (widget.parent / "gh.log").read_text(encoding="utf-8").splitlines()]
    assert calls[0][:2] == ["pr", "view"] and calls[0][-3:] == ["--repo=acme/widget", "--", "7"]
    assert calls[1][:4] == ["api", "--hostname", "github.com", "repos/acme/widget/pulls/5"]  # the PR gh found


@pytest.mark.parametrize(
    ("command", "view_args"),
    [
        (f"python3 -I {RUN_TOWER} merge 7", ["--", "7"]),
        # a worktree path holding "tower" and "merge" is still a plain path, not interpreter code
        ("python3 -I /code/tower/worktrees/fix-stacked-pr-merge/plugin/scripts/run-tower.py merge 7", ["--", "7"]),
        ("tower merge", []),
        ("gh api -X PUT repos/Acme/Widget/pulls/7/merge-async", ["--repo=Acme/Widget", "--", "7"]),
    ],
)
def test_tower_merge_and_gh_api_merges_ask_gh_about_the_pr_they_merge(
    widget: Path, command: str, view_args: list[str]
) -> None:
    policy = 'default_profile = "personal"\n'
    assert not asks(run_hook(widget.parent, in_widget(widget, command), policy=policy, env=widget_env(widget, "dev")))

    calls = [json.loads(line) for line in (widget.parent / "gh.log").read_text(encoding="utf-8").splitlines()]
    assert calls[0] == ["pr", "view", "--json", "url", "--jq", ".url", *view_args]


@pytest.mark.parametrize(
    "command", [f"python3 -I {RUN_TOWER} merge 5", "gh pr merge 5 --squash", f"gh api -X PUT {MERGE_ASYNC}"]
)
def test_the_prompt_for_a_stacked_merge_says_it_lands_on_the_stacks_base(widget: Path, command: str) -> None:
    decision = run_hook(
        widget.parent,
        in_widget(widget, command),
        policy='default_profile = "personal"\n',
        env=widget_env(widget, ("feat1", "main")),
    )
    assert decision is not None
    assert "onto its stack's base, the default branch (merge_to_main=ask)" in str(decision["permissionDecisionReason"])


@pytest.mark.parametrize(
    "command",
    [
        "gh pr merge https://github.com/acme/other/pull/5 --squash",
        "gh pr merge --frobnicate https://github.com/acme/other/pull/5",
        "gh pr comment https://github.com/acme/other/pull/5 -b hi",
        "gh issue close https://github.com/acme/other/issues/3",
        "gh api -X POST repos/acme/other/issues -f title=x",
        "gh api -X POST /repos/Acme/Other/issues/3/comments -f body=x",
    ],
)
def test_a_pr_url_in_a_stricter_repo_is_judged_by_that_repo(widget: Path, command: str) -> None:
    """In a sandbox session, a PR, issue or endpoint in a repo the policy says is work asks, wherever it's named."""
    policy = 'default_profile = "sandbox"\n[repos]\n"acme/other" = "work"\n'
    assert asks(run_hook(widget.parent, in_widget(widget, command), policy=policy, env=widget_env(widget, "dev")))


def test_no_gh_lookup_when_both_merge_keys_agree(widget: Path) -> None:
    for profile in ("work", "sandbox"):
        run_hook(widget.parent, in_widget(widget, "gh pr merge 5"), policy=f'default_profile = "{profile}"\n',
                 env=widget_env(widget, "main"))  # fmt: skip
    assert not (widget.parent / "gh.log").exists()


@pytest.mark.parametrize(
    ("command", "expect_ask"),
    [
        ("gh pr comment 5 -b hi", False),  # the session's repo: sandbox
        ("gh pr comment 5 -b hi -R acme/widget", False),
        ("gh -R acme/other pr comment 5 -b hi", True),  # another repo: the stricter of sandbox and work
        ("gh pr comment 5 -b hi --repo=github.com/acme/other", True),
        ('gh pr comment 5 -b hi -R "$REPO"', True),  # a repo from a variable: the strictest
        ("gh pr merge https://github.com/acme/other/pull/5 --squash", True),
        ("gh pr merge https://github.com/acme/widget/pull/5 --squash", False),
        ('gh pr merge https://github.com/acme/widget/pull/5 -R "$X"', True),
        ("gh api -X POST repos/{owner}/{repo}/issues -f title=x", False),  # gh's placeholders: this repo
        ("gh api -X POST repos/acme/widget/issues -f title=x", False),
    ],
)
def test_a_gh_command_naming_another_repo_gets_the_stricter_policy(
    widget: Path, command: str, expect_ask: bool
) -> None:
    policy = 'default_profile = "work"\n[repos]\n"acme/widget" = "sandbox"\n'
    decision = run_hook(widget.parent, in_widget(widget, command), policy=policy, env=widget_env(widget, "dev"))
    assert asks(decision) == expect_ask


# The safety floor: in every profile, sandbox included, an agent can't change tower's policy, tower.conf or rule
# files without a prompt.
FLOOR_EDITS = [
    ("Edit", "~/.claude/tower/policy.toml"),
    ("Write", "/Users/someone/.claude/tower/policy.toml"),
    ("Write", "~/.claude/tower.conf"),
    ("MultiEdit", "~/.claude/tower/rules/Poot.md"),
    ("Write", "/repo/worktrees/x/CLAUDE.local.md"),
]
FLOOR_COMMANDS = [
    "echo 'default_profile = \"sandbox\"' > ~/.claude/tower/policy.toml",
    "sed -i '' s/work/sandbox/ ~/.claude/tower/policy.toml",
    "rm ~/.claude/tower/policy.toml",
    "echo push_approval=none >> ~/.claude/tower.conf",
    "echo 'use pytest' >> CLAUDE.local.md",
    "tower policy use sandbox",
    "uv run tower policy use sandbox --repo",
    "python3 -m tower policy use sandbox",
    "python3 -I ~/.claude/plugins/cache/tower/tower/0.10.0/scripts/run-tower.py policy use sandbox",
    "tower policy $verb sandbox",
    "bash -c 'tower policy use sandbox'",
    "./setup.sh --profile sandbox",
    "bash setup.sh --projects-dir ~/v --profile=sandbox",
    # can't be checked, and would be let through in sandbox, but names the policy or a rule
    "echo 'tower policy use sandbox' | bash",
    "echo 'echo x >> ~/.claude/tower/policy.toml' | sh",
    "cat x > ~/.claude/tower/policy.toml 'unclosed",
]


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize(("tool", "file_path"), FLOOR_EDITS)
def test_the_floor_asks_before_an_edit_of_policy_or_rules_in_every_profile(
    tmp_path: Path, profile: str, tool: str, file_path: str
) -> None:
    call = {"tool_name": tool, "tool_input": {"file_path": file_path}}
    assert asks(run_hook(tmp_path, call, policy=f'default_profile = "{profile}"\n'))


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("command", FLOOR_COMMANDS)
def test_the_floor_asks_before_a_command_that_changes_policy_or_rules_in_every_profile(
    tmp_path: Path, profile: str, command: str
) -> None:
    assert asks(run_hook(tmp_path, bash(command), policy=f'default_profile = "{profile}"\n'))


@pytest.mark.parametrize(
    "command",
    ["cat ~/.claude/tower/policy.toml", "tower policy", "grep wip ~/.claude/tower.conf", "./setup.sh --dry-run"],
)
def test_sandbox_asks_nothing_else(tmp_path: Path, command: str) -> None:
    """In sandbox, reading tower's settings, and everything outside the floor, needs no approval."""
    assert run_hook(tmp_path, bash(command), policy='default_profile = "sandbox"\n') is None


def test_the_prompt_names_the_profile(tmp_path: Path) -> None:
    decision = run_hook(tmp_path, bash("gh pr comment 5 -b hi"), policy='default_profile = "personal"\n')
    assert decision is not None
    reason = str(decision["permissionDecisionReason"])
    assert reason.startswith("Public action or rule change (tower policy: personal) — approve? ")
    assert "comments=ask" in reason


# --- Which repo a command reaches: an agent can't swap its repo's identity to get a looser profile ----------

SANDBOX_WIDGET = 'default_profile = "work"\n[repos]\n"acme/widget" = "sandbox"\n'


@pytest.fixture
def real(widget: Path) -> Path:
    """A second checkout beside widget, of acme/real, which the policy leaves at the default (work)."""
    checkout = widget.parent / "real"
    checkout.mkdir()
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    subprocess.run(["git", "-C", str(checkout), "remote", "add", "origin", "git@github.com:acme/real.git"], check=True)
    return checkout


def sandbox_widget_asks(widget: Path, command: str) -> bool:
    """Whether COMMAND asks in a session in widget, which the policy makes sandbox (default work)."""
    decision = run_hook(widget.parent, in_widget(widget, command), policy=SANDBOX_WIDGET, env=widget_env(widget, None))
    return asks(decision)


@pytest.mark.parametrize(
    "remote_change",
    [
        ["remote", "set-url", "--push", "origin", "git@github.com:acme/real.git"],
        ["remote", "add", "upstream", "https://github.com/acme/real"],
        ["config", "url.git@github.com:acme/real.git.pushInsteadOf", "git@github.com:Acme/Widget.git"],
    ],
)
def test_every_remote_counts_so_repointing_one_only_tightens(widget: Path, remote_change: list[str]) -> None:
    """A push URL, another remote or a rewrite pointing at a stricter repo makes the stricter profile apply."""
    assert not sandbox_widget_asks(widget, "git push --force origin HEAD:main")
    subprocess.run(["git", "-C", str(widget), *remote_change], check=True)
    assert sandbox_widget_asks(widget, "git push --force origin HEAD:main")
    assert sandbox_widget_asks(widget, "gh pr comment 5 -b hi")


@pytest.mark.parametrize(
    ("command", "expect_ask"),
    [
        ("git push --force origin HEAD:main", False),
        ("git push --force git@github.com:acme/real.git HEAD:main", True),
        ("git push -f https://github.com/acme/real HEAD:main", True),
        ("git push --force --repo=github.com:acme/real.git", True),
        ('git push --force "$URL" HEAD:main', True),
        ("git -c remote.origin.pushurl=git@github.com:acme/real.git push --force", True),
        ("git -C ../real push --force", True),
        ("git --git-dir=../real/.git push --force", True),
        ("cd ../real && git push --force", True),
        ("cd ../real; bash -c 'git push --force'", True),
        ('cd "$DIR" && git push --force', True),
        ("cd - && git push --force", True),
        ("cd . && git push --force", False),
        ("GIT_DIR=../real/.git git push --force", True),
        ("GH_REPO=acme/real gh pr comment 5 -b hi", True),
        ("export GH_REPO=acme/real; gh issue create -t x", True),
    ],
)
def test_a_push_or_gh_command_reaching_another_repo_gets_its_policy(
    widget: Path, real: Path, command: str, expect_ask: bool
) -> None:
    assert sandbox_widget_asks(widget, command) == expect_ask


# Ways around a text-matching floor that the review of T09 found; each must ask in every profile.
FLOOR_BYPASSES = [
    "cd ~/.claude && printf 'default_profile = \"sandbox\"' > tower/policy.toml",
    "find ~/.claude -name policy.toml -exec sed -i '' s/work/sandbox/ {} +",
    "printf 'push_approval=none' > ~/.claude/$'\\x74'ower.conf",
    'cp /tmp/x "$HOME/.claude/$NAME"',
    "python3 -c \"from pathlib import Path; (Path.home()/'.claude'/'tower'/'policy.toml').write_text('x')\"",
    "uv run python -c 'from tower import policy; policy.use(\"sandbox\")'",
    "node -e \"require('child_process').execSync('tower policy use sandbox')\"",
]


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("command", FLOOR_BYPASSES)
def test_the_floor_holds_against_known_ways_around_it(tmp_path: Path, profile: str, command: str) -> None:
    assert asks(run_hook(tmp_path, bash(command), policy=f'default_profile = "{profile}"\n'))


def test_the_floor_follows_a_symlink_to_tower_files(tmp_path: Path) -> None:
    """An Edit through a link to ~/.claude is an edit of the policy file."""
    (tmp_path / ".claude" / "tower").mkdir(parents=True)
    (tmp_path / "link").symlink_to(tmp_path / ".claude")
    call = {"tool_name": "Edit", "tool_input": {"file_path": str(tmp_path / "link" / "tower" / "policy.toml")}}
    assert asks(run_hook(tmp_path, call, policy='default_profile = "sandbox"\n'))


# --- Regressions from the /code-review xhigh of T09: each was a way to reach a work repo without a prompt -----

FORK_POLICY = 'default_profile = "personal"\n[repos]\n"acme/*" = "work"\n'


@pytest.fixture
def fork(tmp_path: Path) -> Path:
    """A session checkout of me/fork, beside a checkout of acme/secret (work) in a folder named acme."""
    for name, url in (("fork", "git@github.com:me/fork.git"), ("acme", "git@github.com:acme/secret.git")):
        checkout = tmp_path / name
        checkout.mkdir()
        subprocess.run(["git", "init", "-q", str(checkout)], check=True)
        subprocess.run(["git", "-C", str(checkout), "remote", "add", "origin", url], check=True)
    return tmp_path / "fork"


def fork_decision(fork: Path, command: str, policy: str = FORK_POLICY) -> dict[str, object] | None:
    """The hook's decision on COMMAND from a session in the fork checkout."""
    env = {"PATH": f"/usr/bin{os.pathsep}/bin"}
    return run_hook(fork.parent, {**bash(command), "cwd": str(fork)}, policy=policy, env=env)


@pytest.mark.parametrize(
    "command",
    [
        "git push git@github.com:acme/secret.git HEAD:main",  # an scp-form URL (finding 1)
        "git push github.com:acme/secret HEAD:main",
        "cd .. && cd acme && git push origin main",  # cds taken in order (finding 2)
        "git -C .. -C acme push origin main",
        "git clone https://github.com/acme/secret /tmp/not-there-yet && cd /tmp/not-there-yet && git push",
        "gh pr create -dR acme/secret --fill",  # -R inside a cluster of short flags (finding 3)
        "git push --repo https://github.com/acme/secret.git HEAD:main",  # --repo with its value apart (finding 4)
        "git -c alias.p=push p https://github.com/acme/secret.git main",  # an alias's push URL (finding 5)
        "git -c url.git@github.com:acme/secret.git.insteadOf=git@github.com:me/fork.git push origin main",  # finding 6
        "gh api https://api.github.com/repos/acme/secret/issues -f title=x",  # an API URL (finding 9)
    ],
)
def test_a_push_or_write_reaching_a_work_repo_asks_from_a_personal_repo(fork: Path, command: str) -> None:
    assert asks(fork_decision(fork, command))


def test_a_plain_push_from_the_personal_repo_itself_passes(fork: Path) -> None:
    assert fork_decision(fork, "git push origin main") is None
    assert fork_decision(fork, "cd . && git push origin main") is None


def test_an_api_endpoint_with_a_query_still_names_its_repo(fork: Path) -> None:
    policy = 'default_profile = "sandbox"\n[repos]\n"acme/secret" = "work"\n'
    assert asks(fork_decision(fork, "gh api 'repos/acme/secret/issues?x=1' -f title=x", policy))


def test_in_sandbox_a_command_that_cant_be_checked_asks_if_any_repo_is_stricter(fork: Path) -> None:
    """It could push anywhere (finding 7); with no stricter profile in the file, it passes as before."""
    command = "git push https://github.com/acme/secret.git main; cat <<$E"
    assert asks(fork_decision(fork, command, 'default_profile = "sandbox"\n[repos]\n"acme/*" = "work"\n'))
    assert fork_decision(fork, command, 'default_profile = "sandbox"\n') is None


def test_many_folders_give_the_strictest_quickly(fork: Path) -> None:
    """A line with more distinct cd targets than the hook looks up is judged by the strictest profile (finding 8)."""
    command = "".join(f"cd d{n}; " for n in range(2000)) + "gh pr create --fill"
    assert asks(fork_decision(fork, command))


@pytest.mark.parametrize(
    ("command", "cwd_holds_tower", "expect_ask"),
    [
        ("printf '/p\\n3\\nsandbox\\n' | script -q /dev/null ./setup.sh --no-plugin", True, True),  # finding 10
        ("~/Developer/personal/tower/setup.sh --projects-dir ~/v", False, True),
        ("./setup.sh --dry-run", True, False),
        ("./setup.sh", False, False),  # some other project's setup script
    ],
)
def test_setup_sh_of_this_project_is_behind_the_floor_however_its_answers_come(
    tmp_path: Path, command: str, cwd_holds_tower: bool, expect_ask: bool
) -> None:
    cwd = tmp_path / ("tower" if cwd_holds_tower else "poot")
    cwd.mkdir()
    decision = run_hook(tmp_path, {**bash(command), "cwd": str(cwd)}, policy='default_profile = "sandbox"\n')
    assert asks(decision) == expect_ask


def test_migrated_pr_approval_none_still_asks_before_locking_a_pr(tmp_path: Path) -> None:
    """pr_approval=none never let gh pr lock/unlock through, so the migrated profile doesn't either (finding 12)."""
    conf = "push_approval=force\npr_approval=none\n"
    assert asks(run_hook(tmp_path, bash("gh pr lock 5"), policy="", conf=conf))
    assert run_hook(tmp_path, bash("gh pr merge 5 --squash"), policy="", conf=conf) is None


@pytest.mark.parametrize(
    "command",
    [
        "python3 -m pytest /Users/r/tower/worktrees/feat-26-policy-profiles/tests/test_state.py",  # finding 14
        "python3 -c 'print(\"tower and policy are words\")'",
    ],
)
def test_interpreter_code_that_only_mentions_tower_and_policy_passes(tmp_path: Path, command: str) -> None:
    assert run_hook(tmp_path, bash(command), policy='default_profile = "sandbox"\n') is None


def test_the_prompt_names_a_stricter_profile_reached_in_another_repo(fork: Path) -> None:
    """The label says which policy asked when it isn't the session's (finding 15)."""
    decision = fork_decision(fork, "git push https://github.com/acme/secret.git main")
    assert decision is not None
    assert str(decision["permissionDecisionReason"]).startswith(
        "Public action or rule change (tower policy: personal; it reaches another repo: personal+work) — approve? "
    )
