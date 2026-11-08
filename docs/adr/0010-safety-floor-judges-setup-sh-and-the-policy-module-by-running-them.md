# The safety floor judges setup.sh and the policy module by running or writing them, and reads only narrowly

The safety floor asked whenever a Bash word's last path part was `setup.sh` and the word or the session's folder said
"tower", so workers on tower stalled on read-only greps of their own checkout (#100). Following ADR 0009, it now judges
what the command does with the file, but — unlike tower.conf, which resolves to one real path — "runs setup.sh" has a
long tail of forms (a pipe into a shell, git's external diff or pager, a reader whose output is captured). So the floor
inverts the question: it **asks for any naming of setup.sh or the policy module (src/tower/policy.py), and lets only a
narrow, provably-read shape through.**

A read passes only when the line is one top-level simple command (no pipe, no second command, no `$( )`, no wrapper),
with no redirect writing a file, whose program is a plain file reader (cat, grep, head, ...), a sed whose one script
only prints lines, or git with a read/stage/show subcommand (diff, log, show, status, add, ls-files) on a clean line:
no `-c`/`--config-env`, no word from a variable, no option that hands a file to a program (`--ext-diff`, `--textconv`,
`--output`, grep's `-O`), and nothing on the line that can make git run a program (`GIT_EXTERNAL_DIFF`, a pager or
editor variable, a `git config` of such a key). Everything else asks: running setup.sh (except `./setup.sh --dry-run`
and `--help`, which only check), writing to either file by redirect, in-place edit or copy (`echo >`, `sed -i`, `cp`,
`tee`), a reader whose output is piped or captured, and interpreter/awk/sed code that names setup.sh or imports the
policy module.

A write to setup.sh or the policy module asks (#100's acceptance), judged by the file a path resolves to, not the
word. The earlier draft of this ADR argued writes needn't ask; the owner chose to keep the ticket's acceptance, and a
Bash write gate is cheap defense in depth even though the Edit/Write tools don't gate these files (they are not under
`~/.claude`, so `check_protected_edit` doesn't cover them — a known limit, below).

## Considered options

- **Judge each read form cleverly (enumerate safe git options, trace pipelines)**: the first draft did this and a
  multi-agent review found fifteen evasions — git external diff, pager, grep `-O`, the editor, GNU vs BSD sed, a
  reader piped or captured, two regex denial-of-service inputs. Each added route was another place to be wrong. The
  single-command rule is coarser but provably fails closed, which is the floor's whole purpose.
- **Keep passing writes to the two files**: rejected; it misses #100's acceptance and drops a cheap Bash-side gate.

## Consequences

- The read exemption is deliberately conservative: a read in a pipeline or after another command asks
  (`cat setup.sh | less`, `cd repo && grep x setup.sh`). The real stalls (#93, #100) were single greps, which pass.
  A worker that hits a false ask can read the file with the Read tool or split the command.
- Config set outside the command still isn't seen: a `diff.external` in `~/.gitconfig` lets `git diff -- setup.sh` run
  a program with no prompt. This is the pre-existing "config outside the command" limit (ADR 0009); the sandbox's
  write-deny on tower's files is the backstop.
- A write to a bare basename after a `cd` into `src/tower` (`cd src/tower && sed -i … policy.py`) isn't caught: the
  module is known by its repo path, and a bare `policy.py` is too common to claim. A sed `w`/`r` filename inside the
  script word is likewise not parsed.
