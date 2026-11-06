#!/usr/bin/env bash
# PROTOTYPE (#30): git-spice across git worktrees, local only (no remote). Throwaway; prints state after each step.
set -uo pipefail
GS=(git-spice --no-prompt)
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/gs-spike-local.XXXX")
step() { printf '\n=== %s\n' "$*"; }
run() { printf '$ %s\n' "$*"; "$@" 2>&1; printf '[exit %s]\n' "$?"; }

git init -q -b main "$ROOT/repo" && cd "$ROOT/repo" || exit 1
git commit -q --allow-empty -m init
run "${GS[@]}" repo init --trunk main

step "Build stack feat1 -> feat2 -> feat3 in the main checkout"
for b in feat1 feat2 feat3; do echo "$b" >"$b.txt"; git add "$b.txt"; run "${GS[@]}" branch create "$b" -m "$b"; done
git switch -q main

step "Q1: one worktree per branch; is tracking shared?"
for b in feat1 feat2 feat3; do git worktree add -q "$ROOT/wt-$b" "$b"; done
run git for-each-ref refs/spice
(cd "$ROOT/wt-feat2" && run "${GS[@]}" log short)
(cd "$ROOT/wt-feat3" && echo f4 >f4.txt && git add f4.txt && run "${GS[@]}" branch create feat4 -m feat4)
step "feat4 was created inside wt-feat3; seen from the main checkout?"
run "${GS[@]}" log short --all

step "Q1b: a worktree made outside git-spice (what 'orca worktree create --base-branch feat1' does) is untracked until 'branch track'"
git worktree add -q -b orcaish "$ROOT/wt-orcaish" feat1
(cd "$ROOT/wt-orcaish" && echo o >o.txt && git add o.txt && git commit -q -m orcaish \
  && run "${GS[@]}" log short && run "${GS[@]}" branch track && run "${GS[@]}" log short)
git worktree remove --force "$ROOT/wt-orcaish" && "${GS[@]}" branch delete --force orcaish >/dev/null 2>&1

step "Q2: trunk moves; restack from wt-feat1 while feat2/feat3/feat4 are checked out elsewhere"
echo t >trunk.txt && git add trunk.txt && git commit -q -m "trunk moves"
(cd "$ROOT/wt-feat1" && run "${GS[@]}" stack restack && run "${GS[@]}" log short)
step "repo restack from the main checkout (on main)"
run "${GS[@]}" repo restack
run "${GS[@]}" log short --all
step "Restack bottom up, one worktree at a time (wt-feat3 now holds feat4: branch create moved it)"
(cd "$ROOT/wt-feat1" && run "${GS[@]}" branch restack)
(cd "$ROOT/wt-feat2" && run "${GS[@]}" branch restack)
(cd "$ROOT/wt-feat3" && run "${GS[@]}" upstack restack --branch feat3)
run "${GS[@]}" log short --all

step "Q2b: one worktree per stack (branches not checked out anywhere else)"
git worktree remove "$ROOT/wt-feat2" && git worktree remove "$ROOT/wt-feat3"
git -C "$ROOT/wt-feat1" switch -q --detach
git worktree add -q "$ROOT/wt-stack" feat1
echo t2 >trunk2.txt && git add trunk2.txt && git commit -q -m "trunk moves again"
(cd "$ROOT/wt-stack" && run "${GS[@]}" stack restack && run "${GS[@]}" log short --all)

step "Plain git, for comparison: rebase a branch checked out in another worktree"
git worktree add -q "$ROOT/wt-plain" feat2
run git rebase main feat2
echo "Workdir: $ROOT"
