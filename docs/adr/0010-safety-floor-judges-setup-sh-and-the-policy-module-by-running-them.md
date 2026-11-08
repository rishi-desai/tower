# The safety floor judges setup.sh and the policy module by what runs them, not what names or writes them

The safety floor asked whenever a Bash word's last path part was `setup.sh` and the word or the session's folder said
"tower", so workers on tower stalled on read-only greps of their own checkout (#100). Following ADR 0009, it now judges
what the command does with the file. A word naming setup.sh runs it unless the program only reads that word: an
argument of a read-only program (cat, grep), an operand of a git subcommand that only reads, stages or commits files
(diff, log, add) when nothing on the line can make git hand files to a program, or a word of a sed whose scripts only
print lines. A reader whose output goes into a file or becomes another command's words (`bash "$(ls setup.sh)"`)
isn't only reading. Code for an interpreter, awk or sed runs setup.sh wherever the code mentions it, since code can
build the path; the policy module counts wherever code imports it, heredocs included.

A write to setup.sh or `src/tower/policy.py` isn't a reason to ask by itself, though #100 asked for one (and for an
unresolvable write target to ask): neither changes the policy until something runs it, and the installed plugin runs
its own copy of tower's core, not a checkout's. A program that isn't a known reader still asks when it names setup.sh
(`cp`, `tee`, `sed -i`), because it may run it.

## Considered options

- **Ask for any write to either file too**: Edit and Write tool calls on them never asked, so a Bash-only gate guards
  nothing. The runs that need no prompt after an edit (a `./setup.sh --dry-run` edited to ignore the flag, a test run
  importing an edited policy module) are as open to an Edit as to a Bash write. Judging writes by name would also ask
  for `git checkout` or `ruff format` of them, stalling every worker on a policy or setup ticket, which is the problem
  #75 and #100 fix.

## Consequences

- Code that only mentions setup.sh asks (`awk '/setup.sh/' README.md`, `python3 -c 'print(open("setup.sh").read())'`),
  as code naming tower.conf does under ADR 0009; a sed that only prints doesn't.
- Some routes that asked before pass now, a departure from ADR 0009's "what can't be resolved still asks": setup.sh
  piped into code that runs what it reads, and git under config set outside the command that hands files to a program
  (diff.external, a textconv or filter). Both need code or config that runs a program, which already got through for
  a setup.sh named by a glob, a variable or a git alias; asking for every read instead would fail the ticket.
- The reader lists grow when a stall shows a program is safe: `shellcheck setup.sh` still asks.
