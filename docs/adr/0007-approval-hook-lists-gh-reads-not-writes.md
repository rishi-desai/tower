# The approval hook lists the gh commands that only read, not the ones that write

The approval hook used to know the write subcommands of four `gh` groups (`pr`, `issue`, `release`, `label`) and let
every other `gh` command through, so `gh repo delete`, `gh secret set` and `gh workflow run` ran with no prompt even
under the work profile (#59). It now lists the opposite: the known reads, the `gh` commands that only read GitHub or
work locally (`view`, `list`, `status`, `diff`, `checks`, `gh repo clone`, `gh pr checkout`, ...). `gh pr` and
`gh issue` writes are still judged by their own policy keys; every other `gh` command asks unless every GitHub key
is auto, as release and label writes did. `gh` adds commands in most releases, and a list of writes misses each new
one silently, while a list of reads only costs a prompt until the new read is added.

## Consequences

- A read a newer `gh` adds asks under work and personal until it joins `GH_READS`.
- A `gh` extension or alias asks, since its name says nothing about what it does. gh's built-in aliases (`gh pr ls`,
  `gh cs list`) can't be redefined, so they are read as what they stand for; `gh co`, a default alias in gh's config,
  asks like any other.
- `gh auth token` and `gh auth status --show-token` ask: they print a credential, so they aren't listed as reads.
