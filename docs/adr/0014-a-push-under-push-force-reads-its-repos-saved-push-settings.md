# A push under push = "force" reads its repo's saved push settings when it runs

Under `push = "force"` (the `personal` profile) a plain push passes and one that may force or delete asks. The hook
judged that from the command text, so settings saved before the command went unseen: `git config remote.origin.mirror
true` in one Bash call and `git push origin` in the next passed, and so did `git p origin` with `alias.p = push
--force` saved (#83). Now, when a push runs under `push = "force"`, or a subcommand that isn't one of git's own (so may
be an alias) runs under `every` or `force`, the hook reads the settings saved in that push's repo with one `git config
--null --get-regexp '^(remote\..*\.(mirror|push)|alias\..*)$'`. A remote that mirrors, a `+` or `:<ref>` push refspec, an
alias that expands to a forcing push, a `!` alias, or a read that fails or outlasts its 3 seconds makes the push one
that may force.

## What is read, and where

- **The effective config**, as git itself reads it: system, global, the repo's, and every `include`/`includeIf` they
  name, which git resolves, so an included file can't hide a setting.
- **Of every folder the push may act on**: the session's, every folder the line cds into, `git -C`, `--git-dir`,
  `--work-tree`, `env -C` and `sudo -D`, and each with a `GIT_DIR` or `GIT_COMMON_DIR` the line gives: the same
  folders whose `git remote -v` judges which repo a push reaches. A folder from a variable (`cd "$DIR"`), or more than
  16 of them, can't be read, so the push may force. A folder that doesn't exist yet is read from the nearest one
  above it, where the system and global config still apply.
- **Not push URLs**: `remote.<name>.pushurl` and `url.<base>.pushInsteadOf` change where a push goes, and `git remote
  -v`, which already decides the profile, applies both.
- **Only for a push or a possible alias**: `git status`, `git log` and a lone `git config user.name x` read nothing.
  Each folder is read once per command line, for the same work-budget cost as its `git remote -v`. For a subcommand
  that may only be an alias (`git lfs`, `git diff-tree`), a folder from a variable is left out rather than making it
  ask: an alias saved only in that repo is a Limit. An alias found is read as what it stands for with git's options
  before it still applying (`git -c remote.origin.mirror=true p`), and so is one that writes a push setting (`alias.cfg
  = config`).

## Considered options

- **Command text only**, as before: the gap in #83 is exactly the settings an earlier tool call saved, which no
  reading of the current command can see.
- **Treat every push under `force` as one that may force**: makes `personal` the same as `work` for pushes, and
  workers push unattended.
- **Read the config with `git config --list` and parse it all**: one call too, but it prints every setting
  (credential helpers' arguments, URLs with tokens) into the hook's memory for no gain; the regex keeps it to what
  decides a push.

## Consequences

- A push under `force` costs one more `git` call (a few milliseconds), and a push in a folder named by a variable
  asks even when that repo has no such setting.
- On a stalled disk each folder's read adds up to 3 s beside its `git remote -v`; past the hook's 30 s timeout the call
  is refused (ADR 0012), which is safe.
- A saved `!` alias counts as a push that may force rather than having its code checked: reading command-running
  settings from saved config (#82 checks only those written in the line) is a possible follow-up.
- Settings written in the same line, before the push runs, aren't in the saved config yet. A write that can make a
  push force or alias a subcommand (`remote.<name>.mirror` or `.push`, `alias.*`, `include*`, `git config --edit`,
  `git remote add --mirror=push`, `clone --mirror`) counts as config the hook can't read; one that only moves where a
  push goes (a remote's URL, `url.*`, `git remote add`, `set-url` or `rename`) counts as a repo the hook can't tell,
  which matters only when `[repos]` gives repos their own profiles. `push.*` settings change neither, so `git remote
  add origin ... && git push -u origin main` stays a plain push.
  Settings changed another way (an edit of `.git/config`, a script) stay a Limit.
