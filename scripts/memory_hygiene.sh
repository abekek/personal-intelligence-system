#!/bin/bash
# Drive all memory-hygiene stages to completion against the live service.
set -uo pipefail
source ~/.pis/daemon.env
call() { curl -s -m 115 -X POST -H "Authorization: Bearer $PIS_INGEST_TOKEN" "$PIS_API_URL/v1/admin/memory-hygiene?$1"; }

echo "== evidence dedup:"; call "stage=evidence"; echo
echo "== retract note-only:"; call "stage=retract-notes"; echo

for stage in merge supersede; do
  echo "== $stage:"
  after=""
  while true; do
    out=$(call "stage=$stage&after=$after&limit=150" || echo "")
    next=$(echo "$out" | python3 -c "import json,sys; print(json.load(sys.stdin).get('next_after',''))" 2>/dev/null || echo "RETRY")
    if [ "$next" = "RETRY" ]; then echo "  batch failed, retrying"; sleep 8; continue; fi
    echo "  $out"
    after="$next"
    [ -z "$after" ] && break
  done
done
echo "hygiene complete"
