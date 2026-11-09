# The approval hook fails closed with a 30-second timeout; the other hooks stay open

The approval hook bounds its own work with a work budget (ADR 0003), but the budget only bounds work it is told about:
the review rounds of #105's PR found eight families of uncharged per-word checks, and #129 (tilde lookups) is one more.
A path nobody has found yet, a `python3` that can't start or a killed process used to let the call through after
Claude Code's 10-minute default, since a PreToolUse hook that fails or times out doesn't block it (#127). `hooks.json`
now gives the approval hook `"timeout": 30` and `"onFailure": "block"` (Claude Code 2.1.295+), so any of those refuses
the call instead. The hook still turns its own exceptions into an ask (`main()`), and a command too big for the budget
still asks: the block is the backstop for what the script can't catch itself.

**30 seconds, not 15.** The work budget aims at a few seconds at worst on macOS's Python 3.9, and the timing tests hold
every known case under 10 s. But #118's 350 KB command took about 14 s before #105, a merge's two `gh` lookups may take
5 s each on a slow network, and five to seven workers running pytest at once load the machine. A block stops a command
you would have approved, so the limit leaves room; 30 s still refuses twenty times sooner than the default. Lower it
once the worst case stays fast. Only a stalled disk outlasts it with allowed work: each folder a command reaches costs
a `git remote -v` of up to 3 s, so the session's folder and the 16 others the hook looks up before giving up could
take 51 s, and a merge's lookups 10 s more. Such a call is refused, which is safe.

**Only the approval hook.** The card reminder, event log, worker memory, PreCompact, session env and policy line hooks
are bookkeeping: one that fails should cost a missing note, not the session, so they keep Claude Code's default and
carry on.

## Considered options

- **A wall-clock deadline inside confirm-public.py** (`signal.setitimer` raising TooComplex, or a clock check in
  `CommandChecker.spend()`): an overrun would ask instead of refusing, and it catches paths that never call `spend()`,
  but it can't help when `python3` doesn't start or the process is killed, so `onFailure` is needed either way, and
  the issue's worry was a verdict that depends on the machine's speed (though with a 30 s timeout, whether a slow
  check is refused or finishes already does). It can still sit inside the 30 s: #134.
- **15 s**, the issue's first suggestion: too close to what loaded machines have measured (above).
- **Every hook fails closed**: a broken bookkeeping hook would stop sessions for no safety gain.

## Consequences

- The hook matches Bash, Edit, Write and MultiEdit, so a broken `python3` or plugin install blocks every one of them
  until it's fixed: loud on purpose.
- A refusal isn't a prompt. The agent sees `hook error: … timed out; blocking because onFailure is "block"` and can
  retry or split the command; you can't approve the call through.
- Claude Code before 2.1.295 ignores `onFailure` rather than rejecting the plugin: 2.1.294's `claude plugin validate
  --strict` passes with it and accepts any value for it (while still rejecting a bad `timeout`), and a live 2.1.294
  session loaded the plugin, ran the approval hook past its timeout and then ran the command; 2.1.295 refused it.
  There it fails open as before, and the 30 s makes it worse for a slow check: one that took between 30 s and 10
  minutes used to finish and ask, and is now killed and let through. `tower doctor` warns about such a Claude Code;
  #134 would make an overrun ask on every version.
- `claude plugin validate --strict` (2.1.304) rejects a wrong `onFailure` value but not a misspelled key, so
  `tests/test_confirm_public.py` pins the key and its value.
- Checked on 2.1.304: a PreToolUse hook that slept past its timeout with `onFailure: "block"` refused the call; the same
  hook without it let the call run.
