# The approval hook splits commands with its own small parser, not shlex

The approval hook (`plugin/scripts/confirm-public.py`) must find every `git push` and GitHub write in a Bash command line, and it runs under macOS's stdlib-only `python3 -I`, so no third-party shell parser is available. T04 (#21) planned `shlex` tokenising, but `shlex` doesn't know heredocs, `$( )` or backticks. It merges a heredoc body with the commands after it, which hid a `gh pr comment` that the old regexes caught. It also raises on Claude Code's own `git commit -m "$(cat <<'EOF' ... EOF)"` pattern whenever the body has an odd number of double quotes, and "can't tokenise, so ask" would then prompt on ordinary commits and `gh pr create --body` calls, which stalls unattended workers. So the hook has a small parser (`CommandSplitter`) that splits a command line into simple commands the way bash and zsh do. It handles quotes, escapes, comments, heredocs, `$( )`, backticks, `<( )`, case statements and arithmetic. When it can't split a command, or the command is too big to check within a fixed work budget, it asks.

## Considered options

- **`shlex` with "unparseable asks"**: fewer lines, but the heredoc merge is a bypass the regexes didn't have, and the commit-pattern errors would prompt constantly.
- **Keep the regexes and add more of them**: every fix would need another regex for another quoting or ordering case, and quoted text that merely mentions `git push` would keep prompting.
- **Ask bash itself** (`bash -n`, or a `bash -c` parse hook): bash has no machine-readable parse output, and zsh, which this machine's Bash tool runs commands in, differs in places.

## Consequences

The work budget bounds only the work charged to it. A check that runs before anything is charged for it (the safety floor's, or a per-word test of what a word runs: the program, an interpreter given code, tower) must stay linear in the command's length, and a check that is charged pays for every word it reads, those before its own word included. One that rescans the rest of the line, or the words before its own, once per word grows with the square of the padding: lines that fit the budget ran 12 to 90 seconds, and longer ones outlast the hook's timeout, which lets the command through (#105). A linear check finds what it needs once per command (the program's position, the first interpreter, the first word naming tower) and scans once from there; one whose start can't be pinned down (any env or sudo word may be a wrapper that runs) pays for each scan instead.
