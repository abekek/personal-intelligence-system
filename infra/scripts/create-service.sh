#!/bin/bash
# Create (or recreate) the pis-api App Runner service from PisCore stack
# outputs. Kept out of CloudFormation while iterating on service-level
# failures; fold back into the stack via INCLUDE_SERVICE once stable.
set -euo pipefail

AWS=/usr/local/bin/aws
PROFILE=abekek
REGION=us-east-1

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
        "PIS_DB_SSLMODE": "require"
      },
      "RuntimeEnvironmentSecrets": {
        "PIS_DB_SECRET": "$DB_SECRET",
        "PIS_INGEST_TOKEN": "$INGEST_TOKEN",
        "PIS_GITHUB_WEBHOOK_SECRET": "$WEBHOOK_SECRET"
      }
    }
  }
}
EOF

$AWS apprunner create-service \
  --service-name pis-api \
  --source-configuration file:///tmp/pis-source-config.json \
  --instance-configuration "Cpu=0.25 vCPU,Memory=0.5 GB,InstanceRoleArn=$INSTANCE_ROLE" \
  --network-configuration "EgressConfiguration={EgressType=VPC,VpcConnectorArn=$CONNECTOR}" \
  --health-check-configuration "Protocol=HTTP,Path=/healthz,Interval=10,Timeout=5,HealthyThreshold=1,UnhealthyThreshold=5" \
  --profile $PROFILE --region $REGION \
  --query 'Service.[ServiceArn,ServiceUrl,Status]' --output text
