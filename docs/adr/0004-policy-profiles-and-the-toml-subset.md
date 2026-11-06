# Approval policy: profiles in a TOML file the core reads itself, merges judged by asking gh

T09 (#26) makes every approval gate a key of a policy profile. The policy lives in `~/.claude/tower/policy.toml`: a `default_profile`, `[profiles.*]` tables and `[repos]` globs on `owner/repo`, with built-in `work`, `personal` and `sandbox` profiles. Anything unknown or invalid resolves to the strictest value, so a typo can only make tower ask more. The approval hook reads the policy through the core (`src/tower/policy.py`), and the safety floor (edits to the policy, tower.conf, rule files, `tower policy use`) stays in the hook, outside any profile.

The hook runs under macOS's Python 3.9, which has no `tomllib`, so the core reads the file with its own small parser for the part of TOML a policy needs: tables, bare or quoted keys, strings, whole numbers, true/false and comments. Anything else (arrays, inline tables, dotted keys, floats, dates) is reported as a problem, never read. One parser on every Python gives the same answer whether the hook runs on 3.9 or the CLI on 3.12, and tests check that it agrees with `tomllib` on what it accepts and refuses what `tomllib` refuses.

`merge_to_main` and `merge_to_branch` depend on a PR's base, which a command line doesn't show. When the two keys differ, the hook asks `gh` (`gh pr view` for the base and URL, `gh repo view` for the default branch), with the same PR and `-R` as the merge, in the session's folder, with a 5-second timeout per call. It does so only when the merge is the whole command line (one simple command, nothing before it), so a `cd`, a `GH_REPO=` or a `gh repo set-default` in the same line can't make it look up a different PR than the one merged. If gh fails, or the merge is chained, it asks unless both keys are `auto`.

## Considered options

- **`tomllib` when present, a fallback parser on 3.9**: two parsers can disagree, so the same file could mean different things to the hook and to `tower policy`.
- **Keep `key=value` lines in tower.conf**: no new parser, but per-profile tables and per-repo overrides would need an invented syntax that's no easier to parse safely than this TOML subset.
- **JSON**: the standard library reads it everywhere, but it has no comments, and the file is meant to be edited by hand.
- **Treat every merge as a merge into main**: no network call, but `merge_to_branch` would never apply, and the stacked-PR flow (T12) needs it.
- **Infer the base from git** (the branch's upstream or git-spice's parent): only correct for the current branch, and not what GitHub will actually merge into.
