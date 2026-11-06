#!/usr/bin/env bash
# card-path.sh runs `tower card-path` (src/tower/ledger.py), which replaced this script. It keeps the skills
# working for one release; T19 deletes it.
exec python3 -I "$(dirname "$0")/../../../scripts/run-tower.py" card-path "$@"
