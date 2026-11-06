# Tower's scripts print GitHub writes; the agent runs them

Tower's approval hook (`plugin/scripts/confirm-public.py`) only sees the command text the agent runs, so a script that calls `gh label create` or the GitHub API itself would make a public change with no approval prompt. So a tower script that decides on a GitHub write prints the exact command instead of running it, and the agent runs what it printed, where the hook asks the user; if they decline, the same printed command is what the agent shows them to run by hand. First case: `plugin/skills/hub/scripts/wayfinder-labels.py` prints the `gh label create` commands for missing `wayfinder:*` labels (#8).

## Considered options

- **The script creates the labels itself**: one step fewer, but it skips the approval prompt, which tower's safety model depends on.
- **The hub works it out in prose** (`gh label list`, compare, create): no code, but no test, and it misses that `gh label list` returns only 30 labels by default.
