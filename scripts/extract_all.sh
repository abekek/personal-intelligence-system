#!/bin/bash
# Loop the extraction endpoint until every conversation is distilled.
# Resilient: batch timeouts/502s are retried, never fatal. Resumable: the
# cursor lives on each conversation row.
set -uo pipefail
source ~/.pis/daemon.env
totals=0
failures=0
while true; do
  out=$(curl -s -m 115 -X POST -H "Authorization: Bearer $PIS_INGEST_TOKEN" \
    "$PIS_API_URL/v1/admin/extract?limit=2" || echo "")
  remaining=$(echo "$out" | python3 -c "import json,sys; print(json.load(sys.stdin).get('remaining', -1))" 2>/dev/null || echo -1)
  created=$(echo "$out" | python3 -c "import json,sys; print(json.load(sys.stdin).get('created', 0))" 2>/dev/null || echo 0)
  if [ "$remaining" = "-1" ]; then
    failures=$((failures + 1))
    echo "batch failed (attempt $failures), retrying in 10s"
    if [ "$failures" -ge 30 ]; then echo "too many failures, aborting"; exit 1; fi
    sleep 10
    continue
  fi
  failures=0
  totals=$((totals + created))
  echo "batch: created=$created total=$totals remaining=$remaining"
  [ "$remaining" = "0" ] && break
done
echo "extraction complete: $totals memories created this run"
