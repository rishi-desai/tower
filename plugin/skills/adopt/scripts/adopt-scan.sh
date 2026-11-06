#!/usr/bin/env bash
# adopt-scan.sh runs `tower adopt-scan` (src/tower/adopt.py), which replaced this script. It keeps the skills
# working for one release; T19 deletes it.
exec python3 -I "$(dirname "$0")/../../../scripts/run-tower.py" adopt-scan "$@"
