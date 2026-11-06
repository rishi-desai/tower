#!/usr/bin/env bash
# PROTOTYPE (#30): git-spice submit and merge retargeting against a private throwaway GitHub repo,
# one worktree per branch. Prints PR bases after each step.
# Usage: remote.sh OWNER/REPO   (must not exist yet; created private. Archive it after: gh repo archive -y OWNER/REPO)
set -uo pipefail
REPO=${1:?usage: remote.sh OWNER/REPO}
GS=(git-spice --no-prompt)
GITHUB_TOKEN=${GITHUB_TOKEN:-$(gh auth token)}
export GITHUB_TOKEN
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/gs-spike-remote.XXXX")
step() { printf '\n=== %s\n' "$*"; }
run() { printf '$ %s\n' "$*"; "$@" 2>&1; printf '[exit %s]\n' "$?"; }
prs() { gh pr list -R "$REPO" --state all --json number,headRefName,baseRefName,state \
  --jq 'sort_by(.number)[] | "#\(.number) \(.headRefName) -> \(.baseRefName) [\(.state)]"'; }
pr_of() { gh pr list -R "$REPO" --state all --head "$1" --json number --jq '.[0].number'; }
# Stacked PRs refuse the normal merge APIs (so `gh pr merge` fails); use merge-async and poll.
merge_async() {
  local pr uuid status=pending
  pr=$(pr_of "$1")
  printf '$ PUT pulls/%s/merge-async (squash)\n' "$pr"
  uuid=$(gh api -X PUT "repos/$REPO/pulls/$pr/merge-async" -f merge_method=squash -f merge_action=direct_merge --jq .details.uuid)
  for _ in $(seq 24); do
    status=$(gh api "repos/$REPO/pulls/$pr/merge-async/$uuid" --jq .status)
    [ "$status" = pending ] || break
    sleep 5
  done
  echo "merge-async: $status"
}

run gh repo create "$REPO" --private
git init -q -b main "$ROOT/repo" && cd "$ROOT/repo" || exit 1
git commit -q --allow-empty -m init
git remote add origin "https://github.com/$REPO.git"
git push -q -u origin main
run "${GS[@]}" repo init --trunk main --remote origin
for b in feat1 feat2 feat3; do echo "$b" >"$b.txt"; git add "$b.txt"; "${GS[@]}" branch create "$b" -m "$b" >/dev/null; done
git switch -q main
for b in feat1 feat2 feat3; do git worktree add -q "$ROOT/wt-$b" "$b"; done

step "Q3: stack submit from wt-feat1 while feat2/feat3 are checked out in other worktrees"
(cd "$ROOT/wt-feat1" && run "${GS[@]}" stack submit --fill --no-draft --nav-comment=false)
prs
step "GitHub's own view of PR 2 (base + native stack membership)"
gh api "repos/$REPO/pulls/$(pr_of feat2)" --jq '{base: .base.ref, stack}'
step "Plain gh pr merge on a stacked PR"
run gh pr merge -R "$REPO" "$(pr_of feat1)" --squash

step "Q4: merge feat1's PR; is feat2's PR retargeted?"
git fetch -q origin
before=$(git rev-parse --short origin/feat2)
merge_async feat1
sleep 3
prs
git fetch -q origin
echo "origin/feat2 before merge $before, after $(git rev-parse --short origin/feat2) (GitHub rebased it server-side)"

step "repo sync, default config: feat1 is checked out in wt-feat1, so it is skipped and feat2 is NOT retargeted locally"
run "${GS[@]}" repo sync
run "${GS[@]}" log short --all
(cd "$ROOT/wt-feat2" && run "${GS[@]}" branch submit)

step "repo sync with spice.repoSync.detachWorktrees=true: detaches wt-feat1, deletes feat1, retargets feat2"
git config spice.repoSync.detachWorktrees true
run "${GS[@]}" repo sync
run git -C "$ROOT/wt-feat1" status --short --branch
run "${GS[@]}" log short --all

step "Restack + submit after the git fetch above: works, overwriting GitHub's server-side rebase with an equivalent one"
# Without a fresh fetch, origin/feat2 is stale and git-spice's lease-protected push is rejected ("stale info").
(cd "$ROOT/wt-feat2" && run "${GS[@]}" branch restack && run "${GS[@]}" branch submit)
step "Alternative: take GitHub's rebased branch instead of pushing your own (same tree), then restack above it"
(cd "$ROOT/wt-feat2" && git reset -q --hard origin/feat2 && run "${GS[@]}" branch restack)
(cd "$ROOT/wt-feat3" && git reset -q --hard origin/feat3 && run "${GS[@]}" branch restack)
run "${GS[@]}" log short --all
step "Amend feat3 and resubmit: force-pushing a stacked branch works"
(cd "$ROOT/wt-feat3" && echo more >>feat3.txt && git commit -qam "feat3 more" && run "${GS[@]}" branch submit)

step "Merge feat2's PR, sync from the main checkout, check feat3's PR"
merge_async feat2
sleep 3
run "${GS[@]}" repo sync
prs
run "${GS[@]}" log short --all
echo "Workdir: $ROOT"
