#!/bin/bash
# Install the PIS capture daemon as a launchd agent pointing at the AWS core.
# Fetches the cloud API URL from CloudFormation and the ingest token from
# Secrets Manager (profile abekek, us-east-1), generates a local daemon token,
# and writes ~/.pis/daemon.env (chmod 600) shared by daemon and Stop hook.
set -euo pipefail

AWS=/usr/local/bin/aws
PROFILE=abekek
REGION=us-east-1
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PIS_DIR="$HOME/.pis"
PLIST="$HOME/Library/LaunchAgents/com.pis.daemon.plist"

# The App Runner service is created outside CloudFormation
# (infra/scripts/create-service.sh), so resolve its URL from App Runner.
SERVICE_URL="https://$($AWS apprunner list-services \
  --query 'ServiceSummaryList[?ServiceName==`pis-api`] | [0].ServiceUrl' \
  --output text --profile $PROFILE --region $REGION)"
if [ "$SERVICE_URL" = "https://None" ] || [ "$SERVICE_URL" = "https://" ]; then
  echo "could not resolve pis-api service URL" >&2
  exit 1
fi
INGEST_TOKEN=$($AWS secretsmanager get-secret-value --secret-id pis/ingest-token \
  --query SecretString --output text --profile $PROFILE --region $REGION)

mkdir -p "$PIS_DIR"
if [ -f "$PIS_DIR/daemon.env" ] && grep -q PIS_DAEMON_TOKEN "$PIS_DIR/daemon.env"; then
  DAEMON_TOKEN=$(grep PIS_DAEMON_TOKEN "$PIS_DIR/daemon.env" | cut -d= -f2)
else
  DAEMON_TOKEN=$(openssl rand -hex 24)
fi

cat > "$PIS_DIR/daemon.env" <<EOF
PIS_API_URL=$SERVICE_URL
PIS_INGEST_TOKEN=$INGEST_TOKEN
PIS_DAEMON_TOKEN=$DAEMON_TOKEN
PIS_DAEMON_OUTBOX_PATH=$PIS_DIR/outbox.sqlite3
EOF
chmod 600 "$PIS_DIR/daemon.env"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.pis.daemon</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-c</string>
    <string>set -a; source $PIS_DIR/daemon.env; exec $REPO/.venv/bin/python -m pis.daemon</string>
  </array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$PIS_DIR/daemon.log</string>
  <key>StandardErrorPath</key><string>$PIS_DIR/daemon.log</string>
</dict>
</plist>
EOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "daemon installed: API=$SERVICE_URL outbox=$PIS_DIR/outbox.sqlite3"
echo "health: curl -s http://127.0.0.1:8787/v1/health"
