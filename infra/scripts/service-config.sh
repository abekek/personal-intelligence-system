#!/bin/bash
# Shared: writes /tmp/pis-source-config.json from PisCore stack outputs.
# Usage: source service-config.sh <PUBLIC_URL>
set -euo pipefail

AWS=/usr/local/bin/aws
PROFILE=abekek
REGION=us-east-1
PUBLIC_URL="${1:?public url required}"

out() {
  $AWS cloudformation describe-stacks --stack-name PisCore \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" \
    --output text --profile $PROFILE --region $REGION
}

IMAGE_URI=$(out ImageUri)
ACCESS_ROLE=$(out AccessRoleArn)
INSTANCE_ROLE=$(out InstanceRoleArn)
CONNECTOR=$(out VpcConnectorArn)
BUCKET=$(out BucketName)
DB_SECRET=$(out DbSecretArn)
INGEST_TOKEN=$(out IngestTokenArn)
WEBHOOK_SECRET=$(out WebhookSecretArn)
OAUTH_PASSCODE=$(out OauthPasscodeArn)

cat > /tmp/pis-source-config.json <<EOF
{
  "AuthenticationConfiguration": {"AccessRoleArn": "$ACCESS_ROLE"},
  "AutoDeploymentsEnabled": false,
  "ImageRepository": {
    "ImageIdentifier": "$IMAGE_URI",
    "ImageRepositoryType": "ECR",
    "ImageConfiguration": {
      "Port": "8800",
      "RuntimeEnvironmentVariables": {
        "PIS_OBJECT_STORE_BACKEND": "s3",
        "PIS_S3_BUCKET": "$BUCKET",
        "PIS_DB_SSLMODE": "require",
        "PIS_PUBLIC_URL": "$PUBLIC_URL",
        "PIS_EMBEDDINGS_ENABLED": "true",
        "PIS_EXTRACTION_MODEL": "us.anthropic.claude-haiku-4-5-20251001-v1:0"
      },
      "RuntimeEnvironmentSecrets": {
        "PIS_DB_SECRET": "$DB_SECRET",
        "PIS_INGEST_TOKEN": "$INGEST_TOKEN",
        "PIS_GITHUB_WEBHOOK_SECRET": "$WEBHOOK_SECRET",
        "PIS_OAUTH_PASSCODE": "$OAUTH_PASSCODE"
      }
    }
  }
}
EOF
