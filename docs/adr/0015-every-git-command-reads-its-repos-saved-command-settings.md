# Every git command reads its repo's saved command-running settings, and checks them like the line's own

#82 made the approval hook check the shell code git runs from settings written in the line (`-c core.pager=...`, a
`!` alias, `git config core.sshCommand '...'`), and #83 made a push under `push = "force"` read the push settings saved
in its repo (ADR 0014). A command-running setting saved earlier, by another tool call or by hand, was still unseen:
with `core.pager = gh pr comment 5 -b hi` in `.git/config` or `~/.gitconfig`, a later `git log` posted the comment
with no prompt (#139). Now every git command in a Bash call reads the settings saved for the folder it runs in that
name a command (`git_key_runs_program`'s keys: pagers, editors, diff and filter drivers, `core.sshCommand`,
credential helpers, ...) together with the aliases, and checks each value as shell code through the same splitter
(ADR 0003): a GitHub write by its key, a push by `push`, and the safety floor. The `!` alias the subcommand names is
checked with the words after it, and a non-`!` one is read as what it stands for (`rebase --exec ...`).

## What is read, and when

- **One read beside #83's**: the `git config --null --get-regexp` of ADR 0014, its pattern widened to these keys, so a
  folder is still read once per hook run, cached across the line's git commands, for the same work-budget cost. It
  runs git with the hook's own `HOME`, so `~/.gitconfig` counts as the repo's config does, with every include.
- **For every git command**, not only the subcommands that use a key: almost any git command can run the pager or a
  credential helper, and a map from subcommand to key would go stale as git grows.
- **Every setting, whatever the subcommand**: `git status` checks the saved `diff.external` too. A value that only
  runs a program (`less`, `osxkeychain`) passes, so this asks only when a saved value holds a GitHub write, a push the
  policy asks for, or something the safety floor guards.
- **The known folders**: the session's, the line's cds, `git -C`, `--git-dir`. A folder from a variable is left out
  rather than making every git command there ask (a Limit, as for a possible alias in ADR 0014); a read that fails or
  takes longer than 3 s asks.
- **Saved text is checked as written**, like `GIT_SSH_COMMAND`: a `$` in a saved value (a helper's
  `echo "password=$TOKEN"`) was fixed when it was saved, not built by the line at run time, so it doesn't ask for that
  alone, unlike a `$` in a `-c` value (ADR 0013).

## Considered options

- **Read only for the subcommands mapped to each key** (`log` to `core.pager`, `fetch` to `core.sshCommand`): fewer
  reads, but the map is long, git adds paths to it, and a pager runs for nearly every subcommand anyway.
- **A separate read beside #83's**: simpler code, but two `git` calls per folder where one does.
- **Treat a saved `!` alias as a push that may force**, as ADR 0014 did: the prompt named a push the alias never made,
  and a gh write inside it passed under `push = "none"`.

## Consequences

- Each git command costs one more `git config` call per folder per hook run: about 8 ms for the read alone on an M-series
  Mac, and 10 to 20 ms in the hook's median time for `git status`, `git log` and `git diff` (about 70 to 85 ms before,
  measured 40 runs each; run-to-run noise is about the same size). A line of many git commands in one folder reads once.
- A repo whose saved config can't be read (a broken `.git/config`) makes every git command there ask until it is fixed.
- Files a setting names are not read: hook scripts in `.git/hooks` or the folder `core.hooksPath` names, a helper found
  on `PATH`. Config changed by a process outside Claude Code between the check and the run isn't seen either.
