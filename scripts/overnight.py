#!/usr/bin/env python3
"""overnight.py: run the tower A+ tickets unattended, starting each one in Orca once its blockers have merged.

Every worker branches from, and merges into, the integration branch (never main). In the morning you review one
pull request, integration branch -> main.

Loop, every --interval seconds:
  1. For each ticket it started: if a merged PR into the integration branch says "Closes #N", the ticket is done
     and this script closes the issue. A draft PR means the worker got stuck. No PR after --max-hours also counts
     as stuck. A stuck ticket frees its slot, and anything blocked by it waits for you.
  2. Starts tickets whose "Blocked by:" issues are all done (closed, or merged into the integration branch),
     up to --wip running at once, in the order of TICKETS. No new starts after --stop-starting-at.
  3. Exits when nothing is running and nothing more can start.

Usage:
  python3 overnight.py --dry-run --once     # shows what it would do, changes nothing
  caffeinate -dims python3 overnight.py     # the real run; caffeinate keeps the Mac awake

Needs: gh (logged in), orca, git. Standard library only.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import subprocess
import sys
import tempfile
import time

REPO = "rishi-desai/tower"
BASE = "integration/a-plus"
REPO_PATH = pathlib.Path.home() / "Developer/personal/tower"
STATE = pathlib.Path.home() / ".claude/overnight-state.json"
LOG = pathlib.Path.home() / ".claude/overnight.log"

# issue number: (effort, Orca worktree name, start skill). Order = start priority.
# Left out on purpose: #18 (ci repo), #20 (GitHub settings, you), #38 and #39 (discuss first),
# #40 (blocked by the parent issue, so it runs last, by hand).
TICKETS: dict[int, tuple[str, str, str]] = {
    19: ("high", "chore/19-shared-ci", "/implement"),
    23: ("medium", "fix/23-wayfinder", "/diagnosing-bugs"),
    21: ("xhigh", "fix/21-hook-parsing", "/diagnosing-bugs"),
    22: ("high", "feat/22-core-events", "/implement"),
    24: ("high", "feat/24-effective-state", "/implement"),
    25: ("medium", "feat/25-stats", "/implement"),
    26: ("xhigh", "feat/26-policy-profiles", "/implement"),
    27: ("high", "feat/27-port-scans", "/implement"),
    29: ("high", "feat/29-pane-v2", "/implement"),
    30: ("medium", "spike/30-git-spice", "/prototype"),
    28: ("high", "feat/28-rendered-cards", "/implement"),
    31: ("high", "feat/31-cleanup", "/implement"),
    32: ("high", "feat/32-stacks", "/implement"),
    33: ("high", "feat/33-conflicts", "/implement"),
    34: ("medium", "feat/34-multi-repo", "/implement"),
    35: ("medium", "feat/35-adopt-into-hub", "/implement"),
    36: ("medium", "feat/36-doctor", "/implement"),
    37: ("medium", "chore/37-one-plugin", "/implement"),
}

RULES = """You are running unattended overnight and I'm asleep. Follow these rules exactly:
1. Never ask me a question or wait for input. When a choice comes up, take the option you'd recommend and list it under "Decisions made without you" in the PR description.
2. {base_step}
3. Read the whole issue first, including its Blocked by line.
4. Before the PR: if the repo has a mise.toml, `mise run ci` must pass. Then run mattpocock-skills:code-review by that full name, then the built-in /code-review {review}. Fix what they find.
5. Open the PR with `gh pr create --base {base}` and put `Closes #{n}` in its body. Never target main. Never use --admin.
6. When its checks pass: `git fetch origin && git merge origin/{base}` (no rebase, no force push), push, wait for the checks again, then `gh pr merge --squash --delete-branch`.
7. Any write-up the issue asks for (findings, notes) goes in a file in your PR, never in an issue comment. Don't comment on GitHub at all.
8. If you're truly blocked, open the PR as a draft (`gh pr create --draft --base {base}`) explaining exactly what blocks you, and stop."""

BASE_STEP = ("Your branch starts from origin/{base}. Before any work, run `git fetch origin && git merge origin/{base}` "
             "in case it moved since; don't change your branch's base.")

CLOSES = r"(?i)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#{n}\b"


def log(msg: str) -> None:
    line = f"{dt.datetime.now():%H:%M} {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def notify(msg: str) -> None:
    """A macOS notification; silently skipped elsewhere."""
    try:
        subprocess.run(["osascript", "-e", f"display notification {json.dumps(msg)} with title \"overnight\""],
                       capture_output=True, check=False)
    except OSError:
        pass


def gh_json(*args: str) -> list[dict]:
    out = subprocess.run(["gh", *args], capture_output=True, text=True, check=True).stdout
    return json.loads(out or "[]")


def blockers(body: str) -> set[int]:
    """Issue numbers on the body's 'Blocked by:' line ('nothing' -> none)."""
    for line in body.splitlines():
        if line.lower().startswith("blocked by:"):
            return {int(n) for n in re.findall(r"#(\d+)", line)}
    return set()


def pr_for(n: int, prs: list[dict]) -> dict | None:
    """The newest PR whose body closes issue N (merged first, then open)."""
    found = [p for p in prs if re.search(CLOSES.format(n=n), p.get("body") or "")]
    merged = [p for p in found if p.get("mergedAt")]
    return (merged or found or [None])[0]


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"started": {}, "done": [], "stuck": []}


def save_state(state: dict) -> None:
    STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def kickoff(n: int, title: str, effort: str, start: str) -> str:
    review = "xhigh" if effort == "xhigh" else "high"
    head = f"/prototype {title} — #{n}." if start == "/prototype" else f"{start} #{n} — {title}."
    base_step = BASE_STEP.format(base=BASE)
    rules = RULES.format(base_step=base_step, review=review, base=BASE, n=n)
    return head + "\n\n" + rules


def start(n: int, title: str, args: argparse.Namespace) -> bool:
    effort, name, skill = TICKETS[n]
    text = kickoff(n, title, effort, skill)
    if args.dry_run:
        log(f"would start #{n} ({effort}, {name})")
        return True
    with tempfile.NamedTemporaryFile("w", prefix=f"kickoff-{n}-", suffix=".txt", delete=False) as f:
        f.write(text)
    # Fetch first, so the worktree starts from the integration branch as it is now, with its blockers merged.
    fetch = subprocess.run(["git", "-C", args.repo_path, "fetch", "origin", BASE], capture_output=True, text=True,
                           check=False)
    if fetch.returncode != 0:
        log(f"git fetch failed for #{n}, not starting it: {fetch.stderr.strip()}")
        return False
    cmd = ["orca", "worktree", "create", "--repo", f"path:{args.repo_path}", "--name", name, "--no-parent",
           "--base-branch", f"origin/{BASE}",
           "--issue", str(n), "--agent", "claude", "--model", args.model, "--effort", effort,
           "--surface", "terminal", "--json", "--prompt", text]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        log(f"orca failed for #{n}: {result.stderr.strip() or result.stdout.strip()}")
        return False
    log(f"started #{n} ({effort}, {name}); kickoff in {f.name}")
    return True


def tick(args: argparse.Namespace, state: dict) -> bool:
    """One pass. Returns False when there's nothing left to do."""
    issues = {i["number"]: i for i in gh_json("issue", "list", "--repo", REPO, "--state", "all", "--limit", "200",
                                               "--json", "number,state,title,body,labels")}
    prs = gh_json("pr", "list", "--repo", REPO, "--base", BASE, "--state", "all", "--limit", "200",
                  "--json", "number,body,isDraft,mergedAt,state")
    now = dt.datetime.now()

    for key, started_at in list(state["started"].items()):
        n = int(key)
        if n in state["done"] or n in state["stuck"]:
            continue
        pr = pr_for(n, prs)
        if issues.get(n, {}).get("state") == "CLOSED":
            state["done"].append(n)
            log(f"#{n} done (issue closed)")
        elif pr and pr.get("mergedAt"):
            state["done"].append(n)
            log(f"#{n} done: PR #{pr['number']} merged into {BASE}")
            if not args.dry_run:
                subprocess.run(["gh", "issue", "close", str(n), "--repo", REPO, "--comment",
                                f"Merged into `{BASE}` in #{pr['number']} (overnight run)."], check=False)
        elif pr and pr.get("isDraft"):
            state["stuck"].append(n)
            log(f"#{n} STUCK: draft PR #{pr['number']}")
            notify(f"#{n} is stuck: see draft PR #{pr['number']}")
        elif not pr and now - dt.datetime.fromisoformat(started_at) > dt.timedelta(hours=args.max_hours):
            state["stuck"].append(n)
            log(f"#{n} STUCK: no PR after {args.max_hours}h")
            notify(f"#{n} has no PR after {args.max_hours}h")

    done = set(state["done"]) | {n for n, i in issues.items() if i["state"] == "CLOSED"}
    running = [int(k) for k in state["started"] if int(k) not in done and int(k) not in state["stuck"]]
    startable = []
    for n in TICKETS:
        issue = issues.get(n)
        if not issue or issue["state"] == "CLOSED" or str(n) in state["started"]:
            continue
        if "ready-for-agent" not in {label["name"] for label in issue["labels"]}:
            continue
        if blockers(issue["body"]) <= done:
            startable.append(n)

    stop_hour, stop_minute = map(int, args.stop_starting_at.split(":"))
    may_start = (now.hour, now.minute) < (stop_hour, stop_minute) or now.hour >= 18
    for n in startable:
        if len(running) >= args.wip or not may_start:
            break
        if start(n, issues[n]["title"], args):
            state["started"][str(n)] = now.isoformat(timespec="minutes")
            running.append(n)
        else:
            state["stuck"].append(n)

    if not args.dry_run:
        save_state(state)
    log(f"running {sorted(running)} | done {sorted(set(state['done']))} | stuck {sorted(state['stuck'])}")
    return bool(running) or (bool(startable) and may_start)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo-path", default=str(REPO_PATH), help="main checkout of tower")
    p.add_argument("--model", default="opus", help="model for workers (Orca needs --model with --effort)")
    p.add_argument("--wip", type=int, default=3, help="workers running at once")
    p.add_argument("--interval", type=int, default=300, help="seconds between passes")
    p.add_argument("--max-hours", type=float, default=4, help="a worker with no PR after this long is stuck")
    p.add_argument("--stop-starting-at", default="07:00", help="no new starts after this time (HH:MM)")
    p.add_argument("--already-started", default="",
                   help="comma-separated issues you started by hand (e.g. 19,23): tracked, never relaunched")
    p.add_argument("--dry-run", action="store_true", help="show what it would do; change nothing")
    p.add_argument("--once", action="store_true", help="one pass, then exit")
    args = p.parse_args()

    state = load_state()
    for n in filter(None, args.already_started.split(",")):
        state["started"].setdefault(n.strip().lstrip("#"), dt.datetime.now().isoformat(timespec="minutes"))
    log(f"overnight run: base {BASE}, wip {args.wip}{' (dry run)' if args.dry_run else ''}")
    while True:
        try:
            keep_going = tick(args, state)
        except subprocess.CalledProcessError as e:
            log(f"gh failed, retrying next pass: {e.stderr.strip() if e.stderr else e}")
            keep_going = True
        if args.once:
            return 0
        if not keep_going:
            break
        time.sleep(args.interval)
    log("finished: nothing running and nothing left to start")
    notify("overnight run finished")
    return 0


if __name__ == "__main__":
    sys.exit(main())
