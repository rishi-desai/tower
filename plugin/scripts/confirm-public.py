#!/usr/bin/env python3
"""confirm-public.py: Claude Code PreToolUse hook. Forces an approval prompt before public GitHub actions.

Claude Code runs this before every Bash command and sends the call as JSON on stdin. If the command
would post or change something other people can see, we print a JSON "ask" decision, and Claude Code
shows the user a confirmation prompt, even when an allow rule like Bash(git *) would normally skip it.

What asks is set by tower's policy for the session's repo (src/tower/policy.py, read through the plugin's
lib/tower link to tower's core; `tower policy` shows it). Each action is judged by one key of it:
  push             git push: every -> every push asks; force -> only force pushes, deletions, --prune and
                   +refspecs (and pushes under config that can force: -c remote.*.mirror/push, any --config-env,
                   an alias for a forcing push or one that sets git's options (a `!` alias's shell code is checked
                   as code instead), or config it can't read: GIT_CONFIG_*, HOME or XDG_CONFIG_HOME set on the
                   line, an include.path, help.autocorrect, a key from a variable, under which a subcommand that
                   isn't one of git's own, so may be an alias, counts as a possible push);
                   none -> no push asks
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
or with -c/--config-env remote.*/url.* settings, git -C/--git-dir/--work-tree, every folder the line cds or
pushds into, and every folder env -C/--chdir or sudo -D/--chdir runs a command in. A repo from a variable or
command output, a line naming GH_REPO or GH_HOST (for gh and tower), or naming GIT_DIR, GIT_WORK_TREE or
GIT_COMMON_DIR or setting config through variables (GIT_CONFIG_*, HOME, XDG_CONFIG_HOME; for git push, and gh and
tower, which find their repo through git), and `cd -` get the strictest profile. If tower's core can't be
loaded, the built-in strictest profile (work: everything asks) applies.

Safety floor, in every profile: any Edit/Write/MultiEdit of a CLAUDE.local.md, tower.conf (in a .claude
folder, or the one TOWER_CONF names), a file under ~/.claude/tower/ or $TOWER_DIR (the rules, the policy file,
the event log) or a Claude Code settings file (.claude/settings.json or settings.local.json, the user's or a
project's), by its path or one a symlink resolves to, always asks. So does any Bash command naming a rule file
(CLAUDE.local.md, ~/.claude/tower/rules/), running `tower policy use` (however tower is run, tower's policy module
used in python/node code included) or tower's setup.sh (other than --dry-run), which rewrites tower.conf and can
set the profile, or turning tower's plugin off: `claude plugin disable|uninstall|remove` or `claude plugin
marketplace remove|rm` naming tower, with a name from a variable or none, disable --all, or any name xargs,
parallel or find may add. A Bash command naming tower.conf, the policy file, another file in ~/.claude/tower, a
Claude Code settings file or a .claude folder itself (cp settings.json ~/.claude/) asks unless it only reads it
with a program like cat, grep, tail or jq, and so does one writing to a path under .claude that comes from a
variable or $'...' quoting.

A file named tower.conf or policy.toml anywhere else (a test fixture in a temp folder) isn't tower's: a Bash path
ending in either name counts only when it resolves to tower's file, the way the Edit check judges, relative to
the session's folder and every folder the line cds into, through symlinks, and put under its own name into any
folder another argument names (git -C DIR, a copy's destination). So a commit message, a heredoc or a fixture
that merely says tower.conf passes. What can't be resolved still asks: a path or folder from a variable, command
output, a glob or brace expansion or a ~name, a relative one after a cd to a bare name (CDPATH may send it
anywhere), any one beside such an argument, any one in code for python, node, awk, vim and the like (which can
build a path at run time), and any one in a line that may make a link (ln).

Reading tower's setup.sh or policy module (src/tower/policy.py) isn't running or writing it, so the floor lets a read
through, but only in the narrowest form and asks for everything else (it fails closed). A read passes only when the
line is one simple command (no pipe, no second command, no $( ), no wrapper) whose program is a plain file reader
(cat, grep, head, ...), a sed whose one script only prints lines (1,20p, /re/p), or git with a read/stage/show
subcommand (diff, log, show, status, add, ls-files) on a clean line: no -c/--config-env, no word from a variable, no
option that hands a file to a program (--ext-diff, --textconv, --output, grep's -O), and nothing on the line that can
make git run a program (GIT_EXTERNAL_DIFF, a pager or editor variable, a `git config` of such a key). Anything else
that names setup.sh asks: running it (except `./setup.sh --dry-run`/`--help`, which only check), a write into it or
the policy module by redirect, a copy, an in-place edit or a destination option's value (cp, tee, sed -i, dd of=,
git --output=), and code for python, node, awk or sed, a heredoc's included, which runs setup.sh or names the policy
module's path. A bare write to the two files is caught here on the Bash side; the Edit/Write tools don't gate them
(ADR 0010).

Rule files are standing instructions loaded into every session, tower.conf and the policy file set the approvals
above, and the plugin and Claude Code's settings (enabledPlugins, disableAllHooks, hooks) decide whether this hook
runs at all, so a prompt injection must not be able to change them silently: an agent can't loosen its own rules.

How a command is read: it is split into simple commands roughly the way bash or zsh splits it: on
&& || ; | & and newlines and around ( ), respecting quotes, escapes, comments, heredocs, case
statements and arithmetic. Commands inside $( ), backticks and <( ) are checked too, and so is the code
given to `bash -c` (or sh, zsh, csh, ...), `env -S`, eval, ssh/su/watch/tmux/screen/script, or a shell
through a heredoc. So is the shell code git itself runs from what the line says: the value of a setting that names a
command (core.sshCommand, core.pager, core.editor, diff.external, credential.helper, gpg.program, a diff, filter or
merge driver, ...) given with -c, --config-env or GIT_CONFIG_* (with its value on the line), a clone's -c or a `git
config` that saves it; a `!` alias's code with the words after it; the command given to submodule foreach, rebase
--exec/-x, bisect run, difftool -x, filter-branch's filters and --upload-pack/--receive-pack; and the variables naming
a command git runs (GIT_SSH_COMMAND, GIT_EDITOR, EDITOR, PAGER, ...). A subcommand that is a -c alias is read as what
it stands for. In each simple command, every word naming git or gh (by any path, in any letter
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
output, a command git would run that is only known at run time (-c core.sshCommand="$X", --config-env naming a
variable the line doesn't set, rebase --exec "$CMD": the shell parses what the variable holds as code too) or that an
ext:: URL names (a setting or GIT_ALLOW_PROTOCOL letting ext run), and a command too big to check quickly. Those ask
unless the policy lets every push and GitHub write through (sandbox), and even then they ask when their text names
tower.conf, ~/.claude/tower, a Claude Code settings file or a rule file, `tower policy use`, or a `claude plugin`
command that turns one off. JSON that isn't a well-formed tool call, and any crash in this hook, always ask. (Input
that isn't JSON at all passes, leaving the call to Claude Code's own rules.)

Limits: this sees only the command text. These are not caught:
  - scripts and programs: `bash script.sh`, `python x.py`, `make`, or anything that calls the GitHub
    API itself (such as curl with a token, or subprocess.run(["gh", ...]) in Python);
  - commands built at run time: a variable or command output used as the program or as eval/-c code
    (`$GIT push`, `eval "$(...)"`, `bash -c "$CMD"`) or in a variable naming a command git runs
    (GIT_SSH_COMMAND="$CMD", EDITOR; a git setting's value asks instead), xargs input that supplies the subcommand, or a
    tower.conf path supplied at run time (`... | xargs sed -i`);
  - aliases and functions: git aliases set outside the command (`git p` for push) and shell functions;
  - disguised names: globs, brace expansion or zsh glob qualifiers in a program name, and a protected
    path reached through cd and a relative path, a variable, or a glob other than right under a .claude folder;
  - under push=force, a variable holding a force refspec (`git push origin "$REF"`, REF=+main), or git
    config that changes what push does set outside the command, by an earlier command of the same line
    (`git config remote.origin.mirror true && git push`), or by eval or source code that runs after a push
    written before it (in a loop or a function);
  - commands git runs from config the line doesn't show: a command-running key or `!` alias saved in a config file
    (the repo's, the user's, an include.path, GIT_CONFIG_GLOBAL or a HOME the line moves) or inherited in
    GIT_CONFIG_PARAMETERS, and an ext:: URL in a saved remote or .gitmodules while ext is allowed outside the line;
  - files git runs by name rather than a command written out: hooks installed under .git/hooks, the folder
    core.hooksPath names, helpers in GIT_EXEC_PATH, and a merge strategy or remote helper found on PATH;
  - a plain push straight to the default branch (`git push origin HEAD:main`) is judged by push, not
    merge_to_main: refusing direct pushes to main is GitHub branch protection's job (T03, #20);
  - which repo a command acts on, beyond what its words show (see "Which repo's policy"): a GraphQL
    mutation (which can reach any repo by node id), a script or another program that changes folders
    (`tmux new -c DIR`, `make -C DIR`), zsh's AUTO_CD (a folder
    name alone as a command) and gh repo set-default run in an earlier command are judged by the session's
    repo; TOWER_DIR and TOWER_CONF, which move the policy file and tower.conf for tests, are known to the
    safety floor only as the hook's own environment has them, not as a command sets them;
  - the safety floor sees only the command, so a protected file reached by a path built at run time (pieces joined
    in python, a glob of a .claude/tower folder, a copy or unpack into the folder above .claude, a symlink
    inside it, a hard link, an argument folder written as an option's value glued to another option) isn't
    caught, and neither is setup.sh run by code that a reader pipes it into (python code that runs what it
    reads), by git under config set outside the command that hands files to a program (diff.external, a
    textconv or filter, a pager), or through a glob or a variable set outside the command. Claude Code's own
    permission rules or sandbox (a write-deny on ~/.claude/tower) are the backstop for that;
  - Claude Code run another way to turn tower's plugin off (npx @anthropic-ai/claude-code, a script), a
    nested session started with hooks off (claude --bare, --settings with disableAllHooks; #87), and an edit
    of the installed plugin's own files under ~/.claude/plugins/.
"""

from __future__ import annotations

import json
import os
import re
import shlex
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
# isn't listed, and neither is `auth status` with -t/--show-token (see prints_token). `auth git-credential` is: it is
# the credential helper `gh auth setup-git` saves, which hands git the token (its store and erase do nothing); run by
# hand with get it prints the token, so that asks (prints_token).
# `extension browse` isn't listed: its screen can install and remove extensions.
GH_READS: dict[str, set[str]] = {
    "agent-task": {"list", "view"},
    "alias": {"list"},
    "attestation": {"download", "trusted-root", "verify"},
    "auth": {"status", "git-credential"},
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
GIT_ENV_MENTION = re.compile(r"\bGIT_(?:DIR|WORK_TREE|COMMON_DIR)\b")
# Setting one of these gives git config the hook can't read: GIT_CONFIG_* (GIT_CONFIG_COUNT=1, export
# GIT_CONFIG_PARAMETERS=...) holds settings, and HOME and XDG_CONFIG_HOME move the global config file. It could alias
# push, make it force or point it at another repo. See sets_git_config(). (Another repo's own config, reached with
# GIT_DIR, -C or cd, is that repo's, like the session's: see GIT_ENV_MENTION and Limits.)
GIT_CONFIG_VARIABLE = r"(?:GIT_CONFIG\w*|HOME|XDG_CONFIG_HOME)"
GIT_CONFIG_ASSIGNMENT = re.compile(GIT_CONFIG_VARIABLE + r"\+?=")
GIT_CONFIG_NAME = re.compile(GIT_CONFIG_VARIABLE + r"(?:\+?=|$)")
# Shell builtins whose arguments name variables they set or export (export GIT_CONFIG_COUNT, read GIT_CONFIG_KEY_0).
VARIABLE_SETTERS = {"export", "declare", "typeset", "readonly", "local", "read"}
# git settings that hide what git does: include.path reads more config from a file the hook can't see, and
# help.autocorrect runs the subcommand git guesses for a mistyped one (psuh).
GIT_HIDING_CONFIG = re.compile(r"include(?:if\..*)?\.path|help\.autocorrect")
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
# Commands that run the rest of their words as a command. Used to tell `... | sudo bash` (a shell reading the pipe)
# from `... | grep bash` (bash is just an argument), and to find variables a wrapper like env sets for its command.
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
# sudo's options that take the next word as their value (sudo -C is --close-from; its folder option is -D).
SUDO_OPTIONS_WITH_VALUE = {"-a", "-C", "-c", "-D", "-g", "-p", "-R", "-r", "-T", "-t", "-U", "-u"}
SUDO_OPTIONS_WITH_VALUE |= {"--auth-type", "--close-from", "--login-class", "--chdir", "--group", "--prompt"}
SUDO_OPTIONS_WITH_VALUE |= {"--chroot", "--role", "--command-timeout", "--type", "--other-user", "--user"}
# env's options that take the next word as their value (GNU and BSD env; -S's value is the command env runs).
ENV_OPTIONS_WITH_VALUE = {"-u", "--unset", "-C", "--chdir", "-P", "-S", "--split-string", "-L", "-U", "-a", "--argv0"}
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
# git's own commands that agents run (`git --list-cmds=main`). git ignores an alias named like one, so under config
# the hook can't read these are judged as themselves; any other subcommand may be an alias for push.
GIT_OWN_COMMANDS = {
    *("add", "am", "apply", "archive", "bisect", "blame", "branch", "cat-file", "check-ignore", "checkout"),
    *("cherry-pick", "clean", "clone", "commit", "config", "describe", "diff", "fetch", "for-each-ref"),
    *("format-patch", "fsck", "gc", "grep", "help", "init", "log", "ls-files", "ls-remote", "ls-tree", "merge"),
    *("merge-base", "mv", "notes", "pull", "range-diff", "rebase", "reflog", "remote", "reset", "restore"),
    *("rev-list", "rev-parse", "revert", "rm", "shortlog", "show", "show-ref", "sparse-checkout", "stash"),
    *("status", "submodule", "switch", "symbolic-ref", "tag", "version", "worktree"),
}
# git push's long options that force or delete; git also accepts any unambiguous prefix (--del).
GIT_PUSH_FORCE_OPTIONS = ("force", "force-with-lease", "force-if-includes", "mirror", "delete", "prune")
# `git -c` settings that make a plain push mirror (delete remote refs) or use force refspecs.
GIT_FORCING_CONFIG = re.compile(r"remote\..*\.(?:mirror|push)")
# git subcommands' options whose value is a shell command git runs, as (long names, short letters): rebase's --exec,
# the command difftool runs on each pair of files, filter-branch's filters and setup, the program a fetch, clone, push
# or archive runs for the other side (on this machine for a local repo; ls-remote's --exec is its old name), the
# commands send-email runs, and grep's pager.
GIT_CODE_OPTIONS: dict[str, tuple[set[str], set[str]]] = {
    "rebase": ({"exec"}, {"x"}),
    "difftool": ({"extcmd"}, {"x"}),
    "filter-branch": (
        {
            "env-filter",
            "tree-filter",
            "index-filter",
            "parent-filter",
            "msg-filter",
            "commit-filter",
            "tag-name-filter",
            "setup",
        },
        set(),
    ),
    "fetch": ({"upload-pack"}, set()),
    "pull": ({"upload-pack"}, set()),
    "ls-remote": ({"upload-pack", "exec"}, set()),
    "fetch-pack": ({"upload-pack", "exec"}, set()),
    "send-pack": ({"receive-pack", "exec"}, set()),
    "clone": ({"upload-pack"}, {"u"}),
    "push": ({"receive-pack", "exec"}, set()),
    "archive": ({"exec"}, set()),
    "send-email": ({"sendmail-cmd", "to-cmd", "cc-cmd", "header-cmd"}, set()),
    "grep": ({"open-files-in-pager"}, {"O"}),
}
# Those of them whose value is optional, so given only attached (-Oless, --open-files-in-pager=less): a bare -O opens
# the default pager, and the word after it is grep's pattern.
GIT_ATTACHED_CODE_OPTIONS = {"grep"}
# git subcommands that run the command given as their last arguments, by the word before it: submodule foreach
# (through the shell when the command is one word, else as it is), bisect run (each word quoted, through the shell).
GIT_COMMAND_VERBS = {"submodule": "foreach", "bisect": "run"}
# git config's options that take the next word as their value, and the options and subcommands that only read, remove
# or edit settings rather than set one (git config --help).
GIT_CONFIG_OPTIONS_WITH_VALUE = {"-f", "--file", "--blob", "--type", "--default", "--comment", "--value", "--url"}
GIT_CONFIG_NON_WRITES = {
    *("--get", "--get-all", "--get-regexp", "--get-urlmatch", "--get-color", "--get-colorbool", "--unset"),
    *("--unset-all", "-l", "--list", "-e", "--edit", "--rename-section", "--remove-section"),
    *("get", "list", "unset", "rename-section", "remove-section", "edit"),
}
# Variables naming a command git (or another program) runs: their value, set on the line, is checked like bash -c
# code. git falls back to EDITOR, VISUAL and PAGER; GIT_SSH and GIT_ASKPASS name a program it runs as it is.
COMMAND_VARIABLES = {
    *("GIT_SSH_COMMAND", "GIT_SSH", "GIT_ASKPASS", "SSH_ASKPASS", "GIT_PROXY_COMMAND", "GIT_EXTERNAL_DIFF"),
    *("GIT_EDITOR", "GIT_SEQUENCE_EDITOR", "EDITOR", "VISUAL", "GIT_PAGER", "PAGER"),
}
# The variables that give git settings one by one (GIT_CONFIG_KEY_0, GIT_CONFIG_VALUE_0), by kind and number.
GIT_CONFIG_PAIR = re.compile(r"GIT_CONFIG_(KEY|VALUE)_(\d+)")
# A shell parameter that hands on the words after a `!` alias ("$@", $*, $1), which git passes to its code.
POSITIONAL_PARAMETER = re.compile(r"\$\{?[@*1-9]")
# git settings that let an ext:: URL run the command it names (git refuses it by default), unless set to never.
GIT_EXT_PROTOCOL_KEYS = {"protocol.ext.allow", "protocol.allow"}
ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\+?=")
MAX_NESTING = 8  # shells inside shells (bash -c "bash -c '...'") before we stop and ask
# Characters split plus words examined (with their characters, where a check scans their text) per tool call: a few
# seconds at worst on macOS's Python 3.9, far under Claude Code's hook timeout.
WORK_BUDGET = 2_000_000
# Work units per path the safety floor looks up on disk (a folder test, or resolving symlinks for is_tower_file): about
# 60 microseconds on macOS's Python 3.9, so a command naming thousands of paths runs out of budget instead of time.
FILE_LOOKUP_COST = 100

GH_DYNAMIC_REASON = "gh with a subcommand from a variable or command output can't be checked"
HIDDEN_CODE_REASON = "runs shell code it can't see (from a pipe, <( ), standard input or xargs)"
RUNTIME_CODE_REASON = (
    "a command git would run can't be checked: it is only known at run time (a -c, --config-env, GIT_CONFIG_* or "
    "alias value, or an --exec, from a variable or command output), named by an ext:: URL or moved by a git config "
    "--rename-section"
)


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


# A redirect's target that is another file descriptor (2>&1, >&-), not a file.
DESCRIPTOR = re.compile(r"\d+|-")


@dataclass
class Redirect:
    """One redirection of a simple command: its file descriptor (the "2" of 2>, "" for the default), its operator
    (>, >>, <, ...) and its target."""

    operator: str
    target: Word
    fd: str = ""

    @property
    def writes(self) -> bool:
        """Whether it opens its target for writing (>, >>, >|, &>, <>)."""
        return ">" in self.operator

    @property
    def writes_file(self) -> bool:
        """Whether it saves the command's standard output into a file a later command can read or run: a >, >> or &>
        onto a real file (not /dev/null or another descriptor). A 2> (or other high-fd) redirect saves stderr, not
        the output of a read, so it isn't a copy of what was read."""
        is_descriptor = self.operator.endswith("&") and bool(DESCRIPTOR.fullmatch(self.target.text))
        captures_stdout = self.operator.startswith("&") or self.fd in ("", "1")
        return self.writes and captures_stdout and self.target.text != "/dev/null" and not is_descriptor


@dataclass
class SimpleCommand:
    """One simple command (a program and its arguments), its redirections, and its standard input."""

    words: list[Word] = field(default_factory=list)
    redirects: list[Redirect] = field(default_factory=list)
    stdin_text: str | None = None  # the bodies of the heredocs (<<) and here-strings (<<<) it reads
    piped: bool = False  # it reads the output of the command before it in a pipeline
    substituted: bool = False  # it runs inside $( ), backticks or <( ): its output becomes another command's words


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
        """Record COMMAND if it has words and return a fresh one, still reading COMMAND's pipe if KEEP_PIPE. Inside a
        $( ) or <( ) (a level past the line's own), COMMAND is substituted."""
        if command.words or command.redirects:
            command.substituted = command.substituted or self.level > 1
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
        self.commands.extend(mark_substituted(split_commands("".join(inner))))
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
            command.redirects.append(Redirect(operator, self.read_process_substitution(), fd))  # e.g. `> >(tee log)`
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
            command.redirects.append(Redirect(operator, target, fd))

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
                self.commands.extend(mark_substituted(expansions.commands))  # all from the body's $( ) and backticks
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


def mark_substituted(commands: list[SimpleCommand]) -> list[SimpleCommand]:
    """COMMANDS, split from a backtick or a heredoc body's $( ) by a splitter of their own, marked substituted."""
    for command in commands:
        command.substituted = True
    return commands


def add_stdin(command: SimpleCommand, text: str) -> None:
    """Add TEXT to what COMMAND reads on standard input (zsh feeds it every heredoc and here-string it has)."""
    command.stdin_text = text if command.stdin_text is None else command.stdin_text + "\n" + text


# --- Judging the commands -------------------------------------------------------------------------


class TooComplex(Exception):
    """Checking the command would take longer than the hook should; it asks instead."""


class CommandChecker:
    """Checks one Bash command line under the policy for the session's repo.

    It carries a work budget (characters split plus words examined), because a hook that runs past its
    timeout never asks you: Claude Code blocks the command (before 2.1.295, runs it unchecked); a huge or crafted
    command raises TooComplex instead.
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
        self.names_git_env = False  # the line names GIT_DIR, GIT_WORK_TREE or GIT_COMMON_DIR: git at another repo
        self.git_config_from_env = False  # the line sets GIT_CONFIG_*, HOME, ...: git config the hook can't read
        self.makes_links = False  # the line may run ln: the safety floor judges tower's file names by text
        self.git_may_run_program = False  # the line can make git run a program (external diff, pager, editor)
        self.cds_by_name = False  # the line cds to a bare name, which CDPATH or zsh's cdpath may send anywhere
        self.assigned: dict[str, list[Word]] = {}  # every value the line gives each variable, nested code included
        # the values the line gives each variable for its later commands too (not NAME=value cmd or env NAME=value cmd)
        self.line_assigned: dict[str, list[Word]] = {}
        # git's global options (-C, -c, --config-env, ...) that the code being checked inherits from the git running it
        self.git_context: list[Word] = []

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
        self.makes_links = bool(LINK_MENTION.search(command_line))
        found = self.check(command_line)
        found += [reason for reason in self.variable_code_reasons() if reason not in found]
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
            self.note_folders(command.words)
            scoped = command_scoped_names(command.words)
            for name, value in variable_assignments(command.words):
                self.assigned.setdefault(name, []).append(value)
                if name not in scoped:
                    self.line_assigned.setdefault(name, []).append(value)
            self.git_config_from_env = self.git_config_from_env or sets_git_config(command.words, self.spend)
            self.git_may_run_program = self.git_may_run_program or git_runs_program(command.words)
        # A read of setup.sh or the policy module is let through only when it is the line's one simple command; a pipe,
        # a second command or a substitution (each adds a command) asks, so the floor fails closed (ADR 0010).
        single_command = depth == 0 and len(commands) == 1
        reasons: list[str] = []
        for command in commands:
            searched = [Word("a bare cd target", dynamic=True)] if self.cds_by_name else []
            # git can't be trusted to only read: it may run a program on the files, or run under config the hook can't
            # read or in another repo
            git_unsafe = self.git_may_run_program or self.git_config_from_env or self.names_git_env
            self.spend(len(self.folders))  # each command is judged against every folder the line cds into so far
            folders = self.folders + searched
            found = protected_reasons(
                command, self.cwd, folders, self.spend, self.makes_links, single_command, git_unsafe
            )
            program = program_of(command.words)
            for index in range(len(command.words)):
                found += self.check_invocation(command, index, program, depth)
            reasons += [reason for reason in dict.fromkeys(found) if reason not in reasons]
        return reasons

    def note_folders(self, words: list[Word]) -> None:
        """Record the folder a simple command cds or pushds into, from the session's folder and from the line's last
        cd (one of them is where it really goes); `cd -` and a folder from a variable are marked dynamic. Record the
        folder env -C or sudo -D runs its command in the same way, without moving the line's later commands there."""
        for target in changed_folders(words):
            first = target.text.split("/")[0]
            self.cds_by_name = self.cds_by_name or first not in ("", ".", "..") and not first.startswith("~")
            if target.dynamic:
                self.folders.append(target)
                continue
            path = os.path.expanduser(target.text)
            self.spend(len(self.cd_folder) + len(path))  # joining copies the path the line's cds have built so far
            joined = os.path.join(self.cd_folder, path)
            self.folders += [Word(os.path.join(self.cwd, path)), Word(joined)]
            self.cd_folder = joined
        chdirs = wrapper_folders(words, program_of(words), self.spend)
        if chdirs:
            parts = [os.path.expanduser(folder.text) for folder in chdirs]
            # each join copies its base and the parts: onto the session's folder and onto the cds' path
            self.spend(len(self.cwd) + len(self.cd_folder) + 2 * sum(len(part) for part in parts))
            dynamic = any(folder.dynamic for folder in chdirs)
            self.folders += [Word(os.path.join(base, *parts), dynamic) for base in (self.cwd, self.cd_folder)]

    def check_invocation(self, command: SimpleCommand, index: int, program: Program, depth: int) -> list[str]:
        """Reasons to ask if word INDEX of COMMAND runs something we check (git, gh, a shell, eval, env -S,
        source, a program given shell code, an interpreter), with the words after it. PROGRAM is the program COMMAND
        runs (program_of).

        Looking at every word, not just the first, covers wrappers like env, xargs, sudo and timeout.
        """
        name = program_name(command.words[index])
        is_language = bool(LANGUAGE_RUNNER.fullmatch(name))
        is_git_dashed = name.startswith("git-") and program.runs(index)  # not `man git-push`
        if name in TOWER_PROGRAMS and runs_tower(index, program):
            # A merge is judged at the end, like gh pr merge; a clean now, by the cleanup key
            self.note_tower_merge(command.words, index, depth == 0)
            return self.tower_clean_reasons(command.words, index, program)
        if name not in INVOKERS and not is_git_dashed and not is_language:
            return []
        self.spend(scan_cost(command.words, index, len(command.words)))
        args = command.words[index + 1 :]
        if name == "eval":
            return self.check(" ".join(arg.text for arg in args), depth + 1)
        if name == "env":
            line = env_split_line(args)
            return self.check(f"env {line}", depth + 1) if line is not None else []
        if name in ("source", "."):  # only as the program: `git add .` passes a directory
            is_sourcing = args and program.runs(index)
            return self.check_sourced(args[0], command, depth) if is_sourcing else []
        if name in SHELLS:
            return self.check_shell(args, command, program.runs(index), depth)
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
            # env -C DIR / sudo -D DIR before git work like git -C DIR, so a later git -C joins onto DIR
            # the words before git, whose names are read for wrappers (their options pay as they're read)
            self.spend(scan_cost(command.words, 0, index))
            moved = [
                part
                for folder in wrapper_folders(command.words[:index], program, self.spend)
                for part in (Word("-C"), folder)
            ]
            git_args = [*moved, *(args if name == "git" else [Word(name[4:]), *args])]
            # code a git runs (a ! alias, rebase --exec) inherits its -C and its settings (git passes them on in
            # GIT_CONFIG_PARAMETERS), so a git in that code is judged with them: -c alias.p=push, a mirroring remote
            self.spend(len(self.git_context))
            judged = [*self.git_context, *git_args]
            reason = check_git(judged, lambda: self.push_mode(judged), self.git_config_from_env)
            found = [reason] if reason else []
            # --config-env reads the variable git is started with: a value the line gives it for later commands, or
            # one given to this command (X=v git, env X=v git); any other assignment (X=v true) leaves it unknown
            self.spend(len(self.line_assigned))  # copied for each git, like the folders for each push
            config_env = dict(self.line_assigned)
            for variable, value in variable_assignments(command.words[: index + 1]):
                config_env[variable] = [*config_env.get(variable, []), value]
            codes = git_run_code(git_args, self.assigned, self.spend, config_env)
            outer, self.git_context = self.git_context, judged[: git_subcommand_at(judged)]
            try:
                return found + [reason for reason in self.code_reasons(codes, depth + 1) if reason not in found]
            finally:
                self.git_context = outer
        return [reason] if reason else []

    def code_reasons(self, codes: list[Word], depth: int) -> list[str]:
        """Reasons to ask for CODES, shell code that git or another program will run: each is checked like bash -c
        code at DEPTH, and one only known at run time (dynamic) also can't be checked, since the shell parses what the
        variable holds as code too (core.sshCommand="ssh -i $KEY" runs whatever KEY adds)."""
        found: list[str] = []
        for code in {(code.text, code.dynamic): code for code in codes}.values():  # each once: a padded line repeats
            reasons = self.check(code.text, depth) + (self.unchecked(RUNTIME_CODE_REASON) if code.dynamic else [])
            found += [reason for reason in reasons if reason not in found]
        return found

    def variable_code_reasons(self) -> list[str]:
        """Reasons to ask for the commands the line's variables hand to git or another program: the values of
        COMMAND_VARIABLES (GIT_SSH_COMMAND, EDITOR) and of git settings given through GIT_CONFIG_* that name a command
        (environment_code). Checked once the whole line has been read, wherever the variable is set, since a loop or
        a later command may be the one that uses it. A COMMAND_VARIABLES value is checked as written, like bash -c
        code: one holding another variable (GIT_SSH_COMMAND="ssh -i $KEY", which workers use) doesn't ask for that
        alone (Limits), while a git setting's value does, as #82 asks for -c core.sshCommand="$X"."""
        codes = [Word(value.text) for name in sorted(COMMAND_VARIABLES) for value in self.assigned.get(name, [])]
        return self.code_reasons(codes + environment_code(self.assigned), 1)

    def note_tower_merge(self, words: list[Word], index: int, top_level: bool) -> None:
        """Collect the `tower merge` that word INDEX of WORDS (tower or run-tower.py) runs, if it does (a subcommand
        from a variable may be merge), to judge at the end like gh pr merge. Its PR can be looked up only when tower
        runs as its own command (TOP_LEVEL; merge_reasons() checks nothing is chained), straight or from python with
        only python's switches, merge comes right after it, and at most a PR number follows."""
        position = self.tower_subcommand_at(words, index, "merge")
        if position is None:
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

    def tower_subcommand_at(self, words: list[Word], index: int, subcommand: str) -> int | None:
        """Index of the operand after word INDEX of WORDS (tower or run-tower.py) when it is SUBCOMMAND (merge,
        clean) or from a variable, else None. The options skipped to reach it are paid for (a path ending in /tower
        may be one: tower --x=/tower), and once it is found the whole command is too, characters included, since the
        caller reads the text of what launches tower as well as what follows it."""
        position = next_operand(words, index + 1)
        self.spend((len(words) if position is None else position) - index)
        if position is None or not (words[position].dynamic or words[position].text == subcommand):
            return None
        self.spend(scan_cost(words, 0, len(words)))  # launcher words' text is scanned, as well as what follows
        return position

    def tower_clean_reasons(self, words: list[Word], index: int, program: Program) -> list[str]:
        """The reason to ask for the `tower clean` that word INDEX of WORDS (tower or run-tower.py) runs, if it does
        (a subcommand from a variable may be clean) and cleanup asks. A dry run only checks, so it never asks: tower
        run as the command itself (bare, from python with only python's switches, or with uv run, so the words after
        it are all tower's, not another command's as in find -exec ... \\; -exec ... --dry-run), clean right after it,
        and the exact --dry-run among clean's options (before any --, which makes the words after it worktree
        names), with no word from a variable among them (one could be --). PROGRAM is the program the command runs
        (program_of)."""
        position = self.tower_subcommand_at(words, index, "clean")
        if position is None:
            return []
        launcher, after = words[program.at : index], words[position + 1 :]
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
        """The gates for a tower merge or clean: those of the repo it runs in, but the strictest when the line may
        point its gh and git calls at another repo (GH_REPO, GH_HOST, see git_repo_unknown), as for gh itself."""
        if (self.names_gh_env and self.session_policy.has_overrides) or self.git_repo_unknown():
            return self.strictest_reached()
        return self.gates_for([], [])

    def git_repo_unknown(self) -> bool:
        """Whether the line may point git at a repo the hook can't tell, with [repos] entries that make it matter: it
        names GIT_DIR, GIT_WORK_TREE or GIT_COMMON_DIR, or sets config the hook can't read (GIT_CONFIG_*, HOME), which
        can rewrite a remote's URL. gh and tower find their repo through git too."""
        return (self.names_git_env or self.git_config_from_env) and self.session_policy.has_overrides

    def push_mode(self, git_args: list[Word]) -> str:
        """The push key for git run with GIT_ARGS: the stricter of the session's and that of every repo the push
        reaches (a URL it pushes to, the folders git and the line point at); the strictest when git_repo_unknown."""
        if self.git_repo_unknown():
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
        parts = [os.path.expanduser(folder.text) for folder in git_folders]
        for base in {self.cwd, self.cd_folder}:
            # one join builds the path once; joining folder by folder copies it again each time (sudo -D x -D x ...)
            joined = os.path.join(base, *parts)
            if git_folders:
                folders.append(Word(joined, any(folder.dynamic for folder in git_folders)))
        folders = list({(folder.text, folder.dynamic): folder for folder in folders}.values())  # each one once
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
            """The gates for this gh command: the session's and those of every repo it names, or the strictest when
            the line may point it at another repo (GH_REPO, GH_HOST, git_repo_unknown). Only for a write, since
            finding them may run git."""
            if (self.names_gh_env and self.session_policy.has_overrides) or self.git_repo_unknown():
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


def runs_tower(index: int, program: Program) -> bool:
    """Whether word INDEX (naming tower or run-tower.py) is what its command runs: the program itself (or after a
    wrapper like env or sudo), or an argument of any program but git and those that only print or read
    (python3 run-tower.py, uv run tower), so `git -C ~/code/tower merge` and `echo tower merge` aren't tower merges.
    PROGRAM is the program the command runs (program_of)."""
    if program.runs(index):
        return True
    return program.at < index and program.name not in READ_ONLY_PROGRAMS | {"git"}


def gives_code(args: list[Word]) -> bool:
    """Whether an interpreter's ARGS give it inline code to run (-c, -e, --eval, ...), not only a script file."""
    return any(not arg.dynamic and CODE_OPTION.match(arg.text) for arg in args) or any(arg.dynamic for arg in args)


def program_name(word: Word) -> str:
    """The program WORD names, lower-cased since macOS finds GIT as git: its last path part, without zsh's "="
    prefix (=git is git's path) or a zsh brace group's "{"; "" if that part comes from a variable or command
    output."""
    name = os.path.basename(word.text).lstrip("={").lower()
    return "" if has_expansion(name) else name


def has_expansion(text: str) -> bool:
    """Whether TEXT, a word as written, holds a $ or backtick expansion, so its value is only known at run time."""
    return "$" in text or "`" in text


def program_position(words: list[Word]) -> int:
    """Index of the program a simple command runs: its first word that isn't a VAR=value assignment or a
    reserved word like `{` or `then` (len(words) if there is none)."""
    index = 0
    while index < len(words) and (ASSIGNMENT.match(words[index].text) or words[index].text in RESERVED_WORDS):
        index += 1
    return index


@dataclass(frozen=True)
class Program:
    """Where a simple command's program is and what it is called, found once per command (program_of): found again
    for each word, they cost a scan of the assignments before the program, or of a long program word, every time."""

    at: int  # index of the program word (program_position); len(words) if there is none
    name: str  # its program_name; "" if there is none

    def runs(self, index: int) -> bool:
        """Whether word INDEX is what the command runs, not an argument (bash in `grep bash`): the program itself, or
        any later word when the program is a wrapper like sudo, env or timeout."""
        return index == self.at or (self.at < index and self.name in WRAPPERS)


def program_of(words: list[Word]) -> Program:
    """The program a simple command's WORDS run, for checks that ask about each word (see Program)."""
    at = program_position(words)
    return Program(at, program_name(words[at]) if at < len(words) else "")


def scan_cost(words: list[Word], start: int, end: int) -> int:
    """What a check pays to read WORDS[START:END]: a unit per word plus their characters, since it scans their text
    (a few long words cost a scan far more than their count: python3 ... -c '<a megabyte of code>', a short-option
    cluster read letter by letter, or a long command word after a wrapper's options)."""
    return end - start + sum(len(words[i].text) for i in range(start, end))


def is_process_substitution(word: Word) -> bool:
    """Whether WORD is a <( ), >( ) or zsh =( ): a file name the shell makes up for a command's output."""
    return word.dynamic and word.text.startswith(("<(", ">(", "=("))


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


def check_git(args: list[Word], mode: Callable[[], str], config_from_env: bool) -> str | None:
    """Reason to ask for git run with ARGS: a push, judged by the policy's push mode (MODE is called only for a push,
    since finding it may run git), found past git's global options. CONFIG_FROM_ENV: the line sets variables that
    give git config the hook can't read (GIT_CONFIG_VARIABLE).

    A subcommand from a variable, command output or a `-c alias.*` set in the same command could be push. Config the
    hook can't read (from those variables, an include.path, a -c or --config-env key from a variable) could define
    an alias for push and make it force, so then a subcommand that isn't one of git's own (GIT_OWN_COMMANDS) counts
    as a push that may force. Any --config-env takes its value from the environment, so it may force too. A `!` alias
    runs shell code, which git_run_code hands to the caller to check like bash -c code, so it isn't a push here.
    """
    index = 0
    aliases: set[str] = set()
    unreadable = config_from_env
    config_forces = False  # -c remote.<name>.mirror/push, or an alias for push --force, can make a push delete or force
    # An alias that gives git its own options first (-c, --config-env) may do either too.
    while index < len(args) and args[index].text.startswith("-"):
        setting = config_setting(args, index)
        if setting is not None:
            key, _, value = setting.text.partition("=")
            key = key.lower()
            unreadable = unreadable or is_unreadable_config(key)
            # a ! alias's code is checked as code, but one that hands git's words on ("$@", $1) may push them
            alias_name = git_alias_name(key)
            if alias_name is not None and (not is_shell_alias(value) or POSITIONAL_PARAMETER.search(value)):
                aliases.add(alias_name)
                parts = alias_words(value)
                runs_more = parts is None or (bool(parts) and parts[0].startswith(("!", "-")))
                forcing_part = any(map(is_force_push_arg, parts or []))
                config_forces = config_forces or setting.dynamic or runs_more or forcing_part
            from_env = args[index].text.startswith("--config-env")
            config_forces = config_forces or from_env or bool(GIT_FORCING_CONFIG.fullmatch(key))
        index += 2 if args[index].text in GIT_OPTIONS_WITH_VALUE else 1
    if index >= len(args):
        return None
    subcommand = args[index]
    forces = config_forces or unreadable
    if subcommand.dynamic or subcommand.text.lower() in aliases:
        action = "git with a subcommand from a variable, command output or alias, which could be push,"
        return check_push(args[index + 1 :], mode(), action, forces)
    if subcommand.text != "push":
        if not unreadable or subcommand.text in GIT_OWN_COMMANDS:
            return None  # git ignores an alias named like one of its own commands
        action = "git under config it can't read (GIT_CONFIG_*, HOME, an include, a key from a variable), maybe a push,"
        return check_push(args[index + 1 :], mode(), action, forces)
    return check_push(args[index + 1 :], mode(), "git push", forces)


def git_alias_name(key: str) -> str | None:
    """The alias a git setting KEY defines, in lower case: NAME for alias.NAME, and for alias.NAME.command, the
    `[alias "NAME"] command = ...` form git 2.56 added (whose NAME may hold dots); None for any other key."""
    lowered = key.lower()
    if not lowered.startswith("alias."):
        return None
    return lowered[len("alias.") :].removesuffix(".command")


def is_shell_alias(value: str) -> bool:
    """Whether a git alias's VALUE runs shell code (!...) rather than git's own subcommand. git looks only at its first
    character; a leading space counts here too, which only makes more aliases checked as code."""
    return value.lstrip().startswith("!")


def alias_words(value: str) -> list[str] | None:
    """The words git splits a -c alias.*'s VALUE into, honoring quotes and backslashes as git does ("push '--force'"
    is push --force), or None when it can't be split (an unclosed quote)."""
    try:
        return shlex.split(value)
    except ValueError:
        return None


def is_unreadable_config(key: str) -> bool:
    """Whether a git setting's KEY hides what git does: it comes from a variable, includes a config file, or
    autocorrects a mistyped subcommand (GIT_HIDING_CONFIG)."""
    return has_expansion(key) or bool(GIT_HIDING_CONFIG.fullmatch(key.lower()))


def sets_git_config(words: list[Word], spend: Callable[[int], None]) -> bool:
    """Whether a simple command's WORDS may set a GIT_CONFIG_VARIABLE: an assignment before its program
    (GIT_CONFIG_COUNT=1 git push, or alone), one among the words of a wrapper like env or sudo, or a name that
    export, read or declare sets. A name only known at run time may be one: env $(cat vars) git push, export "$N=1",
    or an env that xargs, parallel or find run (xargs -I{} env {} git push). Other words only name one (echo
    GIT_CONFIG_COUNT=1, unset, a commit message). Each env's options read are paid for with SPEND."""
    program = program_position(words)
    if any(GIT_CONFIG_ASSIGNMENT.match(word.text) for word in words[:program]):
        return True
    name = program_name(words[program]) if program < len(words) else ""
    args = words[program + 1 :]
    if name in WRAPPERS:
        if any(GIT_CONFIG_ASSIGNMENT.match(word.text) for word in args):
            return True
        names = [program_name(word) for word in words]
        supplier = next((i for i in range(program, len(words)) if names[i] in ARGUMENT_SUPPLIERS), len(words))
        return any(
            i > supplier or env_hides_variables(words, i, spend)
            for i in range(program, len(words))
            if names[i] == "env"
        )
    return name in VARIABLE_SETTERS and any(GIT_CONFIG_NAME.match(word.text) or hides_name(word) for word in args)


def env_hides_variables(words: list[Word], index: int, spend: Callable[[int], None]) -> bool:
    """Whether the env at word INDEX of WORDS may set variables its words don't show: what it runs starts with a word
    only known at run time, which may expand to assignments (env $(cat vars) git push). (An env that xargs, parallel
    or find run may get assignments from them: sets_git_config counts that.) The options read are paid for with
    SPEND: this env may be another option's value (env -u env), and reading from each runs to the end of the line."""
    command_at = read_paid_wrapper_options(words, WRAPPER_SYNTAX["env"], index + 1, spend).command_at
    return command_at < len(words) and hides_name(words[command_at])


def hides_name(word: Word) -> bool:
    """Whether the variable name WORD gives (all of it, or what comes before its =) is only known at run time."""
    return has_expansion(word.text.split("=", 1)[0])


def config_setting(args: list[Word], index: int) -> Word | None:
    """The key=value setting git's global option at INDEX of ARGS gives, or None for another option: -c's value, or
    --config-env's name with its value taken from an environment variable (so the setting is marked dynamic)."""
    text = args[index].text
    following = args[index + 1] if index + 1 < len(args) else None
    if text == "-c":
        return following
    if text == "--config-env" and following is not None:
        spec = following.text
    elif text.startswith("--config-env="):
        spec = text.split("=", 1)[1]
    else:
        return None
    name, _, variable = spec.partition("=")
    return Word(f"{name}=${variable}", dynamic=True)


def variable_assignments(words: list[Word]) -> list[tuple[str, Word]]:
    """The variables a simple command's WORDS set, with their values: the NAME=value words before its program, and those
    among the arguments of a wrapper (env NAME=value git ...) or of export, declare and the like. A value with an
    expansion, or one added to (NAME+=value), is only known at run time, so it is dynamic."""
    program = program_position(words)
    found = words[:program]
    name = program_name(words[program]) if program < len(words) else ""
    if name in WRAPPERS or name in VARIABLE_SETTERS:
        found = found + words[program + 1 :]
    assignments: list[tuple[str, Word]] = []
    for word in found:
        match = ASSIGNMENT.match(word.text)
        if match is not None:
            added = match.group().endswith("+=")
            assignments.append((match.group().rstrip("+="), Word(word.text[match.end() :], word.dynamic or added)))
    return assignments


def command_scoped_names(words: list[Word]) -> set[str]:
    """The variables a simple command's WORDS set only for the program it runs, not for the line's later commands: the
    NAME=value words before a program (X=v cmd), and every one when the program is a wrapper (env X=v cmd)."""
    program = program_position(words)
    if program >= len(words):
        return set()
    scoped = words[program + 1 :] if program_name(words[program]) in WRAPPERS else []
    return {
        match.group().rstrip("+=") for word in [*words[:program], *scoped] if (match := ASSIGNMENT.match(word.text))
    }


def git_settings(args: list[Word], index: int, config_env: dict[str, list[Word]]) -> list[tuple[str, Word]]:
    """The key=value settings git's global option at INDEX of ARGS gives, as (key, value): -c's, or --config-env's with
    each value its variable may have when git starts (CONFIG_ENV; one only known at run time when the line gives it
    none). A value is dynamic when it holds an expansion, and so is any value whose key does (it may say anything)."""
    text = args[index].text
    if text == "--config-env" or text.startswith("--config-env="):
        following = args[index + 1].text if index + 1 < len(args) else ""
        key, _, variable = (text.partition("=")[2] if "=" in text else following).partition("=")
        return [(key, value) for value in config_env.get(variable, [Word(f"${variable}", dynamic=True)])]
    setting = config_setting(args, index) if text == "-c" else None
    return [] if setting is None else [split_setting(setting)]


def split_setting(setting: Word) -> tuple[str, Word]:
    """A key=value SETTING as (key, value), the value dynamic when it holds an expansion or its key does."""
    key, _, value = setting.text.partition("=")
    return key, Word(value, setting.dynamic and (has_expansion(key) or has_expansion(value)))


def git_key_kind(key: str) -> str:
    """What a git setting's KEY makes of its value, for setting_code: "open" (the key is only known at run time),
    "protocol" (it may let ext:: URLs run the command they name), "command" (git_key_runs_program), "alias", or ""."""
    lowered = key.lower()
    if has_expansion(key):
        return "open"
    if lowered in GIT_EXT_PROTOCOL_KEYS:
        return "protocol"
    if lowered.startswith("alias."):  # before the leaves: alias.cmd and alias.pager are aliases, not commands
        return "alias"
    return "command" if "." in lowered and git_key_runs_program(lowered) else ""


def setting_code(key: str, value: Word, rest: list[Word]) -> Word | None:
    """The shell code a git setting KEY=VALUE makes git run, or None. For a key naming a command (git_key_runs_program)
    it is VALUE, without the ! that credential.helper and submodule.<name>.update put before shell code; for a `!` alias
    it is the alias's code with REST, the words after git's subcommand, which git passes to it ("$@"). A key only known
    at run time may be either, so its setting counts as code only known at run time; so does one letting ext:: URLs run
    the command they name (GIT_EXT_PROTOCOL_KEYS, unless it says never), which the hook can't see. git runs that code
    through its own shell, so an expansion in it is only known at run time even when the line quotes it ('$X'), except
    the parameters an alias hands REST on with ("$@", $1)."""
    kind = git_key_kind(key)
    if kind == "open" or (kind == "protocol" and value.text.lower() != "never"):
        return Word(f"{key}={value.text}", dynamic=True)
    if kind == "command":
        code = value.text.removeprefix("!")
        return Word(code, value.dynamic or has_expansion(code))
    if kind == "alias" and (value.dynamic or is_shell_alias(value.text)):
        code = value.text.lstrip().removeprefix("!")
        expands = value.dynamic or has_expansion(POSITIONAL_PARAMETER.sub("", code))
        return Word(" ".join([code, *map(shell_text, rest)]), expands)
    return None


def git_run_code(
    args: list[Word],
    assigned: dict[str, list[Word]],
    spend: Callable[[int], None],
    config_env: dict[str, list[Word]],
    aliases: dict[str, list[list[str]]] | None = None,
) -> list[Word]:
    """The shell code that git, run with ARGS (its own arguments, subcommand included), runs from what the line says,
    each to be checked like bash -c code: a setting's (setting_code: -c, or --config-env from a variable git starts
    with, which CONFIG_ENV gives; ASSIGNED is every value the line gives each variable), and a subcommand's
    (subcommand_code: rebase --exec, submodule foreach, a `git config` write). A subcommand that is a non-! alias
    (ALIASES, by name, with every value the line may give it: through -c, --config-env or GIT_CONFIG_*, see
    environment_aliases) is read as each of them, its own options included, once per name, so a chain of them ends. Code
    only known at run time comes back dynamic. Each read of ARGS is paid for with SPEND, since an alias chain reads the
    rest of the line again."""
    spend(scan_cost(args, 0, len(args)) + len(aliases or {}))
    aliases = environment_aliases(assigned, spend) if aliases is None else dict(aliases)
    at = git_subcommand_at(args)
    rest = args[at + 1 :]
    codes: list[Word] = []
    index = 0
    while index < at:
        settings = git_settings(args, index, config_env)
        spend(len(settings))  # a --config-env gives one setting per value the line gives its variable
        for key, value in settings:
            code = setting_code(key, value, rest)
            alias = git_alias_name(key)
            parts = alias_words(value.text) if alias is not None and not value.dynamic else None
            if code is not None:
                codes.append(code)
            elif parts and alias is not None:  # every value counts, an overridden -c's too: the safe side
                aliases.setdefault(alias, []).append(parts)
        index += 2 if args[index].text in GIT_OPTIONS_WITH_VALUE else 1
    if at >= len(args) or args[at].dynamic:
        return codes  # a subcommand from a variable is a command built at run time (Limits)
    alternatives = aliases.pop(args[at].text.lower(), None)
    if alternatives:
        expanded = [
            git_run_code([Word(part) for part in parts] + rest, assigned, spend, config_env, aliases)
            for parts in alternatives
        ]
        return codes + [code for found in expanded for code in found]
    return codes + subcommand_code(args[at].text, rest)


def environment_aliases(assigned: dict[str, list[Word]], spend: Callable[[int], None]) -> dict[str, list[list[str]]]:
    """The non-! aliases the line gives through variables (ASSIGNED): GIT_CONFIG_KEY_<n> with GIT_CONFIG_VALUE_<n>,
    and GIT_CONFIG_PARAMETERS, by name, each with every value it may have, split as git splits them. A ! alias's code
    is environment_code's. Each key and value paired is paid for with SPEND before they are, since a padded line can
    give one number many of both."""
    found: dict[str, list[list[str]]] = {}
    numbers = {match.group(2) for name in assigned if (match := GIT_CONFIG_PAIR.fullmatch(name))}
    pairs: list[tuple[str, str]] = []
    for number in sorted(numbers):
        keys = {key.text for key in assigned.get(f"GIT_CONFIG_KEY_{number}", []) if not key.dynamic}
        names = {name for key in keys if (name := git_alias_name(key)) is not None}
        values = [value.text for value in assigned.get(f"GIT_CONFIG_VALUE_{number}", []) if not value.dynamic]
        spend(len(names) * len(values))
        pairs += [(name, value) for name in names for value in values]
    for parameters in assigned.get("GIT_CONFIG_PARAMETERS", []):
        try:
            parts = [] if parameters.dynamic else shlex.split(parameters.text)
        except ValueError:  # environment_code counts it as code the hook can't see
            parts = []
        spend(len(parts))
        settings = [part.partition("=") for part in parts]
        pairs += [(name, value) for key, _, value in settings if (name := git_alias_name(key)) is not None]
    for name, value in pairs:
        words = None if is_shell_alias(value) else alias_words(value)
        if words:
            found.setdefault(name, []).append(words)
    return found


def subcommand_code(subcommand: str, args: list[Word]) -> list[Word]:
    """The shell code git's SUBCOMMAND, run with ARGS (the words after it), runs: its GIT_CODE_OPTIONS' values (rebase
    --exec), the settings a clone writes and uses (-c, --config) or a `git config` sets that name a command, and the
    command submodule foreach or bisect run is given (GIT_COMMAND_VERBS). foreach runs one word through the shell and
    more as they are; bisect run quotes each word for the shell; either way only a program word from a variable makes
    the command unknown, since an argument's value stays one word. An option's value and a one-word foreach command go
    to git's shell, so an expansion in them is only known at run time even when the line quotes it (--exec '$CMD')."""
    long_names, letters = GIT_CODE_OPTIONS.get(subcommand, (set(), set()))
    attached_only = subcommand in GIT_ATTACHED_CODE_OPTIONS
    codes = [
        Word(code.text, code.dynamic or has_expansion(code.text))
        for code in option_values(args, long_names, letters, attached_only)
    ]
    settings: list[tuple[str, Word]] = []
    if subcommand == "clone":
        settings = [split_setting(setting) for setting in option_values(args, {"config"}, {"c"})]
    elif subcommand == "config" and (written := git_config_write(args)) is not None:
        key, value = written
        settings = [(f"${key.text}" if key.dynamic else key.text, value)]
    elif subcommand == "config" and renames_config_section(args):
        codes.append(Word("", dynamic=True))  # the settings it moves into alias.* or core.* aren't on the line
    codes += [code for key, value in settings if (code := setting_code(key, value, [])) is not None]
    verb_at = next_operand(args, 0)
    verb = GIT_COMMAND_VERBS.get(subcommand)
    if verb is None or verb_at is None or not (args[verb_at].dynamic or args[verb_at].text == verb):
        return codes
    command = args[verb_at + 1 :]
    if subcommand == "submodule":  # foreach's own options (--recursive, -q) come first, and a "--" ends them
        while command and command[0].text.startswith("-"):
            first, command = command[0], command[1:]
            if first.text == "--":
                break
        if len(command) == 1:
            return codes + [Word(command[0].text, command[0].dynamic or has_expansion(command[0].text))]
    return codes + ([Word(" ".join(map(shell_text, command)), command[0].dynamic)] if command else [])


def renames_config_section(args: list[Word]) -> bool:
    """Whether a `git config` run with ARGS (the words after `config`) renames a section (--rename-section, or the
    rename-section subcommand), which can turn settings saved under a harmless name into aliases, a remote's push
    settings or a pager the hook never saw written."""
    first = next_operand(args, 0)
    return any(git_long_option(arg.text, "--rename-section") for arg in args) or (
        first is not None and args[first].text == "rename-section"
    )


def git_long_option(text: str, name: str) -> bool:
    """Whether the word TEXT gives git's long option NAME (--name, with or without =value) by any start of it: git
    takes an unambiguous abbreviation (--typ for --type) and refuses an ambiguous one, so every start counts."""
    given = text.partition("=")[0]
    return len(given) > 2 and given.startswith("--") and name.startswith(given)


def option_values(args: list[Word], long_names: set[str], letters: set[str], attached_only: bool = False) -> list[Word]:
    """The values ARGS give the options LONG_NAMES (--name=VALUE or --name VALUE, by any start of the name, which git
    accepts while it is unambiguous) and LETTERS (-x VALUE, -xVALUE, or the first such letter in a cluster: -ix VALUE),
    read up to a "--"; only the attached ones when ATTACHED_ONLY (an optional value). A value attached to its option's
    word is dynamic if that word is."""
    found: list[Word] = []
    index = 0
    while index < len(args):
        word = args[index]
        index += 1
        if word.text == "--":
            break
        if word.text.startswith("--"):
            name, equals, attached = word.text[2:].partition("=")
            matches, has_value = bool(name) and any(option.startswith(name) for option in long_names), bool(equals)
        elif word.text.startswith("-"):
            at = next((i for i in range(1, len(word.text)) if word.text[i] in letters), None)
            attached = word.text[at + 1 :] if at is not None else ""
            matches, has_value = at is not None, bool(attached)
        else:
            continue
        if not matches:
            continue
        if has_value:
            found.append(Word(attached, word.dynamic))
        elif index < len(args) and not attached_only:
            found.append(args[index])
            index += 1
    return found


def git_config_write(args: list[Word]) -> tuple[Word, Word] | None:
    """The key and value a `git config` run with ARGS (the words after `config`) sets: `git config [options] KEY VALUE`
    or `git config set [options] KEY VALUE`; None when it only reads, removes or edits settings, or names no value."""
    operands: list[Word] = []
    index = 0
    while index < len(args):
        text = args[index].text
        if text in GIT_CONFIG_NON_WRITES and (text.startswith("-") or not operands):
            return None
        if any(git_long_option(text, name) for name in GIT_CONFIG_NON_WRITES if name.startswith("--")):
            return None  # an abbreviation git accepts (--get-r, --remove-s)
        takes_value = text in GIT_CONFIG_OPTIONS_WITH_VALUE or (
            "=" not in text and any(git_long_option(text, name) for name in GIT_CONFIG_OPTIONS_WITH_VALUE)
        )
        index += 2 if takes_value else 1
        if not text.startswith("-"):
            operands.append(args[index - 1])
    operands = operands[1:] if operands and operands[0].text == "set" else operands
    return (operands[0], operands[1]) if len(operands) >= 2 else None


def environment_code(assigned: dict[str, list[Word]]) -> list[Word]:
    """The shell code git runs from the settings the line gives through variables (ASSIGNED: every value it gives each),
    as setting_code finds it: GIT_CONFIG_KEY_<n> with GIT_CONFIG_VALUE_<n> (either may be missing from the line, so
    any key or value), and GIT_CONFIG_PARAMETERS ('key'='value' ...). A GIT_ALLOW_PROTOCOL letting ext:: URLs run the
    command they name counts as code the hook can't see. Linear in the values given, however many share a number: a
    number's values are judged by one key of each kind (git_key_kind) among its keys, not by every key."""
    codes: list[Word] = []
    unknown = [Word("", dynamic=True)]  # a key or value the line doesn't give comes from the environment
    numbers = {match.group(2) for name in assigned if (match := GIT_CONFIG_PAIR.fullmatch(name))}
    for number in sorted(numbers):
        keys = ["$KEY" if key.dynamic else key.text for key in assigned.get(f"GIT_CONFIG_KEY_{number}", unknown)]
        judging = {git_key_kind(key): key for key in keys}.values()
        for value in assigned.get(f"GIT_CONFIG_VALUE_{number}", unknown):
            codes += [code for key in judging if (code := setting_code(key, value, [])) is not None]
    for parameters in assigned.get("GIT_CONFIG_PARAMETERS", []):
        settings = [("$GIT_CONFIG_PARAMETERS", parameters)]  # one only known at run time may say anything
        if not parameters.dynamic:
            try:
                settings = [split_setting(Word(part)) for part in shlex.split(parameters.text)]
            except ValueError:  # an unclosed quote: git refuses it too, but the hook can't tell what it says
                settings = [("$GIT_CONFIG_PARAMETERS", Word(parameters.text, dynamic=True))]
        codes += [code for key, value in settings if (code := setting_code(key, value, [])) is not None]
    for protocols in assigned.get("GIT_ALLOW_PROTOCOL", []):
        if protocols.dynamic or "ext" in protocols.text.split(":"):
            codes.append(Word(f"GIT_ALLOW_PROTOCOL={protocols.text}", dynamic=True))
    return codes


@dataclass(frozen=True)
class WrapperSyntax:
    """How a wrapper (env, sudo) reads its options: which take a value, name a folder to run in, or hold more of its
    arguments."""

    with_value: set[str]  # its options that take a value: the rest of a short one's word (-Cdir) or the next word
    chdir: set[str]  # the options among them that name the folder
    split: frozenset[str] = frozenset()  # those whose value it splits into more of its own arguments (env -S)
    flags: frozenset[str] = frozenset()  # long flags that are a prefix of one that takes a value (sudo --login)


@dataclass
class WrapperOptions:
    """What a wrapper's options say, read up to the command it runs."""

    chdirs: list[Word] = field(default_factory=list)  # the folders its folder options name, in order
    split: Word | None = None  # env -S's value, which env splits into more of its own arguments
    split_at: int = 0  # the index of the word holding -S (its value ends at command_at)
    command_at: int = 0  # the index of the first word after its options: the command it runs


# The wrappers whose options the hook reads, to find where and what they run.
WRAPPER_SYNTAX = {
    "env": WrapperSyntax(ENV_OPTIONS_WITH_VALUE, {"-C", "--chdir"}, frozenset({"-S", "--split-string"})),
    "sudo": WrapperSyntax(SUDO_OPTIONS_WITH_VALUE, {"-D", "--chdir"}, flags=frozenset({"--login"})),
}


def wrapper_folders(words: list[Word], program: Program, spend: Callable[[int], None]) -> list[Word]:
    """The folders a simple command's WORDS tell env (-C, --chdir) or sudo (-D, --chdir) to run its command in, in
    order (each relative to the one before). A -C inside env -S's string is found when that is checked. PROGRAM is
    the program the whole command runs (program_of); WORDS may stop short of its end (the words before git). Each
    wrapper's options read are paid for with SPEND: an env or sudo word may be another option's value (sudo -u sudo),
    and reading from each runs to the end of the line."""
    found: list[Word] = []
    for index in range(program.at, len(words)):
        name = program_name(words[index]) if program.runs(index) else ""
        if name in WRAPPER_SYNTAX:
            options = read_paid_wrapper_options(words, WRAPPER_SYNTAX[name], index + 1, spend)
            found += options.chdirs
    return found


def env_split_line(args: list[Word]) -> str | None:
    """The rest of env's command line when env, run with ARGS, has -S/--split-string (env -S 'git push'), or None:
    env splits that string into more of its own arguments, options included (-S'-C ../repo git push'), in its
    place."""
    options = read_wrapper_options(args, WRAPPER_SYNTAX["env"])
    if options.split is None:
        return None
    try:
        parts = shlex.split(options.split.text.replace("\\_", " "))  # env reads \_ as a space between words
        split = [Word(part, has_expansion(part)) for part in parts]
    except ValueError:  # an unclosed quote: checking the string as written reports it can't be split
        return options.split.text
    before, after = args[: options.split_at], args[options.command_at :]
    return " ".join(map(shell_text, [*before, *split, *after]))


def shell_text(word: Word) -> str:
    """WORD written back as shell text, to be split again, so a '#' stays a word rather than starting a comment:
    single-quoted, or double-quoted when it holds an expansion, which must still be seen as one."""
    if not word.dynamic:
        return shlex.quote(word.text)
    return '"' + word.text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def read_paid_wrapper_options(
    words: list[Word], wrapper: WrapperSyntax, start: int, spend: Callable[[int], None]
) -> WrapperOptions:
    """read_wrapper_options from word START of WORDS, paid for with SPEND: the words it read and the one it stopped at
    (what the wrapper runs), characters included, for the checks that read them again from every env or sudo word."""
    options = read_wrapper_options(words, wrapper, start)
    spend(scan_cost(words, start, min(options.command_at + 1, len(words))))
    return options


def read_wrapper_options(args: list[Word], wrapper: WrapperSyntax, start: int = 0) -> WrapperOptions:
    """What WRAPPER's options among its ARGS, from START, say, read up to the command it runs (or to env -S's value,
    after which env reads on from the split string); the indexes it gives count in ARGS. An option that takes a value
    takes the rest of its word (--chdir=DIR, -CDIR, -iCDIR in a cluster) or else the next word; a long one may be cut
    short (--ch), as getopt allows."""
    options = WrapperOptions()
    index = start
    while index < len(args):
        word = args[index]
        if ASSIGNMENT.match(word.text) or word.text == "-":  # env's NAME=value, and - (an empty environment)
            index += 1
            continue
        if word.text == "--":
            index += 1
            break
        if not word.text.startswith("-"):
            break
        start = index
        index += 1
        if word.text.startswith("--"):
            given, _, attached = word.text.partition("=")
            option = long_option(wrapper, given)
            has_value = "=" in word.text
        else:  # a cluster of short options: the first one that takes a value takes the rest of the word
            letter = next((i for i in range(1, len(word.text)) if f"-{word.text[i]}" in wrapper.with_value), None)
            option = None if letter is None else f"-{word.text[letter]}"
            attached = "" if letter is None else word.text[letter + 1 :]
            has_value = bool(attached)
        if option is None:
            continue
        if has_value:
            value = Word(attached, word.dynamic)
        elif index < len(args):
            value = args[index]
            index += 1
        else:
            break
        if option in wrapper.chdir:
            options.chdirs.append(value)
        if option in wrapper.split:
            options.split, options.split_at = value, start
            break
    options.command_at = index
    return options


def long_option(wrapper: WrapperSyntax, given: str) -> str | None:
    """Which of WRAPPER's options that take a value the long option GIVEN stands for: GIVEN itself, or the one it is
    a prefix of (--ch for --chdir), as getopt allows. When it is a prefix of several, a folder or split option wins;
    None when it names none of them (a flag)."""
    if given in wrapper.with_value:
        return given
    if given in wrapper.flags:  # getopt reads an exact name as itself, not as the start of a longer one
        return None
    matches = sorted(option for option in wrapper.with_value if option.startswith("--") and option.startswith(given))
    return next((option for option in matches if option in wrapper.chdir | wrapper.split), next(iter(matches), None))


def git_repos(args: list[Word]) -> tuple[list[Word], list[Word]]:
    """The repos a git push run with ARGS (git's own arguments, subcommand included) reaches besides the session's
    checkout: (repo URLs: from -c or --config-env remote.*.url/pushurl and url.*.insteadOf settings, and the push's
    repository when that is a URL or a variable; folders: -C, --git-dir, --work-tree, in order). Only called for a
    push, or for a subcommand that may be one (an alias, a variable), so the words after the subcommand are read as
    push's."""
    named: list[Word] = []
    folders: list[Word] = []
    index = 0
    while index < len(args) and args[index].text.startswith("-"):
        text = args[index].text
        value = args[index + 1] if index + 1 < len(args) else None
        setting = config_setting(args, index)
        if text in ("-C", "--git-dir", "--work-tree") and value is not None:
            folders.append(value)
        elif text.startswith(("--git-dir=", "--work-tree=")):
            folders.append(Word(text.split("=", 1)[1], args[index].dynamic))
        elif setting is not None:
            named += config_repos(setting)
        index += 2 if text in GIT_OPTIONS_WITH_VALUE else 1
    return named + push_destination(args[index + 1 :]), folders


def config_repos(setting: Word) -> list[Word]:
    """The repo URLs a `git -c` SETTING (key=value) points git at: remote.<name>.url or .pushurl's value, and both
    sides of url.<base>.insteadOf / pushInsteadOf; none for any other setting. A key from a variable, or an included
    file, could set any of them, so its value counts as a URL from a variable."""
    key, _, value = setting.text.partition("=")
    if is_unreadable_config(key):
        return [Word(value, dynamic=True)]
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
    program = program_of(words)
    for index in range(len(words)):
        if program_name(words[index]) in ("cd", "pushd") and program.runs(index):
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
    auth token, gh auth status -t/--show-token (or -t in a cluster), gh auth git-credential get (or an operation from a
    variable: git runs the saved helper with get, which the hook doesn't see), gh config get oauth_token (or a key from
    a variable, which may be it)."""
    if group == "auth" and name == "git-credential":
        return any(flag.text == "get" or flag.dynamic for flag in flags)
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
# Edit/Write: CLAUDE.local.md and Claude Code's settings files, the user's and any project's (enabledPlugins or
# disableAllHooks there turns this hook off); tower's own files are is_tower_file's.
PROTECTED_PATH = re.compile(r"(?:^|/)CLAUDE\.local\.md$|(?:^|/)\.claude/settings(?:\.local)?\.json$", re.IGNORECASE)
# tower.conf in a .claude folder, or anything under a .claude/tower folder (rules, policy file, event log), wherever
# that .claude folder is: a home folder can be anywhere (tests use temp ones).
TOWER_PATH = re.compile(r"/\.claude/tower\.conf$|/\.claude/tower/", re.IGNORECASE)
# Bash: naming a rule file asks even for a read.
RULE_FILE_MENTION = re.compile(r"CLAUDE\.local\.md|\.claude/tower/rules", re.IGNORECASE)
# Bash: the names of tower.conf and the policy file. A path ending in one counts only when it resolves to tower's
# (see names_tower_file), so a commit message or a fixture in a temp folder doesn't.
TOWER_FILE_NAME = re.compile(r"tower\.conf|policy\.toml", re.IGNORECASE)
# Bash: the rest of ~/.claude/tower/ (event log, the folder itself) and Claude Code's settings files, by their text.
TOWER_FOLDER_MENTION = re.compile(
    r"\.claude/tower(?![\w.-])|\.claude/settings(?:\.local)?\.json"
    r"|\.claude/[^/\s]*[{*?[]",  # a glob or brace expansion right under a .claude folder (.claude/{settings.json,x})
    re.IGNORECASE,
)
# Bash, for a command that couldn't be checked, where nothing can be resolved: any of the above by its text. Tower's
# files may be read by READ_ONLY_PROGRAMS.
TOWER_FILE_MENTION = re.compile(f"{TOWER_FILE_NAME.pattern}|{TOWER_FOLDER_MENTION.pattern}", re.IGNORECASE)
# Characters that end the path a tower file name is the last part of, looking back from the name: spaces, quotes,
# an option's = and the punctuation prose and code put around a path ("Fix: tower.conf", open('tower.conf')).
PATH_DELIMITERS = " \t\r\n'\"=:,;(<>|&"
# What leaves a path's value to run time: an expansion, a command's output, a glob or brace expansion, or a ~ still
# starting a part after expanduser (a zsh named folder, or a user it can't find).
UNRESOLVABLE = re.compile(r"[$`(){}*?[\]]|(?:^|/)~")
# Programs whose own code (an awk program, vim's commands) can build a path at run time, like LANGUAGE_RUNNER's.
PATH_BUILDERS = {"awk", "gawk", "mawk", "nawk", "vim", "vi", "nvim", "ex", "view", "ed", "emacs"}
# A line that may make a link (ln) can point a path at tower's files before the command that writes through it runs.
LINK_MENTION = re.compile(r"(?<![\w.-])ln(?![\w.-])")
# Bash: a word that is a .claude folder itself, which a copy, download or unpack can put a settings file into
# (cp settings.json ~/.claude/); only READ_ONLY_PROGRAMS may name it. A whole word, or an option's attached value
# (--output-dir=.claude, -C.claude), so prose naming it doesn't count.
CLAUDE_FOLDER = re.compile(r"(?:\S*[/=]|-[A-Za-z]+)?\.claude", re.IGNORECASE)
# Programs that read the files given to them and write only to standard output, shared by the two reader sets below so
# they can't drift. FILE_READERS adds a couple more; READ_ONLY_PROGRAMS adds programs that read no file at all.
BASE_READERS = {"cat", "grep", "egrep", "fgrep", "head", "tail", "wc", "ls", "stat", "file", "diff", "cmp"}
# Programs that can't write a file except through a redirection (BASE_READERS plus ones that read no file).
READ_ONLY_PROGRAMS = BASE_READERS | {"echo", "printf", "test", "[", "true", ":", "jq"}
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
# The policy module's repo path, named in interpreter/awk/sed code (open('src/tower/policy.py', 'w')); its folder
# holds "tower", so a mention of it is tower's.
POLICY_MODULE_MENTION = re.compile(r"src/tower/policy\.py", re.IGNORECASE)
# Interpreter code that uses tower's policy module, or runs `tower policy use` (not a path that merely holds both words).
POLICY_CODE = re.compile(
    r"from\s+tower(?:\.\w+)*\s+import\b[^\n;]*\bpolicy\b|\btower\.policy\b|\bimport\s+tower\b|\btower\s+policy\s+use\b"
)
# Programs whose code can run setup.sh wherever it names it: PATH_BUILDERS' (awk's system()) and sed's (GNU's e).
SETUP_CODE_PROGRAMS = PATH_BUILDERS | {"sed"}
# Programs that only read the files given to them and write to standard output, so a setup.sh or policy-module path
# among their arguments is read, not run. BASE_READERS plus nl and od; not echo/printf (they don't read files, and
# printf -v assigns a variable), nor awk/sed (they run a script of their own), nor xxd (`xxd in out` writes its second
# operand).
FILE_READERS = BASE_READERS | {"nl", "od"}
# git subcommands that only read, stage or show the files their operands name. grep (its -O opens files in a pager),
# commit (its -F/-t go to the editor) and the rest are left out, so they ask: a clean git line with one of these and a
# setup.sh operand is the only git read that passes. (rg and ag aren't here: ripgrep's --pre runs a program per file,
# so a search tool needs its own option filtering, like git's, before it could be trusted.)
GIT_FILE_READERS = {"add", "blame", "diff", "log", "ls-files", "show", "status"}
# git diff's --output=<file> / --output <file>, which writes its output to a file (a copy of the read). The program
# routes that run a program on the file (an external diff, a textconv, a pager) come from config or the environment,
# which git_runs_program covers, so they aren't options to match here; grep's -O is moot (grep isn't a read subcommand).
# Environment variables, assigned on the line, that name a program git runs or a folder it runs helpers from: an
# external diff, a pager, an editor, or the exec path.
GIT_RUN_PROGRAM_ENV = re.compile(rf"^(?:{'|'.join(sorted(COMMAND_VARIABLES | {'GIT_EXEC_PATH'}))})=")
# The last part of a git config key that names a program or shell command git runs: diff and filter drivers
# (diff.external, diff.<driver>.command, filter.<name>.clean), core.sshCommand and its kin, credential helpers, gpg's
# program, merge drivers, the tools difftool, mergetool, the browser, man and trailers run (*.cmd), send-email's
# commands, and what a fetch or push runs on the other side (remote.<name>.uploadpack); any pager.* and
# submodule.<name>.update are named in git_key_runs_program.
GIT_PROGRAM_KEY_LEAVES = {
    *("external", "command", "textconv", "pager", "clean", "smudge", "process", "editor"),
    *("sshcommand", "fsmonitor", "askpass", "gitproxy", "alternaterefscommand", "packobjectshook", "difffilter"),
    *("helper", "program", "driver", "cmd", "defaultkeycommand", "tunnel"),
    *("sendmailcmd", "tocmd", "cccmd", "headercmd", "uploadpack", "receivepack"),
}
# tower's policy module, by the path it has in the repo (src/tower/policy.py). A write to it, or any naming of it that
# isn't a plain read, asks (#100). A bare basename (policy.py), which many repos have, is not it.
POLICY_MODULE_PATH = os.path.join("src", "tower", "policy.py")
# sed's options that take no value and can't make it edit a file, write one or read its script from one, GNU's and
# BSD's; -e gives a script (SED_EXPRESSION); any other (-i edits in place, -f reads a script file, GNU's -l takes a
# value) makes sed unsafe, so it asks.
SED_SWITCHES = re.compile(
    r"-[nErsuz]+|--(?:quiet|silent|regexp-extended|separate|unbuffered|null-data|posix|debug|sandbox)"
)
# sed's -e/--expression, which gives a script: a short cluster ending in e with the script attached (-e1p, -ne1p) or
# in the next word (-e, -ne), or the long form (--expression=1p, --expression 1p). group 1 or 2 is the attached
# script, empty/None when it is the next word.
SED_EXPRESSION = re.compile(r"-[nErsuz]*e(.*)|--expression(?:=(.*))?")
# One sed statement that only prints, deletes or quits lines picked by number, $ or a /regex/ (1,20p; /^def /p; 5q;
# /a;b/p). A regex may hold an escaped delimiter (\/). sed_statements splits the script first, so matching one
# statement never backtracks over a long script.
SED_ADDRESS = r"(?:\d+|\$|/(?:\\.|[^/\n])*/)"
SED_PRINT_STATEMENT = re.compile(rf"\s*(?:{SED_ADDRESS}(?:,{SED_ADDRESS})?)?[pdq=]\s*")


def check_protected_edit(file_path: str) -> str | None:
    """Reason to ask for an edit tool call that targets a personal rule file or tower's settings, by its own path or
    the one its symlinks resolve to (a link to ~/.claude reaches tower's files under another name)."""
    path = os.path.expanduser(file_path)
    names = (os.path.normpath(path), os.path.realpath(path))
    if any(PROTECTED_PATH.search(candidate) for candidate in names) or is_tower_file(path):
        return f"edits a personal rule file, tower's settings or Claude Code's ({file_path})"
    return None


def is_tower_file(path: str) -> bool:
    """Whether PATH (absolute, or relative to this process's folder), or what its symlinks resolve to, is one of
    tower's files: a tower.conf in a .claude folder, anything under a .claude/tower folder, or the tower.conf and the
    folder tower really reads when TOWER_CONF or TOWER_DIR moves them. Case-insensitive, like macOS's disk."""
    conf = os.path.realpath(os.path.expanduser(os.environ.get("TOWER_CONF") or "~/.claude/tower.conf")).lower()
    folder = os.path.realpath(os.path.expanduser(os.environ.get("TOWER_DIR") or "~/.claude/tower")).lower()
    for candidate in {os.path.normpath(path).lower(), os.path.realpath(path).lower()}:
        if TOWER_PATH.search(candidate) or candidate == conf or candidate.startswith(folder + "/"):
            return True
    return False


def names_tower_file(
    text: str, session_folders: list[Word], argument_folders: list[Word], spend: Callable[[int], None]
) -> bool:
    """Whether TEXT, a word or a heredoc body, may name tower.conf or the policy file: some path in it ending in one's
    name is one of tower's files (is_tower_file), as written (from each of SESSION_FOLDERS, the session's and those
    the line cds into, when relative) or put into one of ARGUMENT_FOLDERS under its own name or relative path (a copy's
    destination, git -C, tar -C: see folder_arguments). It may also be when its value is only known at run time:
    UNRESOLVABLE characters, relative while a session folder is from a variable, or with an argument folder from one.
    The path is the text from the nearest PATH_DELIMITERS before the name, so a name in prose (a commit message) is a
    file in the session's folder. A name followed by more (tower.conf.bak) counts as the name: asking is the safe
    side. Each path looked up is paid for with SPEND."""
    start = scanned = 0
    for match in TOWER_FILE_NAME.finditer(text):
        # scan each character once: a delimiter between the last name and this one starts this path after it
        cut = max(text.rfind(char, scanned, match.start()) for char in PATH_DELIMITERS)
        start, scanned = (cut + 1 if cut >= 0 else start), match.end()
        path = os.path.expanduser(text[start : match.end()])
        relative = not os.path.isabs(path)
        spend(len(session_folders) + len(argument_folders))  # reading them for one from a variable, joining onto each
        if UNRESOLVABLE.search(path) or any(unresolvable(folder) for folder in argument_folders):
            return True
        if relative and any(unresolvable(folder) for folder in session_folders):
            return True
        candidates = {os.path.join(folder.text, path) for folder in session_folders}  # one, for an absolute path
        for folder in argument_folders:
            candidates |= {os.path.join(folder.text, os.path.basename(path))}
            candidates |= {os.path.join(folder.text, path)} if relative else set()
        for candidate in candidates:
            spend(FILE_LOOKUP_COST)
            if is_tower_file(candidate):
                return True
    return False


def unresolvable(folder: Word) -> bool:
    """Whether FOLDER's value is only known at run time: from a variable, or one the shell expands (see UNRESOLVABLE)."""
    return folder.dynamic or bool(UNRESOLVABLE.search(folder.text))


def folder_arguments(words: list[Word], bases: list[Word], spend: Callable[[int], None]) -> list[tuple[Word, Word]]:
    """Each of a command's argument WORDS that may be a folder its program resolves a relative name from (git -C DIR,
    tar --directory=DIR, a copy's destination), paired with that folder: an existing folder from one of BASES (the
    word itself, the value after its = or a short option's attached value), or the word itself when it comes from a
    variable or the shell expands it (a glob), so no name can be resolved. Each folder test is paid for with SPEND."""
    known = [base.text for base in bases if not base.dynamic]
    found: list[tuple[Word, Word]] = []
    for word in words:
        if word.dynamic or EXPANDING.search(word.text):
            found.append((word, Word(word.text, dynamic=True)))
            continue
        short_value = word.text[2:] if word.text.startswith("-") and not word.text.startswith("--") else ""
        values = {word.text, word.text.partition("=")[2], short_value} - {""}
        for value in values:
            for base in known:
                spend(FILE_LOOKUP_COST)
                path = os.path.join(base, os.path.expanduser(value))
                if os.path.isdir(path):
                    found.append((word, Word(path)))
    return found


def protected_reasons(
    command: SimpleCommand,
    cwd: str,
    folders: list[Word],
    spend: Callable[[int], None],
    by_text: bool,
    single_command: bool,
    git_may_run_program: bool,
) -> list[str]:
    """Reasons to ask because COMMAND may change tower's policy (see changes_policy, which SINGLE_COMMAND and
    GIT_MAY_RUN_PROGRAM go to), or names a rule file or one of tower's files, judged on its words
    and redirect targets with quotes removed and paths normalized. A rule file asks even when only read. tower.conf,
    the policy file and the rest of ~/.claude/tower may be read by a read-only program; a redirect into them, or any
    other program naming them, asks, and so does a write to a path under .claude whose text is only known at run time
    ($HOME, $'...'), and so does a non-read program naming a .claude folder itself. A path ending in tower.conf or
    policy.toml names them only when it resolves to them from CWD, the session's folder (see changes_policy), or one
    of FOLDERS the line cds into (see names_tower_file); in code for an interpreter or a PATH_BUILDERS program, which
    can build any path, or when BY_TEXT (the line may make a link), the name alone does. Each lookup on disk is paid
    for with SPEND, which raises TooComplex once the budget is gone."""
    if changes_policy(command, cwd, single_command, git_may_run_program):
        return [POLICY_COMMAND_REASON]
    if turns_tower_off(command.words):
        return [TOWER_PLUGIN_REASON]
    words = [os.path.normpath(word.text) for word in command.words if word.text]
    texts = words + [os.path.normpath(redirect.target.text) for redirect in command.redirects]
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
    names = [program_name(word) for word in command.words]
    # The first interpreter is given every later word too, so whether one gets code is one look after it (looking
    # after each one is quadratic, and runs before any of it is paid for)
    interpreter_at = next((i for i in range(program, len(names)) if LANGUAGE_RUNNER.fullmatch(names[i])), len(names))
    code_words = command.words[interpreter_at + 1 :]
    gets_code = interpreter_at < len(names) and bool(command.stdin_text or gives_code(code_words))
    runs_code = gets_code or any(name in PATH_BUILDERS for name in names[program:])
    judged_by_text = runs_code or by_text
    session = [Word(cwd), *folders]
    targets = [(redirect.writes, redirect.target.text) for redirect in command.redirects]
    named: list[tuple[Word | None, str]] = [(word, word.text) for word in command.words if word.text]
    named += [(None, target) for _, target in targets] + [(None, command.stdin_text or "")]
    needs_resolving = not judged_by_text and any(TOWER_FILE_NAME.search(text) for _, text in named)
    arguments = folder_arguments(command.words[program + 1 :], session, spend) if needs_resolving else []

    def names_tower(own: Word | None, text: str) -> bool:
        """Whether TEXT, which is word OWN (None for a redirect target or standard input), names tower.conf, the policy
        file or the rest of tower's folder, judged against the folders the command's other arguments name."""
        if TOWER_FOLDER_MENTION.search(os.path.normpath(text)):
            return True
        if not TOWER_FILE_NAME.search(text):
            return False
        spend(len(arguments))  # gathering the folders, before names_tower_file pays to look each one up
        others = [folder for word, folder in arguments if word is not own]
        return judged_by_text or names_tower_file(text, session, others, spend)

    if any(writes and names_tower(None, target) for writes, target in targets):
        return [TOWER_FILE_REASON]
    rest = [(own, text) for own, text in named if own is not None] + [(None, command.stdin_text or "")]
    rest += [(None, target) for writes, target in targets if not writes]  # write targets were judged just above
    if not any(names_tower(own, text) for own, text in rest):
        return []
    return [] if reads_only else [TOWER_FILE_REASON]  # only assignments (their $( ) are checked alone), or a read


def protected_repo_file(text: str, cwd: str) -> bool:
    """Whether TEXT is a path to tower's setup.sh or its policy module (src/tower/policy.py). setup.sh is tower's when
    its own path or the session's folder CWD holds "tower"; the policy module is known by its repo path, which holds
    "tower" already, as written (so `git diff -- src/tower/policy.py` counts) or absolute. A bare basename such as
    policy.py, which many repos have, is not it."""
    path = os.path.normpath(os.path.expanduser(text)).lower()
    if os.path.basename(path) == "setup.sh" and ("tower" in cwd.lower() or "tower" in path):
        return True
    module = POLICY_MODULE_PATH.lower()
    return path == module or path.endswith(os.sep + module)


def names_protected_file(text: str, cwd: str) -> bool:
    """Whether a word TEXT names setup.sh or the policy module, as the whole word (./setup.sh), an option's attached
    value (git diff --output=setup.sh, dd of=setup.sh) or a short option's (-osetup.sh). Checking the value catches a
    write whose target is glued to its flag, which protected_repo_file alone would read as the word --output=setup.sh."""
    candidates = [text, text.partition("=")[2]]
    if text.startswith("-") and not text.startswith("--"):
        candidates.append(text[2:])
    return any(part and protected_repo_file(part, cwd) for part in candidates)


def changes_policy(command: SimpleCommand, cwd: str, single_command: bool, git_may_run_program: bool) -> bool:
    """Whether a simple COMMAND may change tower's policy: write to setup.sh or the policy module, run tower's setup.sh
    (for anything but --dry-run or --help, since it rewrites tower.conf and can set the profile from its prompt), run
    `tower policy use` (a word naming tower, then `policy`, then `use` or a word from a variable: `uv run tower ...`,
    `python3 -m tower ...` and run-tower.py all count), or give python, node, another interpreter, awk or sed code (in
    its words or a heredoc) that imports the policy module or names setup.sh.

    setup.sh is tower's when its path or the session's folder CWD holds "tower". A path to it or the policy module
    (protected_repo_file) that the command only reads is allowed; every other naming of one asks. A read is recognised
    only in a single top-level command (SINGLE_COMMAND; see read_only_words, which GIT_MAY_RUN_PROGRAM also goes to),
    so a pipe, a second command, a substitution or a wrapper asks — the floor fails closed (ADR 0010)."""
    words = command.words
    texts = [word.text for word in words]
    program = program_of(words)
    if any(redirect.writes and protected_repo_file(redirect.target.text, cwd) for redirect in command.redirects):
        return True
    reads = read_only_words(command, single_command, git_may_run_program)
    unread = [index for index in range(len(words)) if index not in reads]
    interpreters = [index for index in unread if LANGUAGE_RUNNER.fullmatch(program_name(words[index]))]
    is_interpreter = set(interpreters)
    code_runners = [i for i in unread if i in is_interpreter or program_name(words[i]) in SETUP_CODE_PROGRAMS]
    in_tower = "tower" in cwd.lower()

    def code_after(start: int) -> list[str]:
        """The unread words after word START, and standard input: the code a program there may run. awk and sed read
        their program from standard input with `-f -`, so it is scanned too, not only an interpreter's."""
        stdin = [command.stdin_text or ""] if interpreters or code_runners else []
        return [texts[index] for index in unread if index > start] + stdin

    # The first program that runs code sees all the later ones' code too, so one look at it is enough. awk, sed, vim
    # and the like (code_runners) get the same tower-policy-use check as python/node (POLICY_CODE), not only the
    # setup.sh and policy-module ones; interpreters are a subset of code_runners, so this covers them too.
    if code_runners and any(
        POLICY_CODE.search(code)
        or POLICY_MODULE_MENTION.search(code)
        or (SETUP_MENTION.search(code) and (in_tower or "tower" in code.lower()))
        for code in code_after(code_runners[0])
    ):
        return True
    for index, text in enumerate(texts):
        if names_protected_file(text, cwd):
            runs_setup = program_name(words[index]) == "setup.sh" and program.runs(index)
            after = range(index + 1, len(texts))  # indexes of the words after it: no copy per word
            if runs_setup and after and all(texts[i] in ("--dry-run", "-h", "--help") for i in after):
                continue  # ./setup.sh --dry-run / --help only checks; it doesn't rewrite tower.conf
            if index not in reads:
                return True
    # The first word naming tower sees every later policy and use, so one look from it is enough (one from each such
    # word is quadratic)
    tower_at = next((index for index, text in enumerate(texts) if "tower" in text.lower()), len(texts))
    policy_at = next((index for index in range(tower_at + 1, len(texts)) if texts[index] == "policy"), len(texts))
    return policy_at < len(texts) and any(word.text == "use" or word.dynamic for word in words[policy_at + 1 :])


def read_only_words(command: SimpleCommand, single_command: bool, git_may_run_program: bool) -> set[int]:
    """Indexes of COMMAND's argument words its program only reads, so a setup.sh or policy-module path among them is
    read, not run. Empty unless COMMAND is the one top-level simple command of its line (SINGLE_COMMAND), with no
    substitution and no redirect writing a file, since a pipe, a second command or a captured output could run or save
    what was read. The program must be a FILE_READER (grep, cat), a sed that only prints (sed_only_prints), or git with
    a GIT_FILE_READERS subcommand on a clean line: no global setting (-c, --config-env), no dynamic word, no
    --output (git diff writes it), and not GIT_MAY_RUN_PROGRAM (the line can make git run a program)."""
    words = command.words
    program = program_position(words)
    if not single_command or command.substituted or program >= len(words):
        return set()
    if any(redirect.writes_file for redirect in command.redirects):
        return set()
    name, first_arg = program_name(words[program]), program + 1
    if name in FILE_READERS or (name == "sed" and sed_only_prints(words[first_arg:])):
        return set(range(first_arg, len(words)))
    if name == "git" and not git_may_run_program and not any(word.dynamic for word in words[first_arg:]):
        subcommand = first_arg + git_subcommand_at(words[first_arg:])
        if subcommand >= len(words) or words[subcommand].text not in GIT_FILE_READERS:
            return set()
        if any(config_setting(words, index) is not None for index in range(first_arg, subcommand)):
            return set()
        after = words[subcommand + 1 :]
        if any(word.text == "--output" or word.text.startswith("--output=") for word in after):
            return set()  # git diff --output writes its output to a file (the next word or the =value)
        return {subcommand + 1 + offset for offset, word in enumerate(after) if not word.text.startswith("-")}
    return set()


def git_subcommand_at(args: list[Word]) -> int:
    """Index in git's ARGS of its subcommand, past its global options (len(ARGS) if it has none)."""
    index = 0
    while index < len(args) and args[index].text.startswith("-"):
        index += 2 if args[index].text in GIT_OPTIONS_WITH_VALUE else 1
    return index


def git_runs_program(words: list[Word]) -> bool:
    """Whether a simple command's WORDS set git up to run a program on the files it reads: an environment variable for
    an external diff, a pager, an editor or the exec path (GIT_RUN_PROGRAM_ENV), --exec-path, --paginate (forces the
    pager), a `git config` of such a key, or a -c/--config-env of one (git_key_runs_program). Checked over the whole
    line, so an earlier command's `git config` counts too."""
    if any(GIT_RUN_PROGRAM_ENV.match(word.text) for word in words):
        return True
    if any(word.text == "--exec-path" or word.text.startswith("--exec-path=") for word in words):
        return True  # git runs its helpers from this folder
    if any(word.text == "--paginate" for word in words):
        return True  # forces the configured pager to run, even with no terminal (a bare global -p does too; see ADR)
    for index in range(len(words)):
        setting = config_setting(words, index)
        if setting is not None and git_key_runs_program(setting.text):
            return True
    return any(word.text == "config" for word in words) and any(git_key_runs_program(word.text) for word in words)


def git_key_runs_program(setting: str) -> bool:
    """Whether a git config SETTING (a bare key, or key=value) names a program or shell command git runs:
    core.pager/editor, sequence.editor, any pager.*, a submodule's update (its !command form), or a key whose last part
    is one of GIT_PROGRAM_KEY_LEAVES (diff.external, diff.<driver>.command, filter.<name>.clean, core.sshCommand,
    credential.helper, gpg.program, merge.<driver>.driver, ...)."""
    key = setting.partition("=")[0].lower()
    if key.startswith("pager.") or (key.startswith("submodule.") and key.endswith(".update")):
        return True
    return key.rsplit(".", 1)[-1] in GIT_PROGRAM_KEY_LEAVES


def sed_only_prints(args: list[Word]) -> bool:
    """Whether sed run with ARGS only selects lines to print, delete or quit on, never running a command or writing a
    file. No word from a variable, and no option but SED_SWITCHES or -e (an -i edit, -f script file or -l makes it
    unsafe). Every script must be only SED_PRINT_STATEMENTs. The scripts are the -e values and, since GNU and BSD sed
    disagree on whether a bare operand before -e is a script or a file, the first bare operand when it comes before any
    -e; bare operands after an -e are files. A write (w), a run (GNU's e) or an edit makes a script fail the check."""
    if any(arg.dynamic for arg in args):
        return False
    scripts: list[str] = []
    saw_expression = False
    saw_operand = False
    index = 0
    while index < len(args):
        text = args[index].text
        expression = SED_EXPRESSION.fullmatch(text)
        if text == "--":
            index += 1
            continue
        if expression:
            attached = expression.group(1) if expression.group(1) is not None else expression.group(2)
            if attached:
                scripts.append(attached)
            elif index + 1 < len(args):
                scripts.append(args[index + 1].text)
                index += 1
            else:
                return False  # -e with nothing after it
            saw_expression = True
        elif text.startswith("-") and text != "-" and not SED_SWITCHES.fullmatch(text):
            return False  # -i, -f, -l<n> or another option: not a plain print
        elif not text.startswith("-") or text == "-":
            if not saw_expression and not saw_operand:
                scripts.append(text)  # the first bare operand is the script when no -e came first
            saw_operand = True
        index += 1
    statements = [part for script in scripts for part in sed_statements(script)]
    return any(part.strip() for part in statements) and all(
        not part.strip() or SED_PRINT_STATEMENT.fullmatch(part) for part in statements
    )


def sed_statements(script: str) -> list[str]:
    """SCRIPT split into statements on ; and newlines, but not those inside a /regex/ address, where ; is literal, or
    just after a backslash. The /.../ and its escapes are honoured so a print script with a ; or an escaped / in its
    regex stays one statement; the scan is linear, so a long script never backtracks."""
    parts: list[str] = []
    current: list[str] = []
    in_regex = False
    index = 0
    while index < len(script):
        char = script[index]
        if char == "\\" and index + 1 < len(script):
            current.append(script[index : index + 2])
            index += 2
            continue
        if char == "/":
            in_regex = not in_regex
        if char in ";\n" and not in_regex:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
        index += 1
    parts.append("".join(current))
    return parts


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
    except Exception as err:  # noqa: BLE001 - a crash would block the call without a prompt, so ask instead
        decision = ([f"the approval hook failed ({type(err).__name__}: {err})"], "Approve? ")
    if decision:
        ask(*decision)
    return 0


if __name__ == "__main__":
    sys.exit(main())
