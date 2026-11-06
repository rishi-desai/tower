#!/usr/bin/env python3
"""confirm-public.py: Claude Code PreToolUse hook. Forces an approval prompt before public GitHub actions.

Claude Code runs this before every Bash command and sends the call as JSON on stdin. If the command
would post or change something other people can see, we print a JSON "ask" decision, and Claude Code
shows the user a confirmation prompt, even when an allow rule like Bash(git *) would normally skip it.

What asks is set by tower's policy for the session's repo (src/tower/policy.py, read through the plugin's
lib/tower link to tower's core; `tower policy` shows it). Each action is judged by one key of it:
  push             git push: every -> every push asks; force -> only force pushes, deletions, --prune and
                   +refspecs; none -> no push asks
  comments         gh pr comment, gh pr review, gh issue comment
  issue_writes     gh issue create, edit, close, reopen, delete, lock, pin, transfer, develop, ...
  pr_writes        gh pr create, edit, ready, close, reopen, update-branch, revert (an edit that changes
                   the base, -B/--base, also needs merge_to_main: it can retarget an armed auto-merge at main)
  merge_to_main /  gh pr merge, `tower merge` (the workers' merge path, src/tower/merge.py, whose own gh calls this
  merge_to_branch  hook can't see) and a gh api write to a PR's merge or merge-async endpoint, by the branch the PR
                   lands on: the repo's default branch, or any other. A PR lands on its base, but a PR in a GitHub
                   stack lands on the stack's base, since merging it merges every PR below it too. The hook asks
                   gh (`gh pr view` for the PR, then `gh api` for its base, its stack and the default branch) only
                   when the two keys differ, and only when the merge is the whole command line (one simple command,
                   nothing before it but python and its switches for tower merge); otherwise, or if gh can't say,
                   it asks unless both keys are auto.
  cleanup          `tower clean` (src/tower/clean.py, which removes worktrees and deletes branches here and on
                   GitHub itself), however tower is run, in interpreter code too; never `tower clean --dry-run`,
                   which only checks (the exact flag, before any --, with no word from a variable before it)
The rest ask unless every GitHub key above is auto (as in the sandbox profile), since no one key covers
them: every other gh command that isn't a known read (GH_READS: view, list, status, diff, checks and the like, plus
local work such as clone and checkout), so gh release, label, repo, gist, secret, variable, workflow, run and cache
writes, an extension or a gh alias, and anything a newer gh adds; gh pr lock/unlock; other `gh api` calls that write
(GraphQL mutations or queries read from a file, REST with a write method or with fields, since gh defaults to POST
when fields are given); and a gh subcommand taken from a variable.

Which repo's policy: the session's repo (the hook's `cwd`), judged by every remote it has, fetch and push
URLs alike (the stricter profile of each, so re-pointing a remote can only tighten it). An action that
reaches another repo is judged by the stricter of that repo's policy and the session's, key by key: a gh
command naming one (-R/--repo, a PR or issue URL, a gh api repos/OWNER/REPO endpoint), a git push to a URL
or with -c remote.*/url.* settings, git -C/--git-dir/--work-tree, and every folder the line cds or pushds
into. A repo from a variable or command output, a line naming GH_REPO or GH_HOST (for gh) or GIT_DIR or
GIT_WORK_TREE (for git push), and `cd -` get the strictest profile. If tower's core can't be loaded, the
built-in strictest profile (work: everything asks) applies.

Safety floor, in every profile: any Edit/Write/MultiEdit of a CLAUDE.local.md, a tower.conf, a file
under ~/.claude/tower/ (the rules, the policy file, the event log) or a Claude Code settings file
(.claude/settings.json or settings.local.json, the user's or a project's), by its path or one a symlink
resolves to, always asks. So does any Bash command naming a rule file (CLAUDE.local.md, ~/.claude/tower/rules/),
running `tower policy use` (however tower is run, tower's policy module used in python/node code
included) or tower's setup.sh (other than --dry-run), which rewrites tower.conf and can set the profile, or
turning tower's plugin off: `claude plugin disable|uninstall|remove` or `claude plugin marketplace remove|rm`
naming tower, with a name from a variable or none, disable --all, or any name xargs, parallel or find may add. A
Bash command naming tower.conf, a policy.toml, another file in ~/.claude/tower, a Claude Code settings file or a
.claude folder itself (cp settings.json ~/.claude/) asks unless it only reads it with a program like cat, grep,
tail or jq, and so does one writing to a path under .claude that comes from a variable or $'...' quoting. Rule
files are standing instructions loaded into every session, tower.conf and the policy file set the approvals above,
and the plugin and Claude Code's settings (enabledPlugins, disableAllHooks, hooks) decide whether this hook runs
at all, so a prompt injection must not be able to change them silently: an agent can't loosen its own rules.

How a command is read: it is split into simple commands roughly the way bash or zsh splits it: on
&& || ; | & and newlines and around ( ), respecting quotes, escapes, comments, heredocs, case
statements and arithmetic. Commands inside $( ), backticks and <( ) are checked too, and so is the code
given to `bash -c` (or sh, zsh, csh, ...), `env -S`, eval, ssh/su/watch/tmux/screen/script, or a shell
through a heredoc. In each simple command, every word naming git or gh (by any path, in any letter
case) starts an invocation to check, so wrappers like env, command, xargs, nohup, sudo and timeout need
no special case; so does every word naming tower (or the plugin's run-tower.py) followed by `merge` or `clean`.
git's global options (-C, -c, --git-dir, ...) are skipped to find its subcommand, and gh's command
words are found the way gh itself finds them, so -R/--repo can come anywhere. In code for
python, node, perl, ruby or osascript, the `git ...`/`gh ...` text inside it is checked, and inline code (-c,
-e, --eval) that names tower and merge (or clean), in the code or the arguments after it, counts as a tower merge
(or clean).

When unsure, we ask: a command that can't be split (an unclosed quote, $( or backtick, a heredoc
delimiter with $, a ${ cmd; } substitution), a shell reading code it can't see (from a pipe, <( ), a
redirect or xargs), a git/gh subcommand, gh api method or GraphQL query taken from a variable or command
output, and a command too big to check quickly. Those ask unless the policy lets every push and GitHub
write through (sandbox), and even then they ask when their text names tower.conf, ~/.claude/tower, a
Claude Code settings file or a rule file, `tower policy use`, or a `claude plugin` command that turns one
off. JSON that isn't a well-formed tool call, and any crash in this hook, always ask. (Input that isn't
JSON at all passes, leaving the call to Claude Code's own rules.)

Limits: this sees only the command text. These are not caught:
  - scripts and programs: `bash script.sh`, `python x.py`, `make`, or anything that calls the GitHub
    API itself (such as curl with a token, or subprocess.run(["gh", ...]) in Python);
  - commands built at run time: a variable or command output used as the program or as eval/-c code
    (`$GIT push`, `eval "$(...)"`, `bash -c "$CMD"`), xargs input that supplies the subcommand, or a
    tower.conf path supplied at run time (`... | xargs sed -i`);
  - aliases and functions: git aliases set outside the command (`git p` for push) and shell functions;
  - disguised names: globs, brace expansion or zsh glob qualifiers in a program name, and a protected
    path reached through cd and a relative path, a variable, or a glob other than right under a .claude folder;
  - under push=force, a variable holding a force refspec (`git push origin "$REF"`, REF=+main), or git
    config set outside the command that changes what push does;
  - a plain push straight to the default branch (`git push origin HEAD:main`) is judged by push, not
    merge_to_main: refusing direct pushes to main is GitHub branch protection's job (T03, #20);
  - which repo a command acts on, beyond what its words show (see "Which repo's policy"): a GraphQL
    mutation (which can reach any repo by node id), a script that changes folders, zsh's AUTO_CD (a folder
    name alone as a command) and gh repo set-default run in an earlier command are judged by the session's
    repo; TOWER_DIR and TOWER_CONF, which move the policy file and tower.conf for tests, aren't known to the
    safety floor;
  - the safety floor matches text, so a protected file reached by a path built at run time (pieces joined
    in python, a glob, a relative path after a cd that names no protected folder, a copy or unpack into the
    folder above .claude, a symlink inside it) isn't caught. Claude
    Code's own permission rules or sandbox (a write-deny on ~/.claude/tower) are the backstop for that;
  - Claude Code run another way to turn tower's plugin off (npx @anthropic-ai/claude-code, a script), a
    nested session started with hooks off (claude --bare, --settings with disableAllHooks; #87), and an edit
    of the installed plugin's own files under ~/.claude/plugins/.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # only for the annotations: the hook imports tower's core in load_policy(), where a failure is caught
    from tower.policy import Policy, RepoPolicy

# From this file's place, like run-tower.py: the plugin's lib/ holds the link to tower's core.
PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The gh pr and issue writes a policy key covers (GH_KEYS below says which).
GH_WRITE_SUBCOMMANDS = {
    "pr": {
        "comment",
        "review",
        "create",
        "edit",
        "merge",
        "close",
        "reopen",
        "ready",
        "lock",
        "unlock",
        "update-branch",
        "revert",  # opens a PR that reverts a merged one
    },
    "issue": {
        "comment",
        "create",
        "edit",
        "close",
        "reopen",
        "delete",
        "lock",
        "unlock",
        "pin",
        "unpin",
        "transfer",
        "develop",
    },
}
# gh commands that only read GitHub or work locally (`gh help reference`, gh 2.102), by group; a subgroup ("repo
# license") lists its own, and a group or subgroup alone prints its help. Any other gh command may write: a pr or
# issue write is judged by its key below, and the rest (release, label, repo, secret, workflow writes, an extension,
# a gh alias, a command a newer gh adds) ask unless every GitHub key is auto. `auth token` prints a credential, so it
# isn't listed, and neither is `auth status` with -t/--show-token (see prints_token). `extension browse` isn't
# either: its screen can install and remove extensions.
GH_READS: dict[str, set[str]] = {
    "agent-task": {"list", "view"},
    "alias": {"list"},
    "attestation": {"download", "trusted-root", "verify"},
    "auth": {"status"},
    "cache": {"list"},
    "codespace": {"list", "logs", "view"},
    "config": {"get", "list"},
    "discussion": {"list", "view"},
    "extension": {"list", "search"},
    "gist": {"clone", "list", "view"},
    "gpg-key": {"list"},
    "issue": {"list", "status", "view"},
    "label": {"list"},
    "org": {"list"},
    "pr": {"checkout", "checks", "diff", "list", "status", "view"},
    "preview": {"prompter"},
    "project": {"field-list", "item-list", "list", "view"},
    "release": {"download", "list", "verify", "verify-asset", "view"},
    "repo": {"clone", "list", "read-dir", "read-file", "set-default", "view"},
    "repo autolink": {"list", "view"},
    "repo deploy-key": {"list"},
    "repo gitignore": {"list", "view"},
    "repo license": {"list", "view"},
    "ruleset": {"check", "list", "view"},
    "run": {"download", "list", "view", "watch"},
    "secret": {"list"},
    "skill": {"list", "preview", "search"},
    "ssh-key": {"list"},
    "variable": {"get", "list"},
    "workflow": {"list", "view"},
}
# A word that is a cluster of one-letter options (-t, -at), with no value attached.
SHORT_OPTIONS = re.compile(r"-[A-Za-z]+")
# gh commands that only read, whatever follows them. `co`, gh's default alias for pr checkout, isn't one: it lives in
# gh's config, which can redefine it, so it asks like any gh alias.
GH_READ_COMMANDS = {"browse", "completion", "help", "licenses", "search", "status", "version"}
# Not a policy key: a merge is judged by merge_to_main or merge_to_branch once the branch its PR lands on is known.
MERGE = "merge (by where the PR lands)"
# The policy key each gh pr and issue write above is judged by; a group's other writes are under its "*" entry. None
# (locking a PR's conversation, which pr_approval=none never let through), like a gh command that is neither a known
# read nor one of these writes, asks unless every GitHub key is auto.
GH_KEYS: dict[str, dict[str, str | None]] = {
    "pr": {"comment": "comments", "review": "comments", "merge": MERGE, "lock": None, "unlock": None, "*": "pr_writes"},
    "issue": {"comment": "comments", "*": "issue_writes"},
}
GITHUB_KEYS = ("comments", "issue_writes", "pr_writes", "merge_to_main", "merge_to_branch")
# gh pr merge's flags (`gh pr merge --help`), long name -> short letter, to find which PR it merges. -R/--repo is
# gh's own; --help and -h are cobra's.
GH_MERGE_VALUE_FLAGS = {
    "author-email": "A",
    "body": "b",
    "body-file": "F",
    "match-head-commit": "",
    "subject": "t",
    "repo": "R",
}
GH_MERGE_SWITCHES = {
    "admin": "",
    "auto": "",
    "delete-branch": "d",
    "disable-auto": "",
    "merge": "m",
    "rebase": "r",
    "squash": "s",
    "help": "h",
}
# A gh api REST endpoint under one repo: repos/OWNER/REPO/...
REPOS_ENDPOINT = re.compile(r"/?repos/([^/\s?#]+/[^/\s?#]+)")
# A gh api REST endpoint that merges a PR: the synchronous merge, or merge-async (the only one stacked PRs take).
MERGE_ENDPOINT = re.compile(r"/?repos/(?P<repo>[^/\s?#]+/[^/\s?#]+)/pulls/(?P<number>[^/\s?#]+)/merge(?:-async)?")
# A PR number as this hook and `tower merge` both read it: ASCII digits only.
PR_NUMBER = re.compile(r"[1-9][0-9]*")
# How tower is run: its console script, or the plugin's run-tower.py (python3 -I <plugin>/scripts/run-tower.py merge).
TOWER_PROGRAMS = {"tower", "run-tower.py"}
# Python's own switches, which change nothing about which script runs or where (the -I in python3 -I run-tower.py).
PYTHON_SWITCHES = re.compile(r"-[IEsSBuqbOP]+")
# An interpreter option giving it code to run: python -c, node/perl/ruby/osascript -e, php -r, node --eval/--print.
CODE_OPTION = re.compile(r"-[A-Za-z]*[cerE]|--(?:eval|print)\b")
# Inline code (and the arguments after it) that may run `tower merge` names both words; two searches, each linear.
TOWER_WORD = re.compile(r"\btower\b")
MERGE_WORD = re.compile(r"\bmerge\b")
CLEAN_WORD = re.compile(r"\bclean\b")
CLEANUP_REASON = "tower clean removes finished worktrees and deletes their branches, here and on GitHub (cleanup=ask)"
# What a merge's PR lookup (`gh api repos/OWNER/REPO/pulls/N`) prints, on one line: the PR's base, its repo's default
# branch and, for a PR in a GitHub stack, the stack's base (null otherwise).
PR_LOOKUP_JQ = "[.base.ref, .base.repo.default_branch, .stack.base.ref] | @json"
PR_URL = re.compile(r"https://(?P<host>[^/\s]+)/(?P<repo>[^/\s]+/[^/\s]+)/pull/(?P<number>[0-9]+)")
# A line naming these points gh or git at a repo other than the one the hook judges by.
GH_ENV_MENTION = re.compile(r"\bGH_(?:REPO|HOST)\b")
GIT_ENV_MENTION = re.compile(r"\bGIT_(?:DIR|WORK_TREE)\b")
# A git URL: scheme://host/path, or [user@]host:path where the host has an @ before it or a dot in it (not HEAD:main).
GIT_URL = re.compile(r"[A-Za-z][\w+-]*://\S+|[^\s@/:]+@[^\s/:]+:\S+|[^\s@/:]+\.[^\s@/:]+:[^\s/]\S*")
# git push's options that take the next word as their value (--repo is read for its value instead).
GIT_PUSH_OPTIONS_WITH_VALUE = {"-o", "--push-option", "--receive-pack", "--exec"}
# Distinct folders (cd targets, git -C) whose repo the hook looks up for one command line; past it, the strictest. Each
# lookup is one `git remote -v` (a few ms, at most GIT_TIMEOUT_SECONDS) and costs FOLDER_COST of the work budget.
MAX_FOLDERS = 16
FOLDER_COST = 20_000
GH_LOOKUP_TIMEOUT = 5  # seconds for each of a merge's two read-only gh calls
WRITE_METHODS = {"POST", "PATCH", "PUT", "DELETE"}
# git's global options that take the next word as their value (git.c, handle_options).
GIT_OPTIONS_WITH_VALUE = {
    "-C",
    "-c",
    "--git-dir",
    "--work-tree",
    "--namespace",
    "--super-prefix",
    "--config-env",
    "--attr-source",
}
# gh api's flags that take a value (`gh api --help`), long names and short letters; the rest are switches.
GH_API_VALUE_FLAGS = {
    "method",
    "field",
    "raw-field",
    "input",
    "header",
    "jq",
    "template",
    "preview",
    "cache",
    "hostname",
}
GH_API_SHORT_FLAGS = {
    "X": "method",
    "F": "field",
    "f": "raw-field",
    "H": "header",
    "q": "jq",
    "t": "template",
    "p": "preview",
}
# Shells whose -c code, or a heredoc fed to them, is checked like the command line itself.
SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "mksh", "fish", "csh", "tcsh"}
# Programs that run a string argument as shell code (ssh host 'cmd', watch 'cmd', su -c 'cmd').
CODE_STRING_RUNNERS = {"ssh", "su", "runuser", "watch", "tmux", "screen", "script"}
# Interpreters whose code is another language: only `git ...`/`gh ...` text inside it is checked.
LANGUAGE_RUNNER = re.compile(r"(?:python|node|perl|ruby|osascript|php|deno|bun|lua)[\d.]*")
EMBEDDED_COMMAND = re.compile(r"(?<![\w./-])(?:git|gh)[ \t]+[^'\"`\n;|&)]*")
# The programs that start an invocation we check: env for its -S string, source/. for the code they read.
INVOKERS = {"git", "gh", "eval", "env", "source", "."} | SHELLS | CODE_STRING_RUNNERS
# Commands that run the rest of their words as a command. Only used to tell `... | sudo bash` (a shell
# reading the pipe) from `... | grep bash` (bash is just an argument).
WRAPPERS = {
    "env",
    "command",
    "builtin",
    "exec",
    "nohup",
    "sudo",
    "doas",
    "time",
    "timeout",
    "nice",
    "stdbuf",
    "xargs",
    "arch",
    "caffeinate",
    "ionice",
    "taskset",
}
# What a shell reads commands from when given one of these as its script: standard input.
STDIN_PATHS = {"-", "/dev/stdin", "/dev/fd/0"}
# env's options that take the next word as their value (GNU and BSD env).
ENV_OPTIONS_WITH_VALUE = {"-u", "--unset", "-C", "--chdir", "-P"}
# gh's built-in aliases (the ALIASES line of `gh <group> [<subcommand>] --help`, gh 2.102): for groups, and for
# subcommands by group, where "*" holds those of every group (each list is also ls). Unlike a gh alias, they can't
# be redefined.
GH_GROUP_ALIASES = {
    "agent": "agent-task",
    "agents": "agent-task",
    "agent-tasks": "agent-task",
    "at": "attestation",
    "cs": "codespace",
    "ext": "extension",
    "extensions": "extension",
    "rs": "ruleset",
    "skills": "skill",
}
GH_ALIASES = {
    "*": {"ls": "list"},
    "pr": {"new": "create", "co": "checkout"},
    "issue": {"new": "create"},
    "release": {"new": "create"},
    "skill": {"show": "preview"},
}
# git push's long options that force or delete; git also accepts any unambiguous prefix (--del).
GIT_PUSH_FORCE_OPTIONS = ("force", "force-with-lease", "force-if-includes", "mirror", "delete", "prune")
# `git -c` settings that make a plain push mirror (delete remote refs) or use force refspecs.
GIT_FORCING_CONFIG = re.compile(r"remote\..*\.(?:mirror|push)")
ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\+?=")
MAX_NESTING = 8  # shells inside shells (bash -c "bash -c '...'") before we stop and ask
# Characters split plus words examined per tool call: a few seconds at worst on macOS's Python 3.9, far under
# Claude Code's hook timeout.
WORK_BUDGET = 2_000_000

GH_DYNAMIC_REASON = "gh with a subcommand from a variable or command output can't be checked"
HIDDEN_CODE_REASON = "runs shell code it can't see (from a pipe, <( ), standard input or xargs)"


# --- The policy: which actions ask ---------------------------------------------------------------------


@dataclass(frozen=True)
class Gates:
    """The policy keys this hook enforces, for one repo (from tower.policy's Effective)."""

    profile: str
    push: str  # every | force | none
    comments: str  # the rest are ask | auto
    issue_writes: str
    pr_writes: str
    merge_to_main: str
    merge_to_branch: str
    cleanup: str  # ask | auto, for tower clean; not a GitHub key, though it deletes branches there too

    def asks(self, key: str) -> bool:
        """Whether an ask|auto key (one of GITHUB_KEYS, or cleanup) asks."""
        value: str = getattr(self, key)
        return value != "auto"

    @property
    def github_open(self) -> bool:
        """Whether no GitHub write asks: then writes no single key covers (gh api, releases) don't either."""
        return not any(self.asks(key) for key in GITHUB_KEYS)

    @property
    def all_open(self) -> bool:
        """Whether nothing asks, pushes included: then a command the hook can't check doesn't ask either."""
        return self.github_open and self.push == "none" and not self.asks("cleanup")


# The built-in strictest profile, used when tower's core can't be loaded or a value comes from a variable.
STRICTEST_GATES = Gates("work", "every", "ask", "ask", "ask", "ask", "ask", "ask")


def gates_of(repo_policy: RepoPolicy) -> Gates:
    """The hook's view of one repo's policy."""
    keys = {key: repo_policy.value(key) for key in (*GITHUB_KEYS, "cleanup")}
    return Gates(profile=repo_policy.profile, push=repo_policy.push, **keys)


@dataclass(frozen=True)
class SessionPolicy:
    """The policy for the session's repo, why it applies, and what judging another repo or folder needs: the policy
    file as read and the session's own policy (both None when tower's core couldn't be loaded)."""

    gates: Gates
    why: str
    problems: tuple[str, ...] = ()
    loaded: Policy | None = None
    session: RepoPolicy | None = None
    cwd: str = "."
    reach_open: bool = False  # every profile a repo can get lets every push and GitHub write through
    folder_cache: dict[str, RepoPolicy] = field(default_factory=dict)  # each folder's policy, looked up once

    @property
    def has_overrides(self) -> bool:
        """Whether the policy file has [repos] entries, so which repo an action reaches can change what asks."""
        return self.loaded is not None and bool(self.loaded.repos)

    def judge(self, names: list[Word], folders: list[Word]) -> Gates:
        """The gates for an action reaching the repos NAMES names (-R values, URLs) and the checkouts holding FOLDERS
        (relative to the session's folder), besides the session's: the stricter of all, key by key; the strictest
        if one comes from a variable or command output."""
        if not self.has_overrides or (not names and not folders):
            return self.gates  # without [repos] every repo gets the same profile, so which one doesn't matter
        if self.loaded is None or self.session is None or any(word.dynamic for word in [*names, *folders]):
            return STRICTEST_GATES
        combined = self.loaded.with_repos(self.session, [name.text for name in names])
        for folder in folders:
            path = os.path.normpath(os.path.join(self.cwd, os.path.expanduser(folder.text)))
            if path not in self.folder_cache:
                self.folder_cache[path] = self.loaded.for_folder(Path(path))[0]
            combined = self.loaded.with_policy(combined, self.folder_cache[path])
        return gates_of(combined)


def load_policy(cwd: str) -> SessionPolicy:
    """The policy for the repo holding CWD, read through tower's core; the strictest if the core can't be loaded."""
    sys.path.insert(0, os.path.join(PLUGIN_ROOT, "lib"))
    try:
        from tower import policy
    except Exception as err:  # noqa: BLE001 - a broken install or an old python3 must not loosen anything
        return SessionPolicy(STRICTEST_GATES, f"tower's core couldn't be loaded ({type(err).__name__}), so work")
    loaded = policy.load()
    session = loaded.for_folder(Path(cwd))[0] if loaded.repos else loaded.for_repo(None)  # git only when it matters
    reach_open = all(gates_of(found).all_open for found in loaded.profiles_in_play())
    return SessionPolicy(gates_of(session), session.why, session.problems, loaded, session, cwd, reach_open)


# --- Splitting a command line into simple commands ---------------------------------------------------


class Unparseable(ValueError):
    """The command uses shell syntax we can't split reliably, such as an unclosed quote."""


@dataclass
class Word:
    """One shell word with quotes and escapes removed; expansions ($x, $(...), `...`) are kept as written."""

    text: str
    dynamic: bool = False  # holds an expansion ($x, $(...), `...`, $'...'), so its value is only known at run time


@dataclass
class Redirect:
    """One redirection of a simple command: its operator (>, >>, <, ...) and its target."""

    operator: str
    target: Word

    @property
    def writes(self) -> bool:
        """Whether it opens its target for writing (>, >>, >|, &>, <>)."""
        return ">" in self.operator


@dataclass
class SimpleCommand:
    """One simple command (a program and its arguments), its redirections, and its standard input."""

    words: list[Word] = field(default_factory=list)
    redirects: list[Redirect] = field(default_factory=list)
    stdin_text: str | None = None  # the bodies of the heredocs (<<) and here-strings (<<<) it reads
    piped: bool = False  # it reads the output of the command before it in a pipeline


@dataclass
class PendingHeredoc:
    """A heredoc whose body starts on the line after its << operator."""

    command: SimpleCommand
    delimiter: str
    strip_tabs: bool  # <<- strips leading tabs from each body line
    expanded: bool  # the delimiter is unquoted, so bash runs the $( ) and backticks in the body
    to_stdin: bool  # it feeds standard input (<<, 0<<), not another file descriptor (3<<)
    level: int  # the $( ) nesting it was started in; its body starts after a line ends at that level


REDIRECT = re.compile(r"&>>|&>|\d*(?:<<<|<<-|<<|<>|<&|>>|>&|>\||<|>)")
OPERATORS = (";;&", ";;", ";&", "&&", "||", "|&", ";", "&", "|")
WORD_END = set(" \t\r\n;&|()<>")
VARIABLE = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*|[0-9@*#?$!-])")
# Words after which the next word still starts a command (`if x; then case ...`).
RESERVED_WORDS = {"!", "{", "if", "then", "else", "elif", "while", "until", "do", "time"}


def split_commands(text: str) -> list[SimpleCommand]:
    """Split shell TEXT into simple commands, including those inside $( ), backticks and <( ). Raises Unparseable."""
    splitter = CommandSplitter(text)
    splitter.split()
    return splitter.commands


class CommandSplitter:
    """Reads one command line left to right, collecting its simple commands. Use split_commands()."""

    def __init__(self, src: str) -> None:
        """Start reading SRC from its first character."""
        self.src = src
        self.pos = 0
        self.commands: list[SimpleCommand] = []
        self.heredocs: list[PendingHeredoc] = []  # started but not yet read; their bodies come after a line ends
        self.level = 0  # split() calls in progress: 1 for the line itself, one more per $( ) or <( ) inside it
        self.arithmetic = 0  # (( )), $(( )) being read: << there is a shift, not a heredoc

    def peek(self, offset: int = 0) -> str:
        """The character OFFSET places ahead, or "" past the end."""
        index = self.pos + offset
        return self.src[index] if index < len(self.src) else ""

    def split(self, closer: str = "") -> None:
        """Read commands up to CLOSER (")" ends a $( or <( ), or to the end of the input if CLOSER is ""."""
        self.level += 1
        command = SimpleCommand()
        parens: list[bool] = []  # "(" opened in this call and not yet closed; True if it starts an arithmetic ((
        cases = 0  # case statements open in this call: the ")" after each pattern closes nothing
        at_command_start = True  # the next word starts a command (only reserved words came before it)
        while True:
            char = self.peek()
            if not char:
                if closer or parens:
                    raise Unparseable("an unclosed ( or $(")
                self.finish(command)
                self.level -= 1
                return
            if char in " \t\r":
                self.pos += 1
                continue
            at_command_start = at_command_start or not command.words
            if char == "\\" and self.peek(1) == "\n":
                self.pos += 2
            elif char == "\n":
                self.pos += 1
                command = self.finish(command, keep_pipe=not command.words)  # `a |` newline `b` is still a pipe
                self.read_heredoc_bodies()
            elif char == "#":
                end = self.src.find("\n", self.pos)
                self.pos = end if end >= 0 else len(self.src)
            elif char == "(":
                arithmetic = self.peek(1) == "("
                parens.append(arithmetic)
                self.arithmetic += 1 if arithmetic else 0
                self.pos += 1
                command = self.finish(command, keep_pipe=True)
            elif char == ")":
                self.pos += 1
                command = self.finish(command)
                if parens:
                    self.arithmetic -= 1 if parens.pop() else 0
                elif closer and not cases:
                    self.level -= 1
                    return
            elif char in "<>=" and self.peek(1) == "(":  # =( ) is zsh's process substitution
                command.words.append(self.read_process_substitution())
            elif redirect := REDIRECT.match(self.src, self.pos):
                self.read_redirect(command, redirect)
            elif char in ";&|":
                operator = next(op for op in OPERATORS if self.src.startswith(op, self.pos))
                self.pos += len(operator)
                command = self.finish(command)
                command.piped = operator in ("|", "|&")
            else:
                start = self.pos
                command.words.append(self.read_word())
                raw = self.src[start : self.pos]  # as typed: a quoted "case" is not the keyword
                if at_command_start and raw == "case":
                    cases += 1
                elif at_command_start and raw == "esac" and cases:
                    cases -= 1
                at_command_start = at_command_start and raw in RESERVED_WORDS
                continue
            at_command_start = not command.words

    def finish(self, command: SimpleCommand, keep_pipe: bool = False) -> SimpleCommand:
        """Record COMMAND if it has words and return a fresh one, still reading COMMAND's pipe if KEEP_PIPE."""
        if command.words or command.redirects:
            self.commands.append(command)
        return SimpleCommand(piped=keep_pipe and command.piped)

    def read_word(self) -> Word:
        """Read one word, removing quotes and escapes and splitting out the commands in its $( ) and backticks."""
        parts: list[str] = []
        dynamic = False
        while True:
            char = self.peek()
            if not char or char in WORD_END:
                return Word("".join(parts), dynamic)
            if char == "\\":
                if self.peek(1) != "\n":
                    parts.append(self.peek(1) or "\\")
                self.pos += 2
            elif char == "'":
                parts.append(self.read_single_quoted())
            elif char == '"':
                self.pos += 1
                text, has_expansion = self.read_double_quoted()
                dynamic = dynamic or has_expansion
                parts.append(text)
            elif char == "$":
                text, is_expansion = self.read_dollar(quoted=False)
                dynamic = dynamic or is_expansion
                parts.append(text)
            elif char == "`":
                dynamic = True
                parts.append(self.read_backtick())
            else:
                parts.append(char)
                self.pos += 1

    def read_single_quoted(self) -> str:
        """Read a '...' string from its opening quote; returns the text between the quotes."""
        end = self.src.find("'", self.pos + 1)
        if end < 0:
            raise Unparseable("an unclosed '")
        text = self.src[self.pos + 1 : end]
        self.pos = end + 1
        return text

    def read_double_quoted(self) -> tuple[str, bool]:
        """Read the rest of a "..." string; returns its text and whether it holds an expansion."""
        parts: list[str] = []
        has_expansion = False
        while True:
            char = self.peek()
            if not char:
                raise Unparseable('an unclosed "')
            if char == '"':
                self.pos += 1
                return "".join(parts), has_expansion
            if char == "\\" and self.peek(1) in ("$", "`", '"', "\\", "\n"):
                if self.peek(1) != "\n":
                    parts.append(self.peek(1))
                self.pos += 2
            elif char == "$":
                text, is_expansion = self.read_dollar(quoted=True)
                has_expansion = has_expansion or is_expansion
                parts.append(text)
            elif char == "`":
                has_expansion = True
                parts.append(self.read_backtick())
            else:
                parts.append(char)
                self.pos += 1

    def read_dollar(self, quoted: bool) -> tuple[str, bool]:
        """Read what starts at a "$" (QUOTED: inside "..."); returns its text as written and whether it expands.

        The commands inside a $( ) are split out like the rest of the line.
        """
        start = self.pos
        after = self.peek(1)
        if after == "(":
            arithmetic = 1 if self.peek(2) == "(" else 0  # $(( is arithmetic
            self.pos += 2
            self.arithmetic += arithmetic
            self.split(closer=")")
            self.arithmetic -= arithmetic
        elif after == "{" and self.peek(2) in (" ", "\t", "\n", "|"):
            raise Unparseable("a ${ ...; } command substitution")  # bash 5.3 and ksh93 run its contents
        elif after in ("{", "["):  # ${...}, or $[...] (old-style arithmetic)
            self.pos += 2
            self.read_braced(quoted, after)
        elif after == "'" and not quoted:
            self.read_ansi_c()
        elif after == '"' and not quoted:
            self.pos += 2
            return self.read_double_quoted()  # $"..." is a translatable string, read like "..."
        elif variable := VARIABLE.match(self.src, self.pos):
            self.pos = variable.end()
        else:
            self.pos += 1
            return "$", False
        return self.src[start : self.pos], True

    def read_braced(self, quoted: bool, opener: str) -> None:
        """Skip the rest of a ${...} or $[...] (OPENER is { or [; QUOTED: inside "..."), splitting out any commands
        inside it."""
        closer = {"{": "}", "[": "]"}[opener]
        depth = 1
        while depth:
            char = self.peek()
            if not char:
                raise Unparseable(f"an unclosed ${opener}")
            if char == "\\":
                self.pos += 2
            elif char == "'" and not quoted:
                self.read_single_quoted()
            elif char == '"':
                self.pos += 1
                self.read_double_quoted()
            elif char == "$":
                self.read_dollar(quoted)
            elif char == "`":
                self.read_backtick()
            else:
                depth += {opener: 1, closer: -1}.get(char, 0)
                self.pos += 1

    def read_ansi_c(self) -> None:
        """Skip a $'...' string. Its escapes aren't decoded, so the word it is in counts as dynamic."""
        index = self.pos + 2
        while index < len(self.src) and self.src[index] != "'":
            index += 2 if self.src[index] == "\\" else 1
        if index >= len(self.src):
            raise Unparseable("an unclosed $'")
        self.pos = index + 1

    def read_backtick(self) -> str:
        """Read a `...` command substitution, splitting out the commands inside; returns it as written."""
        start = self.pos
        inner: list[str] = []
        index = self.pos + 1
        while True:
            if index >= len(self.src):
                raise Unparseable("an unclosed `")
            char = self.src[index]
            if char == "`":
                break
            if char == "\\" and index + 1 < len(self.src) and self.src[index + 1] in "`$\\":
                inner.append(self.src[index + 1])
                index += 2
            else:
                inner.append(char)
                index += 1
        self.pos = index + 1
        self.commands.extend(split_commands("".join(inner)))
        return self.src[start : self.pos]

    def read_process_substitution(self) -> Word:
        """Read a <(...), >(...) or zsh =(...), splitting out the commands inside; the word stands for a file
        the shell makes up."""
        start = self.pos
        self.pos += 2
        self.split(closer=")")
        return Word(self.src[start : self.pos], dynamic=True)

    def read_redirect(self, command: SimpleCommand, redirect: re.Match[str]) -> None:
        """Read a redirection of COMMAND. A heredoc's body is read once its line ends; a here-string's word now."""
        fd = redirect.group().rstrip("<>&|-")
        operator = redirect.group()[len(fd) :]
        to_stdin = fd in ("", "0")
        self.pos = redirect.end()
        while self.peek() in (" ", "\t"):
            self.pos += 1
        if self.peek() in ("<", ">", "=") and self.peek(1) == "(":
            command.redirects.append(Redirect(operator, self.read_process_substitution()))  # e.g. `> >(tee log)`
            return
        if not self.peek() or self.peek() in WORD_END:
            raise Unparseable(f"a {operator} with nothing after it")
        start = self.pos
        target = self.read_word()
        if operator in ("<<", "<<-") and not self.arithmetic:
            if target.dynamic:
                raise Unparseable("a heredoc delimiter with $ or backticks")
            expanded = not any(char in self.src[start : self.pos] for char in "'\"\\")
            self.heredocs.append(
                PendingHeredoc(command, target.text, operator == "<<-", expanded, to_stdin, self.level)
            )
        elif operator == "<<<" and to_stdin:
            add_stdin(command, target.text)
        else:
            command.redirects.append(Redirect(operator, target))

    def read_heredoc_bodies(self) -> None:
        """Read, in order, the bodies of the heredocs started at this $( ) level or deeper; a line ending inside a
        $( ) doesn't end the line its outer heredocs were started on."""
        waiting = [heredoc for heredoc in self.heredocs if heredoc.level < self.level]
        for heredoc in self.heredocs:
            if heredoc.level < self.level:
                continue
            lines: list[str] = []
            while self.pos < len(self.src):
                end = self.src.find("\n", self.pos)
                if end < 0:
                    end = len(self.src)
                line = self.src[self.pos : end]
                self.pos = end + 1
                if heredoc.strip_tabs:
                    line = line.lstrip("\t")
                if line == heredoc.delimiter:
                    break
                lines.append(line)
            body = "\n".join(lines)
            if heredoc.to_stdin:
                add_stdin(heredoc.command, body)
            if heredoc.expanded:
                expansions = CommandSplitter(body)
                expansions.split_expansions()
                self.commands.extend(expansions.commands)
        self.heredocs = waiting

    def split_expansions(self) -> None:
        """Split out only the commands in the $( ) and backticks of the text, as bash does for a heredoc body."""
        while self.pos < len(self.src):
            char = self.peek()
            if char == "\\":
                self.pos += 2
            elif char == "$":
                self.read_dollar(quoted=True)
            elif char == "`":
                self.read_backtick()
            else:
                self.pos += 1


def add_stdin(command: SimpleCommand, text: str) -> None:
    """Add TEXT to what COMMAND reads on standard input (zsh feeds it every heredoc and here-string it has)."""
    command.stdin_text = text if command.stdin_text is None else command.stdin_text + "\n" + text


# --- Judging the commands -------------------------------------------------------------------------


class TooComplex(Exception):
    """Checking the command would take longer than the hook should; it asks instead."""


class CommandChecker:
    """Checks one Bash command line under the policy for the session's repo.

    It carries a work budget (characters split plus words examined), because a hook that runs past its
    timeout lets the command through unchecked; a huge or crafted command raises TooComplex instead.
    Merges are collected while checking and judged at the end (merge_reasons()), since whether gh can be asked
    for a merge's base depends on the whole command line.
    """

    def __init__(self, session_policy: SessionPolicy, cwd: str) -> None:
        """Check under SESSION_POLICY, for a session working in CWD, with a full work budget."""
        self.session_policy = session_policy
        self.cwd = cwd
        self.budget = WORK_BUDGET
        self.simple_commands = 0  # in the whole command line, nested code included
        self.merges: list[PendingMerge] = []
        self.incomplete = False  # some part couldn't be checked (split, seen into, or checked in time)
        self.folders: list[Word] = []  # every folder the line cds or pushds into, nested code included (absolute)
        self.cd_folder = cwd  # where the line's cds, taken in order, end up
        self.reached: set[str] = set()  # stricter profiles of other repos the line reaches, for the prompt
        self.names_gh_env = False  # the line names GH_REPO or GH_HOST, which point gh at another repo
        self.names_git_env = False  # the line names GIT_DIR or GIT_WORK_TREE, which point git at another repo

    def spend(self, units: int) -> None:
        """Use UNITS of the work budget; raise TooComplex once it is gone."""
        self.budget -= units
        if self.budget < 0:
            raise TooComplex

    def unchecked(self, reason: str) -> list[str]:
        """REASON, for a part that couldn't be checked: it could be any push or GitHub write, to any repo, so it asks
        unless every profile a repo can get lets them all through. Either way the command counts as incomplete for
        the safety floor."""
        self.incomplete = True
        return [] if self.session_policy.reach_open else [reason]

    def reasons(self, command_line: str) -> list[str]:
        """Reasons to ask for the whole COMMAND_LINE, merges included. Raises TooComplex."""
        self.names_gh_env = bool(GH_ENV_MENTION.search(command_line))
        self.names_git_env = bool(GIT_ENV_MENTION.search(command_line))
        found = self.check(command_line)
        return found + [reason for reason in self.merge_reasons() if reason not in found]

    def check(self, command_line: str, depth: int = 0) -> list[str]:
        """Reasons to ask for COMMAND_LINE, one per distinct problem; empty if it doesn't need approval."""
        if depth > MAX_NESTING:
            return self.unchecked("nests shells too deeply to check")
        self.spend(len(command_line) + 1)
        try:
            commands = split_commands(command_line)
        except Unparseable as err:
            return self.unchecked(f"has {err}, so it can't be split into commands to check")
        except RecursionError:  # $( ) or ( ) nested hundreds deep
            return self.unchecked("nests $( ) or ( ) too deeply to check")
        self.simple_commands += len(commands)
        for command in commands:
            self.note_cd(command.words)
        reasons: list[str] = []
        for command in commands:
            found = protected_reasons(command, self.cwd)
            for index in range(len(command.words)):
                found += self.check_invocation(command, index, depth)
            reasons += [reason for reason in found if reason not in reasons]
        return reasons

    def note_cd(self, words: list[Word]) -> None:
        """Record the folder a simple command cds or pushds into, from the session's folder and from the line's last
        cd (one of them is where it really goes); `cd -` and a folder from a variable are marked dynamic."""
        for target in changed_folders(words):
            if target.dynamic:
                self.folders.append(target)
                continue
            path = os.path.expanduser(target.text)
            self.folders += [Word(os.path.join(self.cwd, path)), Word(os.path.join(self.cd_folder, path))]
            self.cd_folder = os.path.join(self.cd_folder, path)

    def check_invocation(self, command: SimpleCommand, index: int, depth: int) -> list[str]:
        """Reasons to ask if word INDEX of COMMAND runs something we check (git, gh, a shell, eval, env -S,
        source, a program given shell code, an interpreter), with the words after it.

        Looking at every word, not just the first, covers wrappers like env, xargs, sudo and timeout.
        """
        name = program_name(command.words[index])
        is_language = bool(LANGUAGE_RUNNER.fullmatch(name))
        is_git_dashed = name.startswith("git-") and runs_as_program(command.words, index)  # not `man git-push`
        if name in TOWER_PROGRAMS and runs_tower(command.words, index):
            # A merge is judged at the end, like gh pr merge; a clean now, by the cleanup key
            self.note_tower_merge(command.words, index, depth == 0)
            return self.tower_clean_reasons(command.words, index)
        if name not in INVOKERS and not is_git_dashed and not is_language:
            return []
        self.spend(len(command.words) - index)
        args = command.words[index + 1 :]
        if name == "eval":
            return self.check(" ".join(arg.text for arg in args), depth + 1)
        if name == "env":
            code = env_split_string(args)
            return self.check(code, depth + 1) if code is not None else []
        if name in ("source", "."):  # only as the program: `git add .` passes a directory
            is_sourcing = args and runs_as_program(command.words, index)
            return self.check_sourced(args[0], command, depth) if is_sourcing else []
        if name in SHELLS:
            return self.check_shell(args, command, runs_as_program(command.words, index), depth)
        if name in CODE_STRING_RUNNERS:  # each argument with spaces or operators may be a command line
            return [r for arg in args if re.search(r"[\s;|&]", arg.text) for r in self.check(arg.text, depth + 1)]
        if is_language:  # code in another language: check the git/gh commands and tower merges written inside it
            found: list[str] = []
            if gives_code(args):  # the code and the arguments after it (sys.argv) together
                joined = " ".join(arg.text for arg in args)
                if TOWER_WORD.search(joined) and MERGE_WORD.search(joined):
                    self.merges.append(PendingMerge("tower merge (in interpreter code)", self.tower_gates(), False))
                if TOWER_WORD.search(joined) and CLEAN_WORD.search(joined) and self.tower_gates().asks("cleanup"):
                    found.append(f"{CLEANUP_REASON}, in interpreter code")
            return found + [
                r for arg in args for m in EMBEDDED_COMMAND.finditer(arg.text) for r in self.check(m.group(), depth + 1)
            ]
        if name == "gh":
            reason = self.check_gh(args, is_whole_command=depth == 0 and index == 0)
        else:  # git, or one of its dashed executables (libexec/git-core/git-push)
            git_args = args if name == "git" else [Word(name[4:]), *args]
            reason = check_git(git_args, lambda: self.push_mode(git_args))
        return [reason] if reason else []

    def note_tower_merge(self, words: list[Word], index: int, top_level: bool) -> None:
        """Collect the `tower merge` that word INDEX of WORDS (tower or run-tower.py) runs, if it does (a subcommand
        from a variable may be merge), to judge at the end like gh pr merge. Its PR can be looked up only when tower
        runs as its own command (TOP_LEVEL; merge_reasons() checks nothing is chained), straight or from python with
        only python's switches, merge comes right after it, and at most a PR number follows."""
        self.spend(len(words) - index)
        position = next_operand(words, index + 1)
        if position is None or not (words[position].dynamic or words[position].text == "merge"):
            return
        launcher, after = words[:index], words[position + 1 :]
        from_python = bool(launcher) and program_name(launcher[0]).startswith("python")
        readable = (
            top_level
            and position == index + 1  # tower's own options before merge (--version, -h) only print
            and not any(word.dynamic for word in words[: position + 1])
            and (not launcher or (from_python and all(PYTHON_SWITCHES.fullmatch(w.text) for w in launcher[1:])))
            and len(after) <= 1
            and all(not word.dynamic and PR_NUMBER.fullmatch(word.text) for word in after)
        )
        selector = after[0].text if readable and after else None
        self.merges.append(PendingMerge("tower merge", self.tower_gates(), readable, selector))

    def tower_clean_reasons(self, words: list[Word], index: int) -> list[str]:
        """The reason to ask for the `tower clean` that word INDEX of WORDS (tower or run-tower.py) runs, if it does
        (a subcommand from a variable may be clean) and cleanup asks. A dry run only checks, so it never asks: tower
        run as the command itself (bare, from python with only python's switches, or with uv run, so the words after
        it are all tower's, not another command's as in find -exec ... \\; -exec ... --dry-run), clean right after it,
        and the exact --dry-run among clean's options (before any --, which makes the words after it worktree
        names), with no word from a variable among them (one could be --)."""
        position = next_operand(words, index + 1)
        if position is None or not (words[position].dynamic or words[position].text == "clean"):
            return []
        launcher, after = words[program_position(words) : index], words[position + 1 :]
        direct = (
            not launcher
            or [word.text for word in launcher] == ["uv", "run"]
            or (
                program_name(launcher[0]).startswith("python")
                and all(PYTHON_SWITCHES.fullmatch(word.text) for word in launcher[1:])
            )
        )
        options = after[: next((i for i, word in enumerate(after) if word.text == "--"), len(after))]
        dry_run = (
            direct
            and position == index + 1
            and not any(word.dynamic for word in words[: position + 1] + options)
            and any(word.text == "--dry-run" for word in options)
        )
        return [] if dry_run or not self.tower_gates().asks("cleanup") else [CLEANUP_REASON]

    def tower_gates(self) -> Gates:
        """The gates for a tower merge or clean: those of the repo it runs in, but the strictest when the line names GH_REPO or
        GH_HOST, which point its gh calls at another repo, as for gh itself."""
        if self.names_gh_env and self.session_policy.has_overrides:
            return self.strictest_reached()
        return self.gates_for([], [])

    def push_mode(self, git_args: list[Word]) -> str:
        """The push key for git run with GIT_ARGS: the stricter of the session's and that of every repo the push
        reaches (a URL it pushes to, the folders git and the line point at)."""
        if self.names_git_env and self.session_policy.has_overrides:
            return self.strictest_reached().push
        named, folders = git_repos(git_args)
        return self.gates_for(named, folders).push

    def strictest_reached(self) -> Gates:
        """The strictest gates, for a repo that can't be told (noted for the prompt)."""
        self.reached.add(STRICTEST_GATES.profile)
        return STRICTEST_GATES

    def gates_for(self, names: list[Word], git_folders: list[Word]) -> Gates:
        """The gates for an action reaching the repos NAMES names and the folders git is pointed at (GIT_FOLDERS,
        -C first), besides every folder the line cds into; remembers a stricter profile reached, for the prompt.
        Each folder counts both from the session's folder and from the line's last cd, since which applies depends
        on subshells; past MAX_FOLDERS distinct folders, it's the strictest rather than run git that often."""
        folders = list(self.folders)
        for base in {self.cwd, self.cd_folder}:
            joined = base
            for folder in git_folders:
                joined = os.path.join(joined, os.path.expanduser(folder.text))
            if git_folders:
                folders.append(Word(joined, any(folder.dynamic for folder in git_folders)))
        if len({folder.text for folder in folders}) > MAX_FOLDERS and self.session_policy.has_overrides:
            return self.strictest_reached()
        else:
            self.spend(len(folders) * FOLDER_COST)
            gates = self.session_policy.judge(names, folders)
        if gates.profile != self.session_policy.gates.profile:
            self.reached.add(gates.profile)
        return gates

    def check_shell(self, args: list[Word], command: SimpleCommand, is_program: bool, depth: int) -> list[str]:
        """Reasons to ask for a shell run with ARGS: its -c code or heredoc is checked like any command line.

        Code it reads from anywhere else (a pipe, <( ), a redirect, xargs) can't be seen, so that asks when
        the shell is COMMAND's program. A script file it runs isn't checked (see Limits).
        """
        shell = parse_shell_args(args)
        if shell.runs_code:
            if shell.code is None or shell.code.text.strip() == "{}":  # the code comes from xargs
                return self.unchecked(HIDDEN_CODE_REASON)
            return self.check(shell.code.text, depth + 1)
        if not is_program:
            return []
        return self.check_sourced(shell.script, command, depth)

    def check_sourced(self, script: Word | None, command: SimpleCommand, depth: int) -> list[str]:
        """Reasons to ask for running SCRIPT in a shell (with source or bash SCRIPT). A file isn't checked, standard
        input is checked if it is a heredoc or here-string, and anything else (a pipe, <( )) can't be seen."""
        if script is not None and not is_process_substitution(script) and script.text not in STDIN_PATHS:
            return []
        if (script is None or script.text in STDIN_PATHS) and command.stdin_text is not None:
            return self.check(command.stdin_text, depth + 1)
        return self.unchecked(HIDDEN_CODE_REASON)

    def check_gh(self, args: list[Word], is_whole_command: bool) -> str | None:
        """Reason to ask for gh run with ARGS, or None if it is a known read or the policy lets it through. A merge is
        only collected here; IS_WHOLE_COMMAND says gh is the program at the top level, which merge_reasons() needs
        before it asks gh for the merge's base."""
        indexes = gh_command_indexes(args, count=3)  # a subgroup's command (gh repo license list) has three words
        if not indexes:
            return None
        flags = [arg for position, arg in enumerate(args) if position not in indexes[:2]]
        without_group = args[: indexes[0]] + args[indexes[0] + 1 :]  # gh api's endpoint stays in

        def gates() -> Gates:
            """The gates for this gh command: the session's and those of every repo it names. Only for a write, since
            finding them may run git."""
            if self.names_gh_env and self.session_policy.has_overrides:
                return self.strictest_reached()
            return self.gates_for(gh_named_repos(without_group), [])

        group = args[indexes[0]]
        if group.dynamic:
            return None if gates().github_open else GH_DYNAMIC_REASON
        if group.text == "api":
            api_merge = gh_api_merge(without_group, is_whole_command)
            if api_merge is not None:
                self.merges.append(PendingMerge("gh api merge", gates(), *api_merge))
                return None
            reason = check_gh_api(without_group)
            return None if reason is None or gates().github_open else reason
        group_name = GH_GROUP_ALIASES.get(group.text, group.text)
        if group_name in GH_READ_COMMANDS or (len(indexes) == 1 and group_name in GH_READS):
            return None  # it only reads, or it is a group alone, which prints its help
        subcommand = args[indexes[1]] if len(indexes) > 1 else Word("")
        if subcommand.dynamic:
            return None if gates().github_open else GH_DYNAMIC_REASON
        name = gh_alias_target(group_name, subcommand.text)
        third = args[indexes[2]] if len(indexes) > 2 else None
        shows_token = prints_token(group_name, name, flags)
        if not shows_token and is_gh_read(group_name, name, third):
            return None
        group_keys = GH_KEYS.get(group_name, {})
        key = group_keys.get(name, group_keys.get("*")) if name in GH_WRITE_SUBCOMMANDS.get(group_name, ()) else None
        action = f"gh {group_name} {name}".rstrip()
        if key == MERGE:
            self.merges.append(parse_merge(flags, gates(), is_whole_command))
            return None
        if key is None:  # any other gh command that isn't a known read: release, repo, secret writes, PR lock, ...
            reason = f"{action} may change something on GitHub that no one key covers (it isn't a known read)"
            reason = f"{action} prints your GitHub auth token" if shows_token else reason
            return None if gates().github_open else reason
        keys = [key]
        if (group_name, name) == ("pr", "edit") and changes_base(flags):
            keys.append("merge_to_main")
        judged = gates()
        asking = [k for k in keys if judged.asks(k)]
        return (
            f"{action} posts or changes something on GitHub ({', '.join(k + '=ask' for k in asking)})"
            if asking
            else None
        )

    def merge_reasons(self) -> list[str]:
        """Reasons to ask for the merges found. A merge whose two keys differ is judged by the branch its PR lands on,
        which gh is asked for only when the merge is the whole command line and its arguments could be read;
        otherwise, or when gh can't say, it asks unless both keys are auto."""
        reasons: list[str] = []
        for merge in self.merges:
            gates = merge.gates
            landing = None
            if gates.merge_to_main != gates.merge_to_branch and merge.readable and self.simple_commands == 1:
                landing = landing_key(merge, self.cwd)
            if landing is None:
                asking = [key for key in ("merge_to_main", "merge_to_branch") if gates.asks(key)]
                into = "into a base gh wasn't asked about or couldn't name"
            else:
                key, stacked = landing
                asking = [key] if gates.asks(key) else []
                branch = "the default branch" if key == "merge_to_main" else "a branch other than the default"
                into = f"onto its stack's base, {branch}" if stacked else f"into {branch}"
            if asking:
                reasons.append(f"{merge.action} merges a PR {into} ({', '.join(k + '=ask' for k in asking)})")
        return reasons


@dataclass(frozen=True)
class PendingMerge:
    """A merge found in the command line (gh pr merge, tower merge, a gh api merge), judged once the whole line has
    been read."""

    action: str  # what merges, for the prompt
    gates: Gates
    readable: bool  # it is the top-level program, every flag is known, nothing comes from a variable, at most one PR
    selector: str | None = None  # the PR as gh pr view takes it: a number, URL or branch; None for this branch's PR
    repo: str | None = None  # gh pr view's -R/--repo value


def gh_named_repos(words: list[Word]) -> list[Word]:
    """The repos a gh command's WORDS (all but its command words) name besides the session's: -R/--repo values, a
    URL anywhere (a PR or issue given by URL: tower reads owner/repo from its path), and a gh api endpoint under
    repos/OWNER/REPO. Wherever they are, so an unknown flag can't hide one."""
    found = gh_repo_flags(words) + [word for word in words if "://" in word.text]
    for word in words:
        endpoint = REPOS_ENDPOINT.match(word.text)
        if endpoint and "{" not in endpoint.group(1):  # {owner}/{repo} is gh's placeholder for this repo
            found.append(Word(endpoint.group(1), word.dynamic))
    return found


def gh_repo_flags(words: list[Word]) -> list[Word]:
    """The values of gh's -R/--repo flags among WORDS (a gh command's words other than its command words)."""
    found: list[Word] = []
    for index, word in enumerate(words):
        following = words[index + 1] if index + 1 < len(words) else None
        text = word.text
        if text == "--repo" and following is not None:
            found.append(following)
        elif text.startswith("--repo="):
            found.append(Word(text.split("=", 1)[1], word.dynamic))
        elif text.startswith("-") and not text.startswith("--") and "R" in text:  # -R, -Rvalue, or clustered: -dR
            attached = text[text.index("R") + 1 :].removeprefix("=")
            if attached:
                found.append(Word(attached, word.dynamic))
            elif following is not None:
                found.append(following)
    return found


def changes_base(words: list[Word]) -> bool:
    """Whether a gh pr edit with WORDS may change the PR's base (-B/--base, or a word from a variable)."""
    return any(word.dynamic or word.text == "--base" or word.text.startswith(("--base=", "-B")) for word in words)


def parse_merge(words: list[Word], gates: Gates, is_whole_command: bool) -> PendingMerge:
    """The merge gh pr merge's WORDS (all but `pr` and `merge`) ask for, read the way gh's flag library reads them,
    to be judged by GATES (those for the repos the command names)."""
    short_values = {letter for letter in GH_MERGE_VALUE_FLAGS.values() if letter}
    short_switches = {letter for letter in GH_MERGE_SWITCHES.values() if letter}
    positionals: list[str] = []
    readable = is_whole_command
    index = 0
    while index < len(words) and readable:
        text = words[index].text
        value: str | None = None
        takes_value = False
        readable = not words[index].dynamic
        if text == "--":
            positionals += [word.text for word in words[index + 1 :]]
            readable = readable and not any(word.dynamic for word in words[index + 1 :])
            break
        if text.startswith("--"):
            name, has_value, attached = text[2:].partition("=")
            if name in GH_MERGE_VALUE_FLAGS:
                takes_value, value = True, attached if has_value else None
            else:
                readable = readable and name in GH_MERGE_SWITCHES and not has_value
        elif text.startswith("-") and len(text) > 1:
            for offset, letter in enumerate(text[1:], start=2):
                if letter in short_values:
                    takes_value, value = True, text[offset:].removeprefix("=") or None
                    break
                readable = readable and letter in short_switches
        else:
            positionals.append(text)
        index += 1
        if takes_value and value is None:  # its value is the next word
            readable = readable and index < len(words) and not words[index].dynamic
            index += 1
    repos = gh_repo_flags(words)
    repo = repos[-1].text if repos else None  # gh's flag library keeps the last -R
    readable = readable and len(positionals) <= 1
    selector = positionals[0] if positionals else None
    return PendingMerge("gh pr merge", gates, readable, selector, repo)


def gh_api_merge(args: list[Word], is_whole_command: bool) -> tuple[bool, str | None, str | None] | None:
    """For `gh api` with ARGS (all but `api`) writing to a PR's merge or merge-async endpoint: whether its PR can be
    looked up, and the PR and repo to look it up by (gh pr view's selector and -R). None for any other gh api call,
    and for a merge endpoint with a method from a variable (check_gh_api() asks about that). The PR can be looked up
    only when gh is the top-level program (IS_WHOLE_COMMAND) and the endpoint names the repo and PR on gh's default
    host; gh fills {owner}/{repo} in gh api from its own repo resolution, which may differ from gh pr view's."""
    call = parse_gh_api(args)
    found = MERGE_ENDPOINT.fullmatch(call.endpoint.text) if call.endpoint and not call.endpoint.dynamic else None
    if found is None or (call.method is not None and call.method.dynamic):
        return None
    implied = "POST" if call.fields or call.has_input else "GET"  # gh's default method
    if (call.method.text.upper() if call.method is not None else implied) not in WRITE_METHODS:
        return None
    repo, number = found.group("repo"), found.group("number")
    other_host = any(arg.text == "--hostname" or arg.text.startswith("--hostname=") for arg in args)
    if is_whole_command and "{" not in repo and PR_NUMBER.fullmatch(number) and not other_host:
        return True, number, repo
    return False, None, None


def landing_key(merge: PendingMerge, cwd: str) -> tuple[str, bool] | None:
    """The key MERGE is judged by, and whether its PR is in a GitHub stack; None if gh can't say. The key is
    merge_to_main if the PR lands on its repo's default branch, else merge_to_branch. A PR lands on its base, but a
    stacked one on its stack's base: merging it merges every PR below it too. gh finds the PR the way the merge does
    (same selector and -R, same folder), then reads it from the REST API, the only one that shows its stack."""
    view = ["gh", "pr", "view", "--json", "url", "--jq", ".url"]
    view += [f"--repo={merge.repo}"] if merge.repo is not None else []
    view += ["--", merge.selector] if merge.selector is not None else []
    url = PR_URL.fullmatch(gh_lookup(view, cwd) or "")
    if url is None:
        return None
    endpoint = f"repos/{url.group('repo')}/pulls/{url.group('number')}"
    try:
        found = json.loads(
            gh_lookup(["gh", "api", "--hostname", url.group("host"), endpoint, "--jq", PR_LOOKUP_JQ], cwd) or ""
        )
    except ValueError:
        return None
    if not (isinstance(found, list) and len(found) == 3):
        return None
    base, default, stack_base = found
    if not all(isinstance(name, str) and name for name in (base, default)):
        return None
    if stack_base is not None and not (isinstance(stack_base, str) and stack_base):
        return None
    lands = stack_base or base
    return ("merge_to_main" if lands == default else "merge_to_branch"), stack_base is not None


def gh_lookup(argv: list[str], cwd: str) -> str | None:
    """The one line a read-only gh call prints, run in CWD with a short timeout; None if it fails or says more."""
    env = {**os.environ, "GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1", "NO_COLOR": "1"}
    try:
        proc = subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=GH_LOOKUP_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    out = proc.stdout.strip()
    return out if proc.returncode == 0 and out and "\n" not in out else None


def runs_tower(words: list[Word], index: int) -> bool:
    """Whether word INDEX (naming tower or run-tower.py) is what its command runs: the program itself (or after a
    wrapper like env or sudo), or an argument of any program but git and those that only print or read
    (python3 run-tower.py, uv run tower), so `git -C ~/code/tower merge` and `echo tower merge` aren't tower merges."""
    if runs_as_program(words, index):
        return True
    first = program_position(words)
    return first < index and program_name(words[first]) not in READ_ONLY_PROGRAMS | {"git"}


def gives_code(args: list[Word]) -> bool:
    """Whether an interpreter's ARGS give it inline code to run (-c, -e, --eval, ...), not only a script file."""
    return any(not arg.dynamic and CODE_OPTION.match(arg.text) for arg in args) or any(arg.dynamic for arg in args)


def program_name(word: Word) -> str:
    """The program WORD names, lower-cased since macOS finds GIT as git: its last path part, without zsh's "="
    prefix (=git is git's path) or a zsh brace group's "{"; "" if that part comes from a variable or command
    output."""
    name = os.path.basename(word.text).lstrip("={").lower()
    return "" if "$" in name or "`" in name else name


def program_position(words: list[Word]) -> int:
    """Index of the program a simple command runs: its first word that isn't a VAR=value assignment or a
    reserved word like `{` or `then` (len(words) if there is none)."""
    index = 0
    while index < len(words) and (ASSIGNMENT.match(words[index].text) or words[index].text in RESERVED_WORDS):
        index += 1
    return index


def runs_as_program(words: list[Word], index: int) -> bool:
    """Whether word INDEX is the program its command runs, not an argument (bash in `grep bash`): the program
    itself, or any later word when the program is a wrapper like sudo, env or timeout."""
    first = program_position(words)
    return index == first or (first < index and program_name(words[first]) in WRAPPERS)


def is_process_substitution(word: Word) -> bool:
    """Whether WORD is a <( ), >( ) or zsh =( ): a file name the shell makes up for a command's output."""
    return word.dynamic and word.text.startswith(("<(", ">(", "=("))


def env_split_string(args: list[Word]) -> str | None:
    """The command `env` runs from -S/--split-string (env -S 'git push'), or None if it has none."""
    index = 0
    while index < len(args):
        text = args[index].text
        following = args[index + 1].text if index + 1 < len(args) else None
        if text.startswith("--split-string="):
            return text.split("=", 1)[1]
        if text == "--split-string":
            return following
        if text.startswith("-") and not text.startswith("--") and "S" in text:
            return text[text.index("S") + 1 :] or following
        if not text.startswith("-") and not ASSIGNMENT.match(text):
            return None  # the command env runs starts here
        index += 2 if text in ENV_OPTIONS_WITH_VALUE else 1
    return None


@dataclass
class ShellArgs:
    """What a shell's arguments say it runs: -c code, or a script (None: commands from standard input)."""

    runs_code: bool = False  # it was given -c
    code: Word | None = None  # the code given with -c (None if xargs supplies it)
    script: Word | None = None  # the script it runs; None or a STDIN_PATHS entry means standard input


def parse_shell_args(args: list[Word]) -> ShellArgs:
    """What a shell run with ARGS executes: -c code, a script file, or commands from standard input."""
    index = 0
    runs_code = from_stdin = False
    while index < len(args):
        text = args[index].text
        if text == "--":
            index += 1
            break
        if text in ("--version", "--help"):
            return ShellArgs(script=args[index])  # prints and exits, reading no commands
        if text in ("--rcfile", "--init-file"):
            index += 2
        elif text == "--command":  # fish's long form of -c
            runs_code = True
            index += 1
        elif text.startswith("--command="):
            return ShellArgs(runs_code=True, code=Word(text.split("=", 1)[1], args[index].dynamic))
        elif text.startswith("--"):
            index += 1
        elif len(text) > 1 and text[0] in "-+":
            runs_code = runs_code or "c" in text
            from_stdin = from_stdin or "s" in text
            index += 2 if ("o" in text or "O" in text) else 1  # -o/-O take an option name
        else:
            break
    operand = args[index] if index < len(args) else None
    if runs_code:
        return ShellArgs(runs_code=True, code=operand)
    return ShellArgs(script=None if from_stdin else operand)  # -s: commands from stdin, operands are arguments


def check_git(args: list[Word], mode: Callable[[], str]) -> str | None:
    """Reason to ask for git run with ARGS: a push, judged by the policy's push mode (MODE is called only for a push,
    since finding it may run git), found past git's global options.

    A subcommand from a variable, command output or a `-c alias.*` set in the same command could be push.
    """
    index = 0
    aliases: set[str] = set()
    config_forces = False  # -c remote.<name>.mirror/push can make a plain push delete or force
    while index < len(args) and args[index].text.startswith("-"):
        if args[index].text == "-c" and index + 1 < len(args):
            key = args[index + 1].text.split("=", 1)[0].lower()
            if key.startswith("alias."):
                aliases.add(key[len("alias.") :])
            config_forces = config_forces or bool(GIT_FORCING_CONFIG.fullmatch(key))
        index += 2 if args[index].text in GIT_OPTIONS_WITH_VALUE else 1
    if index >= len(args):
        return None
    subcommand = args[index]
    if subcommand.dynamic or subcommand.text.lower() in aliases:
        action = "git with a subcommand from a variable, command output or alias, which could be push,"
        return check_push(args[index + 1 :], mode(), action, config_forces)
    if subcommand.text != "push":
        return None
    return check_push(args[index + 1 :], mode(), "git push", config_forces)


def git_repos(args: list[Word]) -> tuple[list[Word], list[Word]]:
    """The repos a git push run with ARGS (git's own arguments, subcommand included) reaches besides the session's
    checkout: (repo URLs: from -c remote.*.url/pushurl and url.*.insteadOf settings, and the push's repository when
    that is a URL or a variable; folders: -C, --git-dir, --work-tree, in order). Only called for a push, or for a
    subcommand that may be one (an alias, a variable), so the words after the subcommand are read as push's."""
    named: list[Word] = []
    folders: list[Word] = []
    index = 0
    while index < len(args) and args[index].text.startswith("-"):
        text = args[index].text
        value = args[index + 1] if index + 1 < len(args) else None
        if text in ("-C", "--git-dir", "--work-tree") and value is not None:
            folders.append(value)
        elif text.startswith(("--git-dir=", "--work-tree=")):
            folders.append(Word(text.split("=", 1)[1], args[index].dynamic))
        elif text == "-c" and value is not None:
            named += config_repos(value)
        index += 2 if text in GIT_OPTIONS_WITH_VALUE else 1
    return named + push_destination(args[index + 1 :]), folders


def config_repos(setting: Word) -> list[Word]:
    """The repo URLs a `git -c` SETTING (key=value) points git at: remote.<name>.url or .pushurl's value, and both
    sides of url.<base>.insteadOf / pushInsteadOf; none for any other setting."""
    key, _, value = setting.text.partition("=")
    lowered = key.lower()
    if lowered.startswith("remote.") and lowered.endswith((".url", ".pushurl")):
        return [Word(value, setting.dynamic)]
    for suffix in (".insteadof", ".pushinsteadof"):
        if lowered.startswith("url.") and lowered.endswith(suffix):
            return [Word(key[len("url.") : -len(suffix)], setting.dynamic), Word(value, setting.dynamic)]
    return []


def push_destination(args: list[Word]) -> list[Word]:
    """A git push's repository (its first argument that isn't an option, or --repo's value, with = or after it) when
    it is a URL or comes from a variable: then it may be another repo than any remote's. A remote's name gives
    nothing, since every remote already counts."""
    index = 0
    while index < len(args):
        text = args[index].text
        if text.startswith("--repo="):
            return push_destination([Word(text.split("=", 1)[1], args[index].dynamic)])
        if text == "--repo":
            return push_destination(args[index + 1 : index + 2])
        if text in GIT_PUSH_OPTIONS_WITH_VALUE:
            index += 2
        elif text.startswith("-") and text != "-":
            index += 1
        else:
            word = args[index]
            return [word] if word.dynamic or GIT_URL.fullmatch(word.text) else []
    return []


def changed_folders(words: list[Word]) -> list[Word]:
    """The folder a simple command's WORDS cd or pushd into, as a one-item list (none if it doesn't): the first word
    after cd that isn't an option, ~ for a bare cd, and for `cd -` (the previous folder) a word marked dynamic."""
    for index in range(len(words)):
        if program_name(words[index]) in ("cd", "pushd") and runs_as_program(words, index):
            rest = [word for word in words[index + 1 :] if not word.text.startswith("-") or word.text == "-"]
            if not rest:
                return [Word("~")]
            return [Word(rest[0].text, dynamic=True)] if rest[0].text == "-" else [rest[0]]
    return []


def check_push(args: list[Word], mode: str, action: str, config_forces: bool = False) -> str | None:
    """Reason to ask for ACTION, a git push with ARGS, under the policy's push MODE (CONFIG_FORCES: a -c setting
    may make it force or delete)."""
    if mode == "none":
        return None
    is_force = config_forces or any(is_force_push_arg(arg.text) for arg in args)
    if mode == "every":
        return action + (" (force/delete: rewrites or removes remote history)" if is_force else "") + " (push=every)"
    if is_force:
        return "force push or branch delete rewrites or removes remote history (push=force)"
    return None


def is_force_push_arg(arg: str) -> bool:
    """Whether a git push argument forces or deletes: --force*, --mirror, --delete, --prune or a prefix of one,
    f or d among short options (-f, -fu), or a refspec starting with + (force) or : (delete)."""
    if arg.startswith("--"):
        name = arg[2:].split("=", 1)[0]
        return bool(name) and any(option.startswith(name) for option in GIT_PUSH_FORCE_OPTIONS)
    if arg.startswith("-"):
        return "f" in arg or "d" in arg
    return arg.startswith(("+", ":"))


def is_gh_read(group: str, name: str, third: Word | None) -> bool:
    """Whether `gh GROUP NAME` is a known read (GH_READS), THIRD being the command word after NAME if any: NAME is a
    listed read, or NAME is a subgroup (`gh repo license`) and THIRD is missing (its help) or one of its reads."""
    if name in GH_READS.get(group, ()):
        return True
    subgroup = GH_READS.get(f"{group} {name}")
    if subgroup is None:
        return False
    return third is None or (not third.dynamic and gh_alias_target(f"{group} {name}", third.text) in subgroup)


def gh_alias_target(group: str, subcommand: str) -> str:
    """The gh subcommand SUBCOMMAND of GROUP stands for: itself, or what gh's built-in alias for it names (pr co is
    checkout, any ls is list)."""
    return GH_ALIASES.get(group, {}).get(subcommand) or GH_ALIASES["*"].get(subcommand, subcommand)


def prints_token(group: str, name: str, flags: list[Word]) -> bool:
    """Whether `gh GROUP NAME` with FLAGS (the words after them) prints the auth token, which no known read does: gh
    auth token, gh auth status -t/--show-token (or -t in a cluster), gh config get oauth_token (or a key from a
    variable, which may be it)."""
    if group == "auth":
        return name == "token" or any(
            flag.text.startswith("--show-token") or has_short_option(flag.text, "t") for flag in flags
        )
    return (group, name) == ("config", "get") and any(flag.text == "oauth_token" or flag.dynamic for flag in flags)


def has_short_option(text: str, letter: str) -> bool:
    """Whether the word TEXT gives the one-letter option LETTER, alone (-t) or in a cluster of them (-at)."""
    return bool(SHORT_OPTIONS.fullmatch(text)) and letter in text[1:]


def gh_command_indexes(args: list[Word], count: int) -> list[int]:
    """Indexes in ARGS of gh's first COUNT command words (`pr`, `comment`), found the way gh's CLI library
    (cobra) finds them: a flag without "=" takes the next word as its value, except -h and --help, so
    `gh pr -R owner/repo comment` is a comment; "--" ends the search. Past the second, a word may be an argument
    (`gh pr view 5`): only a subgroup's (`gh repo license list`) is a command word."""
    found: list[int] = []
    index = 0
    while index < len(args) and len(found) < count:
        text = args[index].text
        if text == "--":
            break
        takes_value = "=" not in text and (
            (text.startswith("--") and text != "--help") or (len(text) == 2 and text[0] == "-" and text != "-h")
        )
        if takes_value:
            if len(args) - index - 1 <= 1:  # cobra stops when the flag has at most one word after it
                break
            index += 2
            continue
        if text and not text.startswith("-"):
            found.append(index)
        index += 1
    return found


def check_gh_api(args: list[Word]) -> str | None:
    """Reason to ask for `gh api` with ARGS (everything but the word `api`): a GraphQL mutation or a query
    we can't read, or a REST call with a write method, or with fields or --input and no method."""
    call = parse_gh_api(args)
    if call.endpoint is not None and call.endpoint.text == "graphql" and not call.endpoint.dynamic:
        if any("mutation" in arg.text for arg in args):
            return "gh api graphql mutation changes something on GitHub"
        unreadable = call.has_input or any(
            f.text.startswith("query=") and (f.text.startswith("query=@") or f.dynamic) for f in call.fields
        )
        return (
            "gh api graphql's query comes from a file, variable or command output, so it can't be checked"
            if (unreadable)
            else None
        )
    if call.method is not None:
        if call.method.dynamic:
            return "gh api with a method from a variable or command output can't be checked"
        verb = call.method.text.upper()
        return f"gh api {verb} changes something on GitHub" if verb in WRITE_METHODS else None
    if call.fields or call.has_input:
        return "gh api with fields defaults to POST, which changes something on GitHub"
    return None


@dataclass
class GhApiCall:
    """What a `gh api` command's arguments ask for."""

    endpoint: Word | None = None
    method: Word | None = None  # the last -X/--method given
    fields: list[Word] = field(default_factory=list)  # -f/-F/--field/--raw-field values
    has_input: bool = False  # --input: the request body comes from a file


def parse_gh_api(args: list[Word]) -> GhApiCall:
    """Parse gh api's ARGS the way its flag library (pflag) does: values attached (-XPOST, -X=POST,
    --method=POST) or separate, short switches clustered (-iXPOST)."""
    call = GhApiCall()
    positionals: list[Word] = []
    index = 0
    while index < len(args):
        flag_word = args[index]
        text = flag_word.text
        name: str | None = None
        value: str | None = None
        if text == "--":
            positionals += args[index + 1 :]
            break
        if text.startswith("--"):
            flag, has_equals, attached = text[2:].partition("=")
            if flag in GH_API_VALUE_FLAGS:
                name, value = flag, attached if has_equals else None
        elif text.startswith("-") and len(text) > 1:
            for offset, letter in enumerate(text[1:], start=2):
                if letter in GH_API_SHORT_FLAGS:
                    attached = text[offset:]
                    name, value = GH_API_SHORT_FLAGS[letter], attached.removeprefix("=") or None
                    break
        else:
            positionals.append(args[index])
        index += 1
        if name is None:
            continue
        if value is not None:  # attached to its flag word, so dynamic if that word is
            word = Word(value, dynamic=flag_word.dynamic)
        elif index < len(args):
            word = args[index]
            index += 1
        else:
            word = Word("")
        if name == "method":
            call.method = word
        elif name in ("field", "raw-field"):
            call.fields.append(word)
        elif name == "input":
            call.has_input = True
    call.endpoint = positionals[0] if positionals else None
    return call


# --- Rule files, tower's files, and the hook itself -----------------------------------------------

# Case-insensitive, since macOS's disk is: TOWER.conf is tower.conf. Paths are normalized (a//b, a/./b) first.
# Edit/Write: CLAUDE.local.md, tower.conf, anything under ~/.claude/tower/ (rules, policy file, event log), and Claude
# Code's settings files, the user's and any project's (enabledPlugins or disableAllHooks there turns this hook off).
PROTECTED_PATH = re.compile(
    r"(?:^|/)CLAUDE\.local\.md$|/\.claude/tower/|(?:^|/)tower\.conf$|(?:^|/)\.claude/settings(?:\.local)?\.json$",
    re.IGNORECASE,
)
# Bash: naming a rule file asks even for a read.
RULE_FILE_MENTION = re.compile(r"CLAUDE\.local\.md|\.claude/tower/rules", re.IGNORECASE)
# Bash: tower.conf, the rest of ~/.claude/tower/ (policy file, event log) and Claude Code's settings files may be read
# by READ_ONLY_PROGRAMS.
TOWER_FILE_MENTION = re.compile(
    r"tower\.conf|policy\.toml|\.claude/tower(?![\w.-])|\.claude/settings(?:\.local)?\.json"
    r"|\.claude/[^/\s]*[{*?[]",  # a glob or brace expansion right under a .claude folder (.claude/{settings.json,x})
    re.IGNORECASE,
)
# Bash: a word that is a .claude folder itself, which a copy, download or unpack can put a settings file into
# (cp settings.json ~/.claude/); only READ_ONLY_PROGRAMS may name it. A whole word, or an option's attached value
# (--output-dir=.claude, -C.claude), so prose naming it doesn't count.
CLAUDE_FOLDER = re.compile(r"(?:\S*[/=]|-[A-Za-z]+)?\.claude", re.IGNORECASE)
# Programs that can't write a file except through a redirection.
READ_ONLY_PROGRAMS = {"cat", "grep", "egrep", "fgrep", "head", "tail", "wc", "ls", "stat", "file", "diff", "cmp"}
READ_ONLY_PROGRAMS |= {"echo", "printf", "test", "[", "true", ":", "jq"}
RULE_FILE_REASON = "the command touches a personal rule file (CLAUDE.local.md or ~/.claude/tower/rules)"
TOWER_FILE_REASON = (
    "the command may change tower.conf, the policy file (which sets these approvals), tower's files or Claude Code's "
    "settings (which can turn this hook off)"
)
TOWER_PLUGIN_REASON = "the command may turn off tower's plugin, whose hook asks for these approvals"
# `claude plugin` subcommands that turn a plugin off (uninstall's alias is remove), and `claude plugin marketplace`
# ones that remove a marketplace with its plugins (remove's alias is rm).
PLUGIN_OFF_VERBS = {"disable", "uninstall", "remove"}
MARKETPLACE_OFF_VERBS = {"remove", "rm"}
# Programs that add words of their own to the command they run: xargs and parallel from their input, find -exec's {}.
ARGUMENT_SUPPLIERS = {"xargs", "parallel", "find"}
# More words "plugin" after claude than a real command has (one, or one more as an option's value): asking is cheaper
# than reading each one as the group.
MAX_PLUGIN_GROUPS = 3
# Characters that make the shell expand a word into others (a glob, brace expansion), so its text isn't the name.
EXPANDING = re.compile(r"[{*?[]")
PLUGIN_GROUPS = {"plugin", "plugins"}  # claude's plugin command and its alias
# Text that may turn a plugin off, in this order (see mentions_in_order): in a command that couldn't be checked fully,
# or in another language's code.
PLUGIN_OFF_MENTION = (
    re.compile(r"\bclaude\b", re.IGNORECASE),
    re.compile(r"\bplugins?\b", re.IGNORECASE),
    re.compile(rf"\b(?:{'|'.join(sorted(PLUGIN_OFF_VERBS | MARKETPLACE_OFF_VERBS))})\b", re.IGNORECASE),
)
POLICY_COMMAND_REASON = (
    "the command may change tower's policy (tower policy use, its policy module, or tower's setup.sh), "
    "which only you do"
)
# Bash text that may run `tower policy use` (tower, then policy) or setup.sh --profile, for a command that couldn't be
# checked fully.
POLICY_COMMAND_MENTION = (re.compile("tower", re.IGNORECASE), re.compile(r"\bpolicy\b", re.IGNORECASE))
SETUP_MENTION = re.compile(r"setup\.sh", re.IGNORECASE)
# Interpreter code that uses tower's policy module, or runs `tower policy use` (not a path that merely holds both words).
POLICY_CODE = re.compile(
    r"from\s+tower(?:\.\w+)*\s+import\b[^\n;]*\bpolicy\b|\btower\.policy\b|\bimport\s+tower\b|\btower\s+policy\s+use\b"
)


def check_protected_edit(file_path: str) -> str | None:
    """Reason to ask for an edit tool call that targets a personal rule file or tower's settings, by its own path or
    the one its symlinks resolve to (a link to ~/.claude reaches tower's files under another name)."""
    path = os.path.expanduser(file_path)
    if any(PROTECTED_PATH.search(candidate) for candidate in (os.path.normpath(path), os.path.realpath(path))):
        return f"edits a personal rule file, tower's settings or Claude Code's ({file_path})"
    return None


def protected_reasons(command: SimpleCommand, cwd: str) -> list[str]:
    """Reasons to ask because COMMAND names a rule file or one of tower's files, judged on its words and redirect
    targets with quotes removed and paths normalized. A rule file asks even when only read. tower.conf, a
    policy.toml and the rest of ~/.claude/tower may be read by a read-only program; a redirect into them, or any
    other program naming them, asks, and so does a write to a path under .claude whose text is only known at run
    time ($HOME, $'...'), and so does a non-read program naming a .claude folder itself. CWD is the session's folder
    (see changes_policy)."""
    if changes_policy(command.words, cwd):
        return [POLICY_COMMAND_REASON]
    if turns_tower_off(command.words):
        return [TOWER_PLUGIN_REASON]
    words = [os.path.normpath(word.text) for word in command.words if word.text]
    targets = [(redirect.writes, os.path.normpath(redirect.target.text)) for redirect in command.redirects]
    texts = words + [target for _, target in targets]
    if any(RULE_FILE_MENTION.search(text) for text in texts):
        return [RULE_FILE_REASON]
    program = program_position(command.words)
    reads_only = program >= len(command.words) or program_name(command.words[program]) in READ_ONLY_PROGRAMS
    hidden = [redirect.target for redirect in command.redirects if redirect.writes]
    hidden += [] if reads_only else command.words
    if any(word.dynamic and ".claude" in word.text for word in hidden):
        return [TOWER_FILE_REASON]
    if not reads_only and any(CLAUDE_FOLDER.fullmatch(text) for text in words):
        return [TOWER_FILE_REASON]
    if not any(TOWER_FILE_MENTION.search(text) for text in [*texts, command.stdin_text or ""]):
        return []
    if any(writes and TOWER_FILE_MENTION.search(target) for writes, target in targets):
        return [TOWER_FILE_REASON]
    return [] if reads_only else [TOWER_FILE_REASON]  # only assignments (their $( ) are checked alone), or a read


def changes_policy(words: list[Word], cwd: str) -> bool:
    """Whether a simple command's WORDS may change tower's policy: run `tower policy use` (a word naming tower, then
    `policy`, then `use` or a word from a variable: `uv run tower ...`, `python3 -m tower ...` and run-tower.py all
    count), give python, node or another interpreter code that uses tower's policy module or runs `tower policy use`,
    or run tower's setup.sh (named with a path holding "tower", or from a session folder that does) for anything but
    --dry-run or --help, since it rewrites tower.conf and can set the profile from its prompt."""
    texts = [word.text for word in words]
    for index, text in enumerate(texts):
        rest = words[index + 1 :]
        if LANGUAGE_RUNNER.fullmatch(program_name(words[index])) and any(
            POLICY_CODE.search(later) for later in texts[index + 1 :]
        ):
            return True
        is_tower_setup = os.path.basename(text).lower() == "setup.sh" and "tower" in (text + cwd).lower()
        if is_tower_setup and not any(word.text in ("--dry-run", "-h", "--help") for word in rest):
            return True
        if "tower" in text.lower() and "policy" in texts[index + 1 :]:
            after_policy = rest[[w.text for w in rest].index("policy") + 1 :]
            if any(word.text == "use" or word.dynamic for word in after_policy):
                return True
    return False


def turns_tower_off(words: list[Word]) -> bool:
    """Whether a simple command's WORDS run `claude plugin disable|uninstall|remove` or `claude plugin marketplace
    remove|rm` on what may be tower (see plugin_args_turn_tower_off). claude counts wherever it is a word, unless the
    command's program only reads or prints its words (echo, grep); any later word "plugin" may be the group, since an
    option's value can say plugin too (claude -d plugin plugin disable tower), and so may a word from a variable. The
    first claude word is the one that runs: the words after it are its own. Code in another language (python -c)
    that mentions claude, plugin and an off verb in that order counts too."""
    program = program_position(words)
    if program >= len(words) or program_name(words[program]) in READ_ONLY_PROGRAMS:
        return False
    if any(LANGUAGE_RUNNER.fullmatch(program_name(word)) for word in words[program:]) and any(
        mentions_in_order(word.text, PLUGIN_OFF_MENTION) for word in words[program + 1 :]
    ):
        return True
    index = next((i for i in range(program, len(words)) if program_name(words[i]) == "claude"), None)
    if index is None:
        return False
    supplied = any(program_name(word) in ARGUMENT_SUPPLIERS for word in words[program:index])
    rest = words[index + 1 :]
    groups = [i for i, word in enumerate(rest) if word.text in PLUGIN_GROUPS or word.dynamic]
    if len(groups) > MAX_PLUGIN_GROUPS:
        return True
    return any(plugin_args_turn_tower_off(rest[i + 1 :], supplied) for i in groups)


def plugin_args_turn_tower_off(args: list[Word], supplied: bool) -> bool:
    """Whether `claude plugin` with ARGS turns off what may be tower: a name holding "tower", from a variable or that
    the shell expands (t{ow,}er), no name at all, disable --all, or any name when SUPPLIED (xargs, parallel or find
    run claude and may add tower's).
    A subcommand from a variable may be one that does. --help counts like any option, and after -- every word is a
    name."""
    verb_at, verbs = next_operand(args, 0), PLUGIN_OFF_VERBS
    if verb_at is not None and args[verb_at].text == "marketplace":  # its own subcommand comes next
        verb_at, verbs = next_operand(args, verb_at + 1), MARKETPLACE_OFF_VERBS
    if verb_at is None or not (args[verb_at].dynamic or args[verb_at].text in verbs):
        return False
    after = args[verb_at + 1 :]
    end = next((i for i, word in enumerate(after) if word.text == "--"), len(after))
    options = [word.text for word in after[:end] if word.text.startswith("-")]
    names = [word for word in after[:end] if not word.text.startswith("-")] + after[end + 1 :]  # -s user's too
    disables_all = "--all" in options or any(has_short_option(option, "a") for option in options)
    hidden = any(word.dynamic or EXPANDING.search(word.text) or "tower" in word.text.lower() for word in names)
    return supplied or not names or disables_all or hidden


def mentions_in_order(text: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    """Whether TEXT has a match of each of PATTERNS, each after the one before. The earliest match of each leaves the
    most room for the next, so this finds any such sequence in linear time, where one regex joining them with .*
    would backtrack for minutes over a long padded command."""
    position = 0
    for pattern in patterns:
        match = pattern.search(text, position)
        if match is None:
            return False
        position = match.end()
    return True


def next_operand(words: list[Word], start: int) -> int | None:
    """Index of the first of WORDS from START on that isn't an option (doesn't start with -), or None."""
    return next((i for i in range(start, len(words)) if not words[i].text.startswith("-")), None)


def ask(reasons: list[str], prefix: str) -> None:
    """Print the "ask" decision that makes Claude Code show an approval prompt, with PREFIX and the REASONS."""
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                    "permissionDecisionReason": prefix + "; ".join(reasons),
                }
            }
        )
    )


def decide(call: object) -> tuple[list[str], str] | None:
    """The reasons and prompt prefix for asking about one tool CALL, or None to leave it to Claude Code's rules."""
    unreadable = (["the tool call isn't in the shape the hook expects, so it can't be checked"], "Approve? ")
    if not isinstance(call, dict):
        return unreadable
    tool = call.get("tool_name")
    if tool not in {"Bash", "Edit", "Write", "MultiEdit"}:
        return None
    tool_input = call.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return unreadable
    if tool != "Bash":
        file_path = tool_input.get("file_path") or ""
        if not isinstance(file_path, str):
            return unreadable
        reason = check_protected_edit(file_path)
        return ([reason], "Rule or settings change — approve? ") if reason else None
    command = tool_input.get("command") or ""
    if not isinstance(command, str):
        return unreadable
    cwd = call.get("cwd")
    cwd = cwd if isinstance(cwd, str) and cwd else os.getcwd()
    session_policy = load_policy(cwd)
    checker = CommandChecker(session_policy, cwd)
    try:
        reasons = checker.reasons(command)
    except TooComplex:
        reasons = checker.unchecked("is too long or complex to check in time")
    if RULE_FILE_MENTION.search(command) and RULE_FILE_REASON not in reasons:
        reasons.append(RULE_FILE_REASON)  # also catches a rule file named in a heredoc or a string
    if checker.incomplete:  # the safety floor holds for what couldn't be checked, even when nothing else asks
        if TOWER_FILE_MENTION.search(command) and TOWER_FILE_REASON not in reasons:
            reasons.append(TOWER_FILE_REASON)
        mentions_policy = mentions_in_order(command, POLICY_COMMAND_MENTION) or SETUP_MENTION.search(command)
        if mentions_policy and POLICY_COMMAND_REASON not in reasons:
            reasons.append(POLICY_COMMAND_REASON)
        if mentions_in_order(command, PLUGIN_OFF_MENTION) and TOWER_PLUGIN_REASON not in reasons:
            reasons.append(TOWER_PLUGIN_REASON)
    if not reasons:
        return None
    problems = session_policy.problems
    note = f"; its file has {len(problems)} problem(s), see `tower policy`" if problems else ""
    reached = f"; it reaches another repo: {', '.join(sorted(checker.reached))}" if checker.reached else ""
    label = f"tower policy: {session_policy.gates.profile}{reached}{note}"
    return reasons, f"Public action or rule change ({label}) — approve? "


def main() -> int:
    """Read one tool call from stdin and print an ask decision if it is a public action or a rule-file change."""
    try:
        call = json.load(sys.stdin)
    except ValueError:
        return 0  # not a tool call we understand; let Claude Code's normal rules decide
    try:
        decision = decide(call)
    except Exception as err:  # noqa: BLE001 - a crash would let the command run unchecked, so ask instead
        decision = ([f"the approval hook failed ({type(err).__name__}: {err})"], "Approve? ")
    if decision:
        ask(*decision)
    return 0


if __name__ == "__main__":
    sys.exit(main())
