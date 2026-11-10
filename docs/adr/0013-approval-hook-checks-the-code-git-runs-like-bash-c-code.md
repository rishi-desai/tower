# The approval hook checks the shell code git runs from the line like bash -c code, and asks when it's built at run time

git runs shell code that the command line hands it: the value of a setting that names a command (`-c core.sshCommand=...`,
`diff.external`, `core.pager`, a credential helper, given with `-c`, `--config-env`, `GIT_CONFIG_*`, a clone's `-c` or
a `git config` that saves it), a `!` alias, the command `submodule foreach`, `rebase --exec` and `bisect run` take, and
variables such as `GIT_SSH_COMMAND` and `EDITOR`. The hook didn't look inside any of them, so a `gh repo delete` placed
in one passed (#82). It now checks that code the way it checks `bash -c` code, through the same splitter (ADR 0003), so
every rule applies inside it: a GitHub write is judged by its key, a push by `push`, and the safety floor holds. A
git setting whose value is only known at run time (`-c core.sshCommand="$X"`, `--config-env` naming a variable the
line doesn't set, `rebase --exec "$CMD"`) asks, because the shell parses what the variable holds as code too:
`core.sshCommand="ssh -i $KEY"` runs whatever `KEY` adds. That holds for a `$` the line quotes too (`-c
'core.pager=$X'`, `--exec '$CMD'`): git hands the text to its own shell, which expands it. A `--config-env` takes the
value the variable has when git starts: one the line sets for its later commands, or one given to that git (`X=v git`),
not one scoped to another command (`X=v true`). A variable such as `GIT_SSH_COMMAND` is checked as written
instead, like `bash -c` code: `GIT_SSH_COMMAND="ssh -i $HOME/.ssh/deploy" git fetch` is how workers fetch with a deploy
key, and #82 asked only for git's settings.

## Considered options

- **Count a command-running key as a possible force push**, as a `!` alias was before: one rule, but the prompt named a
  push the command never makes, and under `push = "none"` a gh write inside it still passed.
- **Ask whenever a command-running key appears**: simplest, but `git -c core.pager=cat log`, `git rebase -x 'uv run
  pytest -q' main` and `GIT_EDITOR=true git rebase --continue` are what unattended workers run, and each would stop them.
- **Check a run-time value's text like `bash -c "$CMD"` does** (it passes): consistent, but #82's acceptance asks for
  these to ask, and workers rarely build git's settings from variables, so the prompts it adds are few.

## Consequences

- `bash -c "$CMD"`, `eval "$CMD"` and `GIT_SSH_COMMAND="$CMD"` still pass (a documented Limit), while git's own
  settings built at run time ask. Making the first ask too would be a separate change with its own cost in prompts.
- Which keys run a command is a list (`GIT_PROGRAM_KEY_LEAVES`, `git_key_runs_program`) that grows with git. A key it
  misses is a gap, not a crash, so the list leans wide: `core.askPass` and `gpg.program` run a program rather than shell
  code, and are checked as code anyway.
- Config git reads from somewhere the line doesn't show (a saved `~/.gitconfig`, an `include.path`, an inherited
  `GIT_CONFIG_PARAMETERS`) and files git runs by name (hooks, `core.hooksPath`, `GIT_EXEC_PATH`) stay Limits: the hook
  reads the command text. A `git config` that saves a command-running key is checked when it saves it.
- Code git runs inherits its `-C` and its settings, which git passes on in `GIT_CONFIG_PARAMETERS`, so a git in that
  code is judged with them: `git -c remote.origin.mirror=true -c alias.x='!git push' x` is a push that may force.
- `gh auth git-credential`, the helper `gh auth setup-git` saves, is a read: it hands git the token and changes nothing.
- Allowing git's `ext::` transport (`protocol.ext.allow`, `GIT_ALLOW_PROTOCOL=ext`) asks, since an `ext::` URL names a
  command the hook can't follow to its source.
