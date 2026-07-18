#!/bin/bash
# Loop the admin backfill endpoint until every row is embedded.
set -euo pipefail
source ~/.pis/daemon.env
total=0
while true; do
  out=$(curl -s -m 120 -X POST -H "Authorization: Bearer $PIS_INGEST_TOKEN" \
    "$PIS_API_URL/v1/admin/embed-backfill?limit=100")
  scanned=$(echo "$out" | python3 -c "import json,sys; print(json.load(sys.stdin)['scanned'])")
  embedded=$(echo "$out" | python3 -c "import json,sys; print(json.load(sys.stdin)['embedded'])")
  total=$((total + embedded))
  echo "batch: scanned=$scanned embedded=$embedded total=$total"
  [ "$scanned" = "0" ] && break
done
echo "backfill complete: $total embedded"
