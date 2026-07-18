#!/bin/bash
# Update the pis-api App Runner service to the current stack's image/config
# and wait for RUNNING. Run `cdk deploy` first to push a new image.
set -euo pipefail

AWS=/usr/local/bin/aws
PROFILE=abekek
REGION=us-east-1
DIR="$(cd "$(dirname "$0")" && pwd)"

ARN=$($AWS apprunner list-services --profile $PROFILE --region $REGION \
  --query 'ServiceSummaryList[?ServiceName==`pis-api`] | [0].ServiceArn' --output text)
URL="https://$($AWS apprunner list-services --profile $PROFILE --region $REGION \
  --query 'ServiceSummaryList[?ServiceName==`pis-api`] | [0].ServiceUrl' --output text)"

source "$DIR/service-config.sh" "$URL"

$AWS apprunner update-service --service-arn "$ARN" \
  --source-configuration file:///tmp/pis-source-config.json \
  --profile $PROFILE --region $REGION \
  --query 'Service.Status' --output text

until s=$($AWS apprunner describe-service --service-arn "$ARN" --profile $PROFILE \
  --region $REGION --query 'Service.Status' --output text) \
  && [ "$s" != "OPERATION_IN_PROGRESS" ]; do sleep 15; done
echo "service status: $s (url: $URL)"
[ "$s" = "RUNNING" ]
