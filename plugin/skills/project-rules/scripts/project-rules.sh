#!/usr/bin/env bash
# project-rules.sh runs `tower rules` (src/tower/rules.py), which replaced this script. It keeps the skills
# working for one release; T19 deletes it.
exec python3 -I "$(dirname "$0")/../../../scripts/run-tower.py" rules "$@"
