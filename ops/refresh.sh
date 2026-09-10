#!/usr/bin/env bash
# Scheduled data refresh for the pilot VM. Invoked by cron (see ops/crontab).
#
#   ops/refresh.sh daily|weekly|quarterly
#
# Runs inside the repo's virtualenv, logs to ops/logs/, and — critically — is
# safe to run concurrently with itself: a slow quarterly CAGIS pull must not
# have a daily run start scoring underneath it.
set -euo pipefail

TIER="${1:?usage: refresh.sh daily|weekly|quarterly}"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$APP_DIR/ops/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/refresh-$TIER-$(date +%Y%m%d-%H%M%S).log"

cd "$APP_DIR"
[ -f .venv/bin/activate ] && source .venv/bin/activate
[ -f .env ] && set -a && source .env && set +a

# one refresh at a time, whatever the tier
exec 9>"$LOG_DIR/.refresh.lock"
if ! flock -n 9; then
    echo "another refresh is already running; skipping $TIER" | tee -a "$LOG"
    exit 0
fi

{
    echo "=== refresh $TIER started $(date -Is) ==="
    python -m orchestration.pipelines --tier "$TIER"
    echo "=== refresh $TIER finished $(date -Is) ==="
    python -m orchestration.freshness || echo "WARNING: some feeds are stale"
} >>"$LOG" 2>&1

# keep a month of logs, drop the rest
find "$LOG_DIR" -name 'refresh-*.log' -mtime +30 -delete
ln -sfn "$LOG" "$LOG_DIR/refresh-$TIER-latest.log"
