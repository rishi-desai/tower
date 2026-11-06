#!/usr/bin/env python3
"""confirm-public.py: Claude Code PreToolUse hook. Forces an approval prompt before public GitHub actions.

Claude Code runs this before every Bash command and sends the call as JSON on stdin. If the command
would post or change something other people can see, we print a JSON "ask" decision, and Claude Code
shows the user a confirmation prompt, even when an allow rule like Bash(git *) would normally skip it.

Always asks:  gh pr comment/review, gh issue writes (comment, create, edit, close, ...), gh release/label
              writes, and `gh api` calls that write (GraphQL mutations or queries read from a file, REST
              with a write method or with fields, since gh defaults to POST when fields are given).
PR changes:   gh pr create/edit/ready/merge/close/reopen/update-branch depend on pr_approval
                every (default) -> they ask
                none            -> they don't, so a worker can open and merge its own PR
              An unknown value is treated as `every`.
Pushes:       depends on push_approval in ~/.claude/tower.conf
                every (default) -> every `git push` asks
                force           -> only force pushes, deletions, --prune and +refspecs ask
                none            -> pushes never ask
              An unknown value is treated as `every`, the safest choice.

Rule files:   any Edit/Write/MultiEdit of a CLAUDE.local.md, a file under ~/.claude/tower/rules/, a
              tower.conf or ~/.claude/tower/policy.toml, and any Bash command naming those paths, always
              asks. Rule files are standing instructions loaded into every session and tower.conf sets the
              approvals above, so a prompt injection must not be able to change them silently.

How a command is read: it is split into simple commands roughly the way bash splits it: on && || ; |
& and newlines and around ( ), respecting quotes, escapes, comments and heredocs. Commands inside $( ),
backticks and <( ) are checked too, and so is the code given to `bash -c` (or sh, zsh, ...), to eval,
or to a shell through a heredoc. In each simple command, every word that is git or gh (by any path)
starts an invocation to check, so wrappers like env, command, xargs, nohup, sudo and timeout need no
special case. git's global options (-C, -c, --git-dir, ...) are skipped to find its subcommand, and
gh's command words are found the way gh itself finds them, so -R/--repo can come anywhere.

When unsure, we ask: a command that can't be split (an unclosed quote, $( or backtick), commands piped
into a shell, a git or gh subcommand or gh api method taken from a variable or command output, input
that isn't a well-formed tool call, and any crash in this hook.

Limits: this sees only the command text. These are not caught:
  - scripts and programs: `bash script.sh`, `python x.py`, `make`, or anything that calls the GitHub
    API itself (such as curl with a token);
  - commands built at run time: a variable or command output used as the program or as eval/-c code
    (`$GIT push`, `eval "$(...)"`, `bash -c "$CMD"`), or xargs input that supplies the subcommand;
  - aliases and functions: git aliases (`git p` for push), `gh alias`, and shell functions;
  - disguised names: globs, brace expansion and $'...' escapes in a program name or rule-file path, or
    a rule-file path built from variables;
  - under push_approval=force, a variable holding a force refspec (`git push origin "$REF"`, REF=+main).
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field

CONF = os.environ.get("TOWER_CONF", os.path.expanduser("~/.claude/tower.conf"))

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
    "release": {"create", "edit", "delete", "upload", "delete-asset"},
    "label": {"create", "edit", "delete", "clone"},
}
# The gh pr writes a worker needs to open and merge its own PR; pr_approval=none lets these through.
# Comments and reviews stay out of this set: they are messages other people read.
PR_SELF_SERVE = {"create", "edit", "ready", "merge", "close", "reopen", "update-branch"}
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
SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "mksh", "fish"}
INVOKERS = {"git", "gh", "eval"} | SHELLS  # the words that start an invocation we check
# Commands that run the rest of their words as a command. Only used to tell `... | sudo bash` (a shell
# reading the pipe) from `... | grep bash` (bash is just an argument).
PREFIX_COMMANDS = {"env", "command", "builtin", "exec", "nohup", "sudo", "time"}
ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\+?=")
MAX_NESTING = 8  # shells inside shells (bash -c "bash -c '...'") before we stop and ask
WORK_BUDGET = 2_000_000  # characters split plus words examined per tool call: well under a second of work

GH_DYNAMIC = "gh with a subcommand from a variable or command output can't be checked"


def conf_mode(key: str, allowed: set[str]) -> str:
    """KEY's value from the config if it is one of ALLOWED; missing, unreadable or unknown -> every."""
    mode = "every"
    try:
        with open(CONF, encoding="utf-8") as conf:
            for line in conf:
                if line.startswith(key + "="):
                    mode = line.split("=", 1)[1].strip()
    except OSError:
        pass
    return mode if mode in allowed else "every"


def push_mode() -> str:
    """push_approval from the config: every | force | none."""
    return conf_mode("push_approval", {"every", "force", "none"})


def pr_mode() -> str:
    """pr_approval from the config: every | none."""
    return conf_mode("pr_approval", {"every", "none"})


# --- Splitting a command line into simple commands ---------------------------------------------------


class Unparseable(ValueError):
    """The command uses shell syntax we can't split reliably, such as an unclosed quote."""


@dataclass
class Word:
    """One shell word with quotes and escapes removed; expansions ($x, $(...), `...`) are kept as written."""

    text: str
    dynamic: bool = False  # starts with an expansion, so what it says is only known when the shell runs it


@dataclass
class SimpleCommand:
    """One simple command (a program and its arguments) and where its standard input comes from."""

    words: list[Word] = field(default_factory=list)
    stdin_text: str | None = None  # the body of a heredoc (<<) or here-string (<<<) it reads
    piped: bool = False  # it reads the output of the command before it in a pipeline


REDIRECT = re.compile(r"&>>|&>|\d*(?:<<<|<<-|<<|<>|<&|>>|>&|>\||<|>)")
OPERATORS = (";;&", ";;", ";&", "&&", "||", "|&", ";", "&", "|")
WORD_END = set(" \t\r\n;&|()<>")
VARIABLE = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*|[0-9@*#?$!-])")


def split_commands(text: str) -> list[SimpleCommand]:
    """Split shell TEXT into simple commands, including those inside $( ), backticks and <( ). Raises Unparseable."""
    splitter = CommandSplitter(text)
    splitter.split()
    return splitter.commands


def expansion_commands(text: str) -> list[SimpleCommand]:
    """The commands inside the $( ) and backticks of TEXT, such as an unquoted heredoc's body, which bash expands."""
    splitter = CommandSplitter(text)
    while splitter.pos < len(text):
        char = splitter.peek()
        if char == "\\":
            splitter.pos += 2
        elif char == "$":
            splitter.read_dollar(quoted=True)
        elif char == "`":
            splitter.read_backtick()
        else:
            splitter.pos += 1
    return splitter.commands


class CommandSplitter:
    """Reads one command line left to right, collecting its simple commands. Use split_commands()."""

    def __init__(self, src: str) -> None:
        self.src = src
        self.pos = 0
        self.commands: list[SimpleCommand] = []
        # Heredocs whose bodies start after the current line: (command, delimiter, strip tabs, body is expanded).
        self.heredocs: list[tuple[SimpleCommand, str, bool, bool]] = []

    def peek(self, offset: int = 0) -> str:
        """The character OFFSET places ahead, or "" past the end."""
        index = self.pos + offset
        return self.src[index] if index < len(self.src) else ""

    def split(self, closer: str = "") -> None:
        """Read commands up to CLOSER (")" ends a $( or <( ), or to the end of the input if CLOSER is ""."""
        command = SimpleCommand()
        depth = 0  # subshells "(" opened in this call and not yet closed
        while True:
            char = self.peek()
            if not char:
                if closer or depth:
                    raise Unparseable("an unclosed ( or $(")
                self.finish(command)
                return
            if char in " \t\r":
                self.pos += 1
            elif char == "\\" and self.peek(1) == "\n":
                self.pos += 2
            elif char == "\n":
                self.pos += 1
                command = self.finish(command)
                self.read_heredoc_bodies()
            elif char == "#":
                end = self.src.find("\n", self.pos)
                self.pos = end if end >= 0 else len(self.src)
            elif char == "(":
                self.pos += 1
                depth += 1
                command = self.finish(command, keep_pipe=True)
            elif char == ")":
                self.pos += 1
                command = self.finish(command)
                if depth == 0 and closer:
                    return
                depth = max(depth - 1, 0)
            elif char in "<>" and self.peek(1) == "(":
                command.words.append(self.read_process_substitution())
            elif redirect := REDIRECT.match(self.src, self.pos):
                self.read_redirect(command, redirect)
            elif char in ";&|":
                operator = next(op for op in OPERATORS if self.src.startswith(op, self.pos))
                self.pos += len(operator)
                command = self.finish(command)
                command.piped = operator in ("|", "|&")
            else:
                command.words.append(self.read_word())

    def finish(self, command: SimpleCommand, keep_pipe: bool = False) -> SimpleCommand:
        """Record COMMAND if it has words and return a fresh one, still reading COMMAND's pipe if KEEP_PIPE."""
        if command.words:
            self.commands.append(command)
        return SimpleCommand(piped=keep_pipe and command.piped)

    def read_word(self) -> Word:
        """Read one word, removing quotes and escapes and splitting out the commands in its $( ) and backticks."""
        parts: list[str] = []
        dynamic = has_text = False
        while True:
            char = self.peek()
            if not char or char in WORD_END:
                return Word("".join(parts), dynamic)
            at_start = not has_text
            if char == "\\":
                if self.peek(1) != "\n":
                    parts.append(self.peek(1) or "\\")
                self.pos += 2
            elif char == "'":
                end = self.src.find("'", self.pos + 1)
                if end < 0:
                    raise Unparseable("an unclosed '")
                parts.append(self.src[self.pos + 1 : end])
                self.pos = end + 1
            elif char == '"':
                self.pos += 1
                text, starts_dynamic = self.read_double_quoted()
                dynamic = dynamic or (at_start and starts_dynamic)
                parts.append(text)
            elif char == "$":
                text, is_expansion = self.read_dollar(quoted=False)
                dynamic = dynamic or (at_start and is_expansion)
                parts.append(text)
            elif char == "`":
                dynamic = dynamic or at_start
                parts.append(self.read_backtick())
            else:
                parts.append(char)
                self.pos += 1
            has_text = has_text or bool(parts and parts[-1])

    def read_double_quoted(self) -> tuple[str, bool]:
        """Read the rest of a "..." string; returns its text and whether it starts with an expansion."""
        parts: list[str] = []
        starts_dynamic = False
        while True:
            char = self.peek()
            if not char:
                raise Unparseable('an unclosed "')
            if char == '"':
                self.pos += 1
                return "".join(parts), starts_dynamic
            if char == "\\" and self.peek(1) in ("$", "`", '"', "\\", "\n"):
                if self.peek(1) != "\n":
                    parts.append(self.peek(1))
                self.pos += 2
            elif char == "$":
                at_start = not parts  # every part in quotes is non-empty
                text, is_expansion = self.read_dollar(quoted=True)
                starts_dynamic = starts_dynamic or (at_start and is_expansion)
                parts.append(text)
            elif char == "`":
                starts_dynamic = starts_dynamic or not parts
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
            self.pos += 2
            self.split(closer=")")
        elif after == "{":
            self.pos += 2
            self.read_braced(quoted)
        elif after == "'" and not quoted:
            self.read_ansi_c()
        elif after == '"' and not quoted:
            self.pos += 2
            text, _ = self.read_double_quoted()  # $"..." is a translatable string, read like "..."
            return text, False
        elif variable := VARIABLE.match(self.src, self.pos):
            self.pos = variable.end()
        else:
            self.pos += 1
            return "$", False
        return self.src[start : self.pos], True

    def read_braced(self, quoted: bool) -> None:
        """Skip the rest of a ${...} expansion (QUOTED: inside "..."), splitting out any commands inside it."""
        depth = 1
        while depth:
            char = self.peek()
            if not char:
                raise Unparseable("an unclosed ${")
            if char == "\\":
                self.pos += 2
            elif char == "'" and not quoted:
                end = self.src.find("'", self.pos + 1)
                if end < 0:
                    raise Unparseable("an unclosed '")
                self.pos = end + 1
            elif char == '"':
                self.pos += 1
                self.read_double_quoted()
            elif char == "$":
                self.read_dollar(quoted)
            elif char == "`":
                self.read_backtick()
            else:
                depth += {"{": 1, "}": -1}.get(char, 0)
                self.pos += 1

    def read_ansi_c(self) -> None:
        """Skip a $'...' string. Its escapes aren't decoded, so the word it starts counts as dynamic."""
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
        """Read a <(...) or >(...), splitting out the commands inside; the word stands for a file bash makes up."""
        start = self.pos
        self.pos += 2
        self.split(closer=")")
        return Word(self.src[start : self.pos], dynamic=True)

    def read_redirect(self, command: SimpleCommand, redirect: re.Match[str]) -> None:
        """Read a redirection of COMMAND. A heredoc's body is read once its line ends; a here-string's word now."""
        operator = redirect.group().lstrip("0123456789")
        self.pos = redirect.end()
        while self.peek() in (" ", "\t"):
            self.pos += 1
        if self.peek() in ("<", ">") and self.peek(1) == "(":
            self.read_process_substitution()  # e.g. `> >(tee log)`
            return
        if not self.peek() or self.peek() in WORD_END:
            raise Unparseable(f"a {operator} with nothing after it")
        start = self.pos
        target = self.read_word()
        if operator in ("<<", "<<-"):
            quoted = any(char in self.src[start : self.pos] for char in "'\"\\")
            self.heredocs.append((command, target.text, operator == "<<-", not quoted))
        elif operator == "<<<":
            command.stdin_text = target.text

    def read_heredoc_bodies(self) -> None:
        """Read the bodies of the heredocs started on the line that just ended, in order."""
        for command, delimiter, strip_tabs, expanded in self.heredocs:
            lines: list[str] = []
            while self.pos < len(self.src):
                end = self.src.find("\n", self.pos)
                if end < 0:
                    end = len(self.src)
                line = self.src[self.pos : end]
                self.pos = end + 1
                if strip_tabs:
                    line = line.lstrip("\t")
                if line == delimiter:
                    break
                lines.append(line)
            command.stdin_text = "\n".join(lines)
            if expanded:  # an unquoted delimiter: bash runs the $( ) and backticks in the body
                self.commands.extend(expansion_commands(command.stdin_text))
        self.heredocs = []


# --- Judging the commands -------------------------------------------------------------------------


class TooComplex(Exception):
    """Checking the command would take longer than the hook should; it asks instead."""


class CommandChecker:
    """Checks one Bash command line under this machine's push and PR approval modes.

    It carries a work budget (characters split plus words examined), because a hook that runs past its
    timeout lets the command through unchecked; a huge or crafted command raises TooComplex instead.
    """

    def __init__(self, push: str, pr_approval: str, budget: int = WORK_BUDGET) -> None:
        self.push = push
        self.pr_approval = pr_approval
        self.budget = budget

    def spend(self, units: int) -> None:
        """Use UNITS of the work budget; raise TooComplex once it is gone."""
        self.budget -= units
        if self.budget < 0:
            raise TooComplex

    def check(self, command_line: str, depth: int = 0) -> list[str]:
        """Reasons to ask for COMMAND_LINE, one per distinct problem; empty if it doesn't need approval."""
        if depth > MAX_NESTING:
            return ["nests shells too deeply to check"]
        self.spend(len(command_line) + 1)
        try:
            commands = split_commands(command_line)
        except Unparseable as err:
            return [f"has {err}, so it can't be split into commands to check"]
        reasons: list[str] = []
        for command in commands:
            for index in range(len(command.words)):
                for reason in self.check_invocation(command, index, depth):
                    if reason not in reasons:
                        reasons.append(reason)
        return reasons

    def check_invocation(self, command: SimpleCommand, index: int, depth: int) -> list[str]:
        """Reasons to ask if word INDEX of COMMAND is git, gh, eval or a shell, run with the words after it.

        Looking at every word, not just the first, covers wrappers like env, xargs, sudo and timeout.
        """
        word = command.words[index]
        name = "" if word.dynamic else os.path.basename(word.text)
        if name not in INVOKERS:
            return []
        self.spend(len(command.words) - index)
        args = command.words[index + 1 :]
        if name == "eval":
            return self.check(" ".join(arg.text for arg in args), depth + 1)
        if name in SHELLS:
            return self.check_shell(args, command, index == program_index(command.words), depth)
        reason = check_git(args, self.push) if name == "git" else check_gh(args, self.pr_approval)
        return [reason] if reason else []

    def check_shell(self, args: list[Word], command: SimpleCommand, is_program: bool, depth: int) -> list[str]:
        """Reasons to ask for a shell run with ARGS: its -c code or heredoc is checked like any command line.

        Commands piped into it can't be seen, so that asks when the shell is COMMAND's program. A script
        file it runs isn't checked (see Limits).
        """
        code, reads_stdin = shell_input(args)
        if code is not None:
            return self.check(code, depth + 1)
        if not reads_stdin:
            return []
        if command.stdin_text is not None:
            return self.check(command.stdin_text, depth + 1)
        if command.piped and is_program:
            return ["pipes commands into a shell, where they can't be checked"]
        return []


def program_index(words: list[Word]) -> int:
    """Index of the program a simple command runs: past VAR=value assignments and prefix commands like sudo."""
    index = 0
    while index < len(words):
        text = words[index].text
        if not (ASSIGNMENT.match(text) or text in PREFIX_COMMANDS or (index and text.startswith("-"))):
            break
        index += 1
    return index


def shell_input(args: list[Word]) -> tuple[str | None, bool]:
    """What a shell run with ARGS executes: its -c code (or None), and whether it reads commands from stdin."""
    index = 0
    runs_code = from_stdin = False
    while index < len(args):
        text = args[index].text
        if text == "--":
            index += 1
            break
        if text in ("--rcfile", "--init-file"):
            index += 2
        elif text == "--command":  # fish's long form of -c
            runs_code = True
            index += 1
        elif text.startswith("--command="):
            return text.split("=", 1)[1], False
        elif text.startswith("--"):
            index += 1
        elif len(text) > 1 and text[0] in "-+":
            runs_code = runs_code or "c" in text
            from_stdin = from_stdin or "s" in text
            index += 2 if ("o" in text or "O" in text) else 1  # -o/-O take an option name
        else:
            break
    operand = args[index].text if index < len(args) else None
    if runs_code:
        return operand, False
    return None, from_stdin or operand in (None, "-")


def check_git(args: list[Word], mode: str) -> str | None:
    """Reason to ask for git run with ARGS: a push, judged by push_approval, found past git's global options."""
    index = 0
    while index < len(args) and args[index].text.startswith("-"):
        index += 2 if args[index].text in GIT_OPTIONS_WITH_VALUE else 1
    if index >= len(args):
        return None
    subcommand = args[index]
    if subcommand.dynamic:
        action = "git with a subcommand from a variable or command output, which could be push,"
        return check_push(args[index + 1 :], mode, action)
    if subcommand.text != "push":
        return None
    return check_push(args[index + 1 :], mode, "git push")


def check_push(args: list[Word], mode: str, action: str) -> str | None:
    """Reason to ask for ACTION, a git push with ARGS, depending on push_approval."""
    if mode == "none":
        return None
    is_force = any(is_force_push_arg(arg.text) for arg in args)
    if mode == "every":
        return (
            action
            + (" (force/delete: rewrites or removes remote history)" if is_force else "")
            + " (push_approval=every)"
        )
    if is_force:
        return "force push or branch delete rewrites or removes remote history (push_approval=force)"
    return None


def is_force_push_arg(arg: str) -> bool:
    """Whether a git push argument forces or deletes: --force*, --mirror, --delete, --prune, f or d among
    short options (-f, -fu), or a refspec starting with + (force) or : (delete)."""
    if arg.startswith("--"):
        return arg.startswith(("--force", "--mirror", "--delete", "--prune"))
    if arg.startswith("-"):
        return "f" in arg or "d" in arg
    return arg.startswith(("+", ":"))


def check_gh(args: list[Word], pr_approval: str) -> str | None:
    """Reason to ask for gh run with ARGS, or None if it only reads (or is a PR change pr_approval allows)."""
    indexes = gh_command_indexes(args)
    if not indexes:
        return None
    group = args[indexes[0]]
    if group.dynamic:
        return GH_DYNAMIC
    if group.text == "api":
        return check_gh_api(args[: indexes[0]] + args[indexes[0] + 1 :])
    writes = GH_WRITE_SUBCOMMANDS.get(group.text, set())
    if not writes or len(indexes) < 2:
        return None
    subcommand = args[indexes[1]]
    if subcommand.dynamic:
        return GH_DYNAMIC
    if subcommand.text not in writes:
        return None
    if pr_approval == "none" and group.text == "pr" and subcommand.text in PR_SELF_SERVE:
        return None
    return f"gh {group.text} {subcommand.text} posts or changes something on GitHub"


def gh_command_indexes(args: list[Word]) -> list[int]:
    """Indexes in ARGS of gh's first two command words (`pr`, `comment`), found the way gh's CLI library
    (cobra) finds them: a flag without "=" takes the next word as its value, except -h and --help, so
    `gh pr -R owner/repo comment` is a comment; "--" ends the search."""
    found: list[int] = []
    index = 0
    while index < len(args) and len(found) < 2:
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
    method, fields, has_input, endpoint = parse_gh_api(args)
    if endpoint is not None and endpoint.text == "graphql" and not endpoint.dynamic:
        if any("mutation" in arg.text for arg in args):
            return "gh api graphql mutation changes something on GitHub"
        reads_file = has_input or any(
            key == "query" and value.startswith("@") for key, _, value in (f.text.partition("=") for f in fields)
        )
        return "gh api graphql reads its query from a file, so it can't be checked" if reads_file else None
    if method is not None:
        if method.dynamic:
            return "gh api with a method from a variable or command output can't be checked"
        verb = method.text.upper()
        return f"gh api {verb} changes something on GitHub" if verb in WRITE_METHODS else None
    if fields or has_input:
        return "gh api with fields defaults to POST, which changes something on GitHub"
    return None


def parse_gh_api(args: list[Word]) -> tuple[Word | None, list[Word], bool, Word | None]:
    """Parse gh api's ARGS the way its flag library (pflag) does: values attached (-XPOST, -X=POST,
    --method=POST) or separate, short switches clustered (-iXPOST). Returns the method (the last one
    given), the -f/-F field values, whether --input was given, and the endpoint."""
    method: Word | None = None
    fields: list[Word] = []
    has_input = False
    positionals: list[Word] = []
    index = 0
    while index < len(args):
        text = args[index].text
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
        if value is not None:
            word = Word(value, dynamic=value.startswith(("$", "`")))
        elif index < len(args):
            word = args[index]
            index += 1
        else:
            word = Word("")
        if name == "method":
            method = word
        elif name in ("field", "raw-field"):
            fields.append(word)
        elif name == "input":
            has_input = True
    return method, fields, has_input, positionals[0] if positionals else None


# --- Rule files and the hook itself ---------------------------------------------------------------

RULE_PATH = re.compile(
    r"(?:^|/)CLAUDE\.local\.md$|/\.claude/tower/rules/|(?:^|/)tower\.conf$|/\.claude/tower/policy\.toml$"
)
RULE_MENTION = re.compile(r"CLAUDE\.local\.md|\.claude/tower/rules|tower\.conf|\.claude/tower/policy\.toml")


def check_rule_edit(file_path: str) -> str | None:
    """Reason to ask for an edit tool call that targets a personal rule file or tower's approval settings."""
    if RULE_PATH.search(os.path.expanduser(file_path)):
        return f"edits a personal rule file or tower's approval settings ({file_path})"
    return None


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
        reason = check_rule_edit(file_path)
        return ([reason], "Rule change — approve? ") if reason else None
    command = tool_input.get("command") or ""
    if not isinstance(command, str):
        return unreadable
    try:
        reasons = CommandChecker(push_mode(), pr_mode()).check(command)
    except TooComplex:
        reasons = ["is too long or complex to check in time"]
    if RULE_MENTION.search(command):
        reasons.append("the command touches a personal rule file or tower's approval settings")
    return (reasons, "Public action or rule change — approve? ") if reasons else None


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
