#!/bin/bash
# Wait for discovery to finish, then run the daily pipeline once.
# Waits on the absence of the process rather than one PID, so a discovery
# restart is waited out too instead of racing it.
PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$PROJECT" || exit 1
LOG="$PROJECT/data/daily_run.log"

running() { pgrep -f "main\.py discover" | grep -qv "^$$\$"; }

echo "$(date '+%Y-%m-%d %H:%M:%S')  chained run: waiting for discovery" >> "$LOG"
while pgrep -f "main\.py discover" >/dev/null 2>&1; do sleep 60; done
echo "$(date '+%Y-%m-%d %H:%M:%S')  discovery ended — starting daily pipeline" >> "$LOG"
exec /bin/bash "$PROJECT/devops/daily_pipeline.sh" daily
