#!/usr/bin/env python3
"""confirm-public.py: Claude Code PreToolUse hook. Forces an approval prompt before public GitHub actions.

Claude Code runs this before every Bash command and sends the call as JSON on stdin. If the command
would post or change something other people can see, we print a JSON "ask" decision, and Claude Code
shows the user a confirmation prompt, even when an allow rule like Bash(git *) would normally skip it.

Always asks:  gh pr comment/review, gh issue writes (comment, create, edit, close, ...), gh release/label
              writes, and `gh api` calls that write (GraphQL mutations, REST with a write method or
              with fields, since gh defaults to POST when fields are given).
PR changes:   gh pr create/edit/ready/merge/close/reopen/update-branch depend on pr_approval
                every (default) -> they ask
                none            -> they don't, so a worker can open and merge its own PR
              An unknown value is treated as `every`.
Pushes:       depends on push_approval in ~/.claude/tower.conf
                every (default) -> every `git push` asks
                force           -> only force pushes, deletions and +refspecs ask
                none            -> pushes never ask
              An unknown value is treated as `every`, the safest choice.

Rule files:   any Edit/Write/MultiEdit of a CLAUDE.local.md or a file under ~/.claude/tower/rules/,
              and any Bash command naming those paths, always asks. They are standing instructions
              loaded into every session, so a prompt injection must not be able to change them silently.

Limits: this sees the command text the agent runs. A script the agent writes that calls the GitHub
API itself is not caught. Splitting on && ; | is approximate; when unsure, we ask.
"""

from __future__ import annotations

import json
import os
import re
import sys

CONF = os.environ.get("TOWER_CONF", os.path.expanduser("~/.claude/tower.conf"))

GH_WRITE_SUBCOMMANDS = {
    "pr": {"comment", "review", "create", "edit", "merge", "close", "reopen", "ready", "lock", "unlock", "update-branch"},
    "issue": {"comment", "create", "edit", "close", "reopen", "delete", "lock", "unlock", "pin", "unpin", "transfer", "develop"},
    "release": {"create", "edit", "delete", "upload", "delete-asset"},
    "label": {"create", "edit", "delete", "clone"},
}
# The gh pr writes a worker needs to open and merge its own PR; pr_approval=none lets these through.
# Comments and reviews stay out of this set: they are messages other people read.
PR_SELF_SERVE = {"create", "edit", "ready", "merge", "close", "reopen", "update-branch"}
WRITE_METHODS = {"POST", "PATCH", "PUT", "DELETE"}
FIELD_FLAGS = re.compile(r"(?:^|\s)(?:-f|-F|--field|--raw-field|--input)(?:\s|=|$)")
METHOD_FLAG = re.compile(r"(?:^|\s)(?:-X|--method)(?:\s+|=)['\"]?([A-Za-z]+)")
FORCE_PUSH = re.compile(r"(?:^|\s)(?:--force\b|--force-with-lease\b|--force-if-includes\b|--mirror\b|--delete\b|-[A-Za-z]*[fd][A-Za-z]*\b|\+\S+|:\S+)")


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


def segments(command: str) -> list[str]:
    """Split a shell command line into simple commands on && || ; | and newlines (approximate)."""
    return [part.strip() for part in re.split(r"&&|\|\||[;|\n]", command) if part.strip()]


def check_gh(segment: str, pr_approval: str) -> str | None:
    """Reason to ask for a gh command, or None if it only reads (or is a PR change pr_approval allows)."""
    match = re.search(r"\bgh\s+(.*)", segment)
    if not match:
        return None
    words = match.group(1).split()
    # Skip global flags before the subcommand, e.g. `gh -R owner/repo pr comment`.
    while words and words[0].startswith("-"):
        words = words[2:] if words[0] in {"-R", "--repo"} else words[1:]
    if len(words) >= 2 and words[1] in GH_WRITE_SUBCOMMANDS.get(words[0], set()):
        if pr_approval == "none" and words[0] == "pr" and words[1] in PR_SELF_SERVE:
            return None
        return f"gh {words[0]} {words[1]} posts or changes something on GitHub"
    if words and words[0] == "api":
        rest = match.group(1)
        if re.search(r"\bapi\s+graphql\b", rest):
            if "mutation" in rest:
                return "gh api graphql mutation changes something on GitHub"
            if re.search(r"query=@", rest):
                return "gh api graphql reads its query from a file, so it can't be checked"
            return None
        method = METHOD_FLAG.search(rest)
        if method:
            verb = method.group(1).upper()
            return f"gh api {verb} changes something on GitHub" if verb in WRITE_METHODS else None
        if FIELD_FLAGS.search(rest):
            return "gh api with fields defaults to POST, which changes something on GitHub"
    return None


def check_push(segment: str, mode: str) -> str | None:
    """Reason to ask for a git push, depending on push_approval."""
    match = re.search(r"\bgit\b(?:\s+-[Cc]\s+\S+)*\s+push\b(.*)", segment)
    if not match or mode == "none":
        return None
    is_force = bool(FORCE_PUSH.search(match.group(1)))
    if mode == "every":
        return "git push" + (" (force/delete: rewrites or removes remote history)" if is_force else "") + " (push_approval=every)"
    if is_force:
        return "force push or branch delete rewrites or removes remote history (push_approval=force)"
    return None


RULE_PATH = re.compile(r"(?:^|/)CLAUDE\.local\.md$|/\.claude/tower/rules/")
RULE_MENTION = re.compile(r"CLAUDE\.local\.md|\.claude/tower/rules")


def check_rule_edit(file_path: str) -> str | None:
    """Reason to ask for an edit tool call that targets a personal rule file."""
    if RULE_PATH.search(os.path.expanduser(file_path)):
        return f"edits a personal rule file loaded into every session ({file_path})"
    return None


def ask(reasons: list[str], prefix: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": prefix + "; ".join(reasons),
        }
    }))


def main() -> int:
    try:
        call = json.load(sys.stdin)
    except ValueError:
        return 0  # not a tool call we understand; let Claude Code's normal rules decide
    tool = call.get("tool_name")
    tool_input = call.get("tool_input") or {}
    if tool in {"Edit", "Write", "MultiEdit"}:
        reason = check_rule_edit(str(tool_input.get("file_path") or ""))
        if reason:
            ask([reason], "Rule change — approve? ")
        return 0
    if tool != "Bash":
        return 0
    command = tool_input.get("command") or ""
    mode = push_mode()
    pr_approval = pr_mode()
    reasons = []
    for segment in segments(command):
        reason = check_gh(segment, pr_approval) or check_push(segment, mode)
        if reason and reason not in reasons:
            reasons.append(reason)
    if RULE_MENTION.search(command):
        reasons.append("the command touches a personal rule file (CLAUDE.local.md or ~/.claude/tower/rules)")
    if reasons:
        ask(reasons, "Public action or rule change — approve? ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
