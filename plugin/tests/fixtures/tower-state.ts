// `tower state --json` for the shared fixture in tests/test_state.py (temporary paths shortened to /p and
// /vault/repo/Ledger). That test checks this is the core's own output, so the pane's tests draw what the core
// decided. Don't edit by hand: when the JSON changes on purpose, paste what the test prints.
import type { TowerState } from '../../types'

export const SHARED: TowerState = {
  "repo": "repo",
  "main_checkout": "/p",
  "ledger": "/vault/repo/Ledger",
  "base": "main",
  "wip_limit": 3,
  "slots": 5,
  "at_limit": true,
  "sessions_source": "live",
  "notes": [],
  "worktrees": [
    {
      "label": "main",
      "branch": "main",
      "path": "/p",
      "card_file": "main.md",
      "is_main": true,
      "status": "main",
      "reason": "the main checkout",
      "holds_slot": false,
      "card": null,
      "stale": false,
      "card_unknown": false,
      "sessions": [
        {
          "name": "hub-1",
          "kind": "interactive",
          "activity": "idle",
          "waiting_for": ""
        }
      ],
      "unshipped_commits": null,
      "dirty": null,
      "pr": null
    },
    {
      "label": "feat-1",
      "branch": "feat-1",
      "path": "/p/worktrees/feat-1",
      "card_file": "feat-1.md",
      "is_main": false,
      "status": "running",
      "reason": "no card yet, but session w1 is busy",
      "holds_slot": true,
      "card": null,
      "stale": false,
      "card_unknown": false,
      "sessions": [
        {
          "name": "w1",
          "kind": "interactive",
          "activity": "busy",
          "waiting_for": ""
        }
      ],
      "unshipped_commits": 2,
      "dirty": null,
      "pr": null
    },
    {
      "label": "feat-3",
      "branch": "feat-3",
      "path": "/p/worktrees/feat-3",
      "card_file": "feat-3.md",
      "is_main": false,
      "status": "needs-you",
      "reason": "the card says needs-you; no session here is waiting on you on this machine",
      "holds_slot": true,
      "card": {
        "status": "needs-you",
        "ticket": "#3",
        "updated": "2026-11-06T09:00",
        "pr": "none"
      },
      "stale": false,
      "card_unknown": false,
      "sessions": [],
      "unshipped_commits": 1,
      "dirty": null,
      "pr": null
    },
    {
      "label": "feat-5",
      "branch": "feat-5",
      "path": "/p/worktrees/feat-5",
      "card_file": "feat-5.md",
      "is_main": false,
      "status": "no-card",
      "reason": "no card yet",
      "holds_slot": false,
      "card": null,
      "stale": false,
      "card_unknown": false,
      "sessions": [
        {
          "name": "w5",
          "kind": "interactive",
          "activity": "idle",
          "waiting_for": ""
        }
      ],
      "unshipped_commits": 1,
      "dirty": null,
      "pr": null
    },
    {
      "label": "feat-7",
      "branch": "feat-7",
      "path": "/p/worktrees/feat-7",
      "card_file": "feat-7.md",
      "is_main": false,
      "status": "running",
      "reason": "the card says needs-you but session w7 is busy: you already answered",
      "holds_slot": true,
      "card": {
        "status": "needs-you",
        "ticket": "#7",
        "updated": "2026-11-06T09:00",
        "pr": "none"
      },
      "stale": false,
      "card_unknown": false,
      "sessions": [
        {
          "name": "w7",
          "kind": "interactive",
          "activity": "busy",
          "waiting_for": ""
        }
      ],
      "unshipped_commits": 1,
      "dirty": null,
      "pr": null
    },
    {
      "label": "feat-8",
      "branch": "feat-8",
      "path": "/p/worktrees/feat-8",
      "card_file": "feat-8.md",
      "is_main": false,
      "status": "needs-you",
      "reason": "session w8 is waiting on you (approve Bash); the card says running",
      "holds_slot": true,
      "card": {
        "status": "running",
        "ticket": "#8",
        "updated": "2026-11-06T09:00",
        "pr": "none"
      },
      "stale": false,
      "card_unknown": false,
      "sessions": [
        {
          "name": "w8",
          "kind": "interactive",
          "activity": "waiting",
          "waiting_for": "approve Bash"
        }
      ],
      "unshipped_commits": 1,
      "dirty": null,
      "pr": null
    },
    {
      "label": "feat-9",
      "branch": "feat-9",
      "path": "/p/worktrees/feat-9",
      "card_file": "feat-9.md",
      "is_main": false,
      "status": "needs-you",
      "reason": "session w9 is waiting on you (input needed); the card says running",
      "holds_slot": true,
      "card": {
        "status": "running",
        "ticket": "#9",
        "updated": "2026-11-06T09:00",
        "pr": "none"
      },
      "stale": false,
      "card_unknown": false,
      "sessions": [
        {
          "name": "w9",
          "kind": "interactive",
          "activity": "waiting",
          "waiting_for": "input needed"
        }
      ],
      "unshipped_commits": 1,
      "dirty": null,
      "pr": null
    }
  ],
  "unshipped": [
    {
      "label": "feat-1",
      "commits": 2
    },
    {
      "label": "feat-3",
      "commits": 1
    },
    {
      "label": "feat-5",
      "commits": 1
    },
    {
      "label": "feat-7",
      "commits": 1
    },
    {
      "label": "feat-8",
      "commits": 1
    },
    {
      "label": "feat-9",
      "commits": 1
    }
  ],
  "orphan_cards": [
    {
      "name": "feat__2-gone",
      "status": "done",
      "path": "/vault/repo/Ledger/feat__2-gone.md"
    }
  ],
  "overlaps": [],
  "housekeeping": [
    {
      "label": "feat-1",
      "note": "no card"
    },
    {
      "label": "feat-5",
      "note": "no card"
    },
    {
      "label": "feat__2-gone",
      "note": "orphan card (worktree gone), status: done"
    },
    {
      "label": "feat-1",
      "note": "feat-1 needs a restack (its parent feat-0 moved): run git-spice stack restack there"
    }
  ],
  "policy": {
    "profile": "personal",
    "why": "default_profile in /tower/policy.toml",
    "push": "force",
    "merge_to_main": "ask",
    "merge_to_branch": "auto",
    "comments": "ask",
    "issue_writes": "auto",
    "pr_writes": "auto",
    "cleanup": "ask",
    "problems": []
  },
  "stacks": [
    {
      "base": "main",
      "merge_key": "merge_to_main",
      "next_note": "next to merge: feat-0, PR #10 (merge_to_main is ask: it waits for you)",
      "worktrees": [
        "/p/worktrees/feat-1"
      ],
      "branches": [
        {
          "name": "feat-0",
          "ticket": "#0",
          "parent": "main",
          "depth": 0,
          "worktree": null,
          "pr": 10,
          "pr_state": null,
          "merged": false,
          "restack": "",
          "next": true
        },
        {
          "name": "feat-1",
          "ticket": "#1",
          "parent": "feat-0",
          "depth": 1,
          "worktree": "/p/worktrees/feat-1",
          "pr": 11,
          "pr_state": null,
          "merged": false,
          "restack": "its parent feat-0 moved",
          "next": false
        }
      ]
    }
  ],
  "in_progress": {
    "#3": "needs-you in feat-3",
    "#7": "running in feat-7",
    "#8": "needs-you in feat-8",
    "#9": "needs-you in feat-9",
    "#0": "in the stack in feat-1",
    "#1": "in the stack in feat-1"
  }
}
