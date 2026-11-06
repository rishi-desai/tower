#!/usr/bin/env bash
# PROTOTYPE (#30): the recommended layout, ONE worktree for the whole stack, against a private throwaway GitHub repo.
# Also captures: REST merge refusal, push rejected without a fresh fetch, same tree / different SHA after GitHub's rebase.
# Usage: remote-stack.sh OWNER/REPO   (must not exist yet; created private. Archive it after: gh repo archive -y OWNER/REPO)
set -uo pipefail
REPO=${1:?usage: remote-stack.sh OWNER/REPO}
GS=(git-spice --no-prompt)
GITHUB_TOKEN=${GITHUB_TOKEN:-$(gh auth token)}
export GITHUB_TOKEN
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/gs-spike-stack.XXXX")
step() { printf '\n=== %s\n' "$*"; }
run() { printf '$ %s\n' "$*"; "$@" 2>&1; printf '[exit %s]\n' "$?"; }
prs() { gh pr list -R "$REPO" --state all --json number,headRefName,baseRefName,state \
  --jq 'sort_by(.number)[] | "#\(.number) \(.headRefName) -> \(.baseRefName) [\(.state)]"'; }
pr_of() { gh pr list -R "$REPO" --state all --head "$1" --json number --jq '.[0].number'; }
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
run gh api "repos/$REPO" --jq '{delete_branch_on_merge}'
git init -q -b main "$ROOT/repo" && cd "$ROOT/repo" || exit 1
git commit -q --allow-empty -m init
git remote add origin "https://github.com/$REPO.git"
git push -q -u origin main
run "${GS[@]}" repo init --trunk main --remote origin
step "One worktree (wt-stack) holds the whole stack; the main checkout is parked detached"
git switch -q --detach
git worktree add -q "$ROOT/wt-stack" main
cd "$ROOT/wt-stack" || exit 1
for b in feat1 feat2 feat3; do echo "$b" >"$b.txt"; git add "$b.txt"; "${GS[@]}" branch create "$b" -m "$b" >/dev/null; done
run "${GS[@]}" stack submit --fill --no-draft --nav-comment=false
prs
[ -n "$(pr_of feat3)" ] || { echo "setup failed, stopping"; exit 1; }
step "Moving around the stack inside the one worktree"
run "${GS[@]}" down
run "${GS[@]}" bottom
run git branch --show-current
run "${GS[@]}" top

step "The plain REST merge is refused for stacked PRs too"
run gh api -X PUT "repos/$REPO/pulls/$(pr_of feat1)/merge" -f merge_method=squash

step "Merge feat1 while wt-stack sits on the top branch (feat3)"
merge_async feat1
sleep 3
prs
run "${GS[@]}" repo sync
run "${GS[@]}" log short --all
step "GitHub rebased feat2 server-side: same tree as git-spice's restack, different SHA"
run "${GS[@]}" upstack restack --branch feat2
printf 'local feat2 %s, origin/feat2 (stale tracking ref) %s\n' "$(git rev-parse --short feat2)" "$(git rev-parse --short origin/feat2)"
step "Submit without a fresh fetch: the lease-protected push is rejected"
run "${GS[@]}" stack submit
step "git fetch, compare, submit again"
git fetch -q origin
printf 'local feat2 %s, origin/feat2 %s, same tree: ' "$(git rev-parse --short feat2)" "$(git rev-parse --short origin/feat2)"
git diff --quiet feat2 origin/feat2 && echo yes || echo no
run "${GS[@]}" stack submit
prs

step "Merge feat2 while wt-stack sits ON feat2 (the merged branch), sync from wt-stack itself"
run "${GS[@]}" branch checkout feat2
merge_async feat2
sleep 3
run "${GS[@]}" repo sync
run git branch --show-current
run "${GS[@]}" log short --all
prs
echo "Workdir: $ROOT"
