#!/bin/bash
# Loop the extraction endpoint until every conversation is distilled.
# Safe to interrupt and resume: the cursor lives on each conversation row.
set -euo pipefail
source ~/.pis/daemon.env
totals=0
while true; do
  out=$(curl -s -m 115 -X POST -H "Authorization: Bearer $PIS_INGEST_TOKEN" \
    "$PIS_API_URL/v1/admin/extract?limit=5")
  remaining=$(echo "$out" | python3 -c "import json,sys; print(json.load(sys.stdin).get('remaining', -1))" 2>/dev/null || echo -1)
  created=$(echo "$out" | python3 -c "import json,sys; print(json.load(sys.stdin).get('created', 0))" 2>/dev/null || echo 0)
  totals=$((totals + created))
  echo "batch: created=$created total=$totals remaining=$remaining"
  if [ "$remaining" = "0" ]; then break; fi
  if [ "$remaining" = "-1" ]; then echo "transient error, retrying"; sleep 5; fi
done
echo "extraction complete: $totals memories created"
