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
      "is_main": true,
      "status": "main",
      "reason": "the main checkout",
      "holds_slot": false,
      "card": null,
      "stale": false,
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
      "is_main": false,
      "status": "running",
      "reason": "no card yet, but session w1 is busy",
      "holds_slot": true,
      "card": null,
      "stale": false,
      "sessions": [
        {
          "name": "w1",
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
      "label": "feat-3",
      "branch": "feat-3",
      "path": "/p/worktrees/feat-3",
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
      "sessions": [],
      "unshipped_commits": 1,
      "dirty": null,
      "pr": null
    },
    {
      "label": "feat-5",
      "branch": "feat-5",
      "path": "/p/worktrees/feat-5",
      "is_main": false,
      "status": "no-card",
      "reason": "no card yet",
      "holds_slot": false,
      "card": null,
      "stale": false,
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
      "commits": 1
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
    }
  ]
}
