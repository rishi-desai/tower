#!/usr/bin/env bash
# hub-scan.sh runs `tower scan` (src/tower/scan.py), which replaced this script. It keeps the skills
# working for one release; T19 deletes it.
exec python3 -I "$(dirname "$0")/../../../scripts/run-tower.py" scan "$@"
