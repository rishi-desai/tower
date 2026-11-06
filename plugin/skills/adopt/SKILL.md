---
name: adopt
description: Old name for tower:hub's adopt mode, kept for one release. Use only when the user types /tower:adopt or names this skill; for anything else use tower:hub, which switches to adopt mode by itself when a repo needs it.
effort: medium
---

# Adopt (moved into the hub)

Adopting a repo is now part of the `tower:hub` skill. Tell the user in one line that `/tower:hub` does this from now on, and that this name goes away in the next release. Then invoke the `tower:hub` skill and follow its adopt mode (its `ADOPT.md`), even if the scan's `ADOPT:` line says `no`: the user asked to adopt.
