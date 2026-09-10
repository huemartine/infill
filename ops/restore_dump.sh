#!/usr/bin/env bash
# Restore a pg_dump of the infill database into the local docker stack.
#
#   ./ops/restore_dump.sh path/to/infill_YYYYMMDD.dump
#
# Why it drops and recreates the database first: the compose file mounts
# db/ddl into /docker-entrypoint-initdb.d, so the container's first boot
# already created `infill` with the full schema. Restoring a dump on top of a
# populated schema produces hundreds of "already exists" errors and a half-
# merged database. Starting from an empty database makes the restore
# deterministic - the dump carries the schema, the PostGIS extension and the
# data together.
set -euo pipefail

DUMP="${1:-}"
COMPOSE="${COMPOSE:-docker compose -f infra/docker-compose.yml}"
JOBS="${JOBS:-4}"

if [ -z "$DUMP" ] || [ ! -f "$DUMP" ]; then
  echo "usage: $0 <dump-file>" >&2
  exit 2
fi

echo "==> waiting for the database container"
$COMPOSE up -d db
until $COMPOSE exec -T db pg_isready -U infill >/dev/null 2>&1; do sleep 2; done

echo "==> recreating an empty 'infill' database"
$COMPOSE exec -T db psql -U infill -d postgres -q \
  -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='infill' AND pid<>pg_backend_pid();" \
  -c "DROP DATABASE IF EXISTS infill;" \
  -c "CREATE DATABASE infill OWNER infill;"

echo "==> copying the dump into the container"
CID="$($COMPOSE ps -q db)"
docker cp "$DUMP" "$CID:/tmp/infill.dump"

# -j needs a real file (parallel restore cannot read a stream), which is why the
# dump is copied in rather than piped. --no-owner/--no-privileges keep the
# restore working regardless of what roles exist on the target.
echo "==> restoring (this takes a few minutes)"
$COMPOSE exec -T db pg_restore -U infill -d infill \
  --no-owner --no-privileges -j "$JOBS" /tmp/infill.dump

$COMPOSE exec -T db rm -f /tmp/infill.dump

echo "==> verifying"
$COMPOSE exec -T db psql -U infill -d infill -t -A -c \
  "SELECT 'parcels: '||count(*) FROM parcel_master;"
$COMPOSE exec -T db psql -U infill -d infill -t -A -c \
  "SELECT 'scored: '||count(*) FROM parcel_scores;"
$COMPOSE exec -T db psql -U infill -d infill -t -A -c \
  "SELECT 'postgis: '||extversion FROM pg_extension WHERE extname='postgis';"

echo "==> done. Start the API and web client:"
echo "    uvicorn api.main:app --port 8000"
echo "    cd web && npm install && npm run dev"
