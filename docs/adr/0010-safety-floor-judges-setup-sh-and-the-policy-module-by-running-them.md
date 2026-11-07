# The safety floor judges setup.sh and the policy module by what runs them, not what names or writes them

The safety floor asked whenever a Bash word's last path part was `setup.sh` and the word or the session's folder said
"tower", so workers on tower stalled on read-only greps of their own checkout (#100). Following ADR 0009, it now judges
what the command does with the file. A word naming setup.sh runs it unless the program only reads that word: an
argument of a read-only program (cat, grep), an operand of a git subcommand that only reads, stages or commits files
(diff, log, add), or an input file of sed with only options that print. Code for an interpreter, awk or sed runs it
wherever the code mentions it, since code can build the path; the same goes for the policy module, which counts
wherever code imports it, heredocs included.

Writing setup.sh or `src/tower/policy.py` isn't judged, though #100 asked for it: neither changes the policy until
something runs it (`./setup.sh`, `tower policy use`, which ask), since the installed plugin runs its own copy of
tower's core, not a checkout's.

## Considered options

- **Ask for any write to either file too**: Edit and Write tool calls on them never asked, so a Bash-only gate guards
  nothing, and judging writes by name would ask for `git add`, `ruff format` and `git checkout` of them, stalling every
  worker on a policy or setup ticket, which is the problem #75 and #100 fix.

## Consequences

- A reader that writes its output into a file (`cat setup.sh > x.sh`) still asks, since the copy can run under
  another name; one whose output a later command runs (`bash "$(ls setup.sh)"`) doesn't, like a glob or a variable
  set outside the command, which never asked.
- A program that isn't a known reader still asks when it names setup.sh (`shellcheck setup.sh`, `sed -i`), since it
  may run it: the reader lists grow when a stall shows a program is safe.
