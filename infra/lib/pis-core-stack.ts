import * as cdk from "aws-cdk-lib";
import * as apprunner from "aws-cdk-lib/aws-apprunner";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as ecrAssets from "aws-cdk-lib/aws-ecr-assets";
import * as iam from "aws-cdk-lib/aws-iam";
import * as rds from "aws-cdk-lib/aws-rds";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as secretsmanager from "aws-cdk-lib/aws-secretsmanager";
import { Construct } from "constructs";

export class PisCoreStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const vpc = ec2.Vpc.fromLookup(this, "DefaultVpc", { isDefault: true });

    // Instances egress through the VPC (no NAT/internet); S3 needs a
    // gateway endpoint. Free.
    new ec2.GatewayVpcEndpoint(this, "S3Endpoint", {
      vpc,
      service: ec2.GatewayVpcEndpointAwsService.S3,
    });

    const connectorSg = new ec2.SecurityGroup(this, "AppRunnerConnectorSg", {
      vpc,
      description: "App Runner VPC connector egress",
      allowAllOutbound: true,
    });
    const rdsSg = new ec2.SecurityGroup(this, "RdsSg", {
      vpc,
      description: "PIS RDS access",
      allowAllOutbound: false,
    });
    rdsSg.addIngressRule(connectorSg, ec2.Port.tcp(5432), "App Runner via VPC connector");

    const db = new rds.DatabaseInstance(this, "Db", {
      engine: rds.DatabaseInstanceEngine.postgres({
        version: rds.PostgresEngineVersion.VER_16,
      }),
      instanceType: ec2.InstanceType.of(ec2.InstanceClass.T4G, ec2.InstanceSize.MICRO),
      vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PUBLIC },
      securityGroups: [rdsSg],
      credentials: rds.Credentials.fromGeneratedSecret("pis"),
      databaseName: "pis",
      allocatedStorage: 20,
      storageType: rds.StorageType.GP3,
      storageEncrypted: true,
      publiclyAccessible: false,
      multiAz: false,
      backupRetention: cdk.Duration.days(7),
      // Deletion protection stays off until the ledger holds real data
      // (it wedges CloudFormation rollbacks during stack bring-up);
      // RemovalPolicy.SNAPSHOT still snapshots on any delete.
      deletionProtection: false,
      removalPolicy: cdk.RemovalPolicy.SNAPSHOT,
    });

    const bucket = new s3.Bucket(this, "ObjectStore", {
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
    });

    const ingestToken = new secretsmanager.Secret(this, "IngestToken", {
      secretName: "pis/ingest-token",
      generateSecretString: { excludePunctuation: true, passwordLength: 48 },
    });
    const webhookSecret = new secretsmanager.Secret(this, "WebhookSecret", {
      secretName: "pis/webhook-secret",
      generateSecretString: { excludePunctuation: true, passwordLength: 48 },
    });
    const oauthPasscode = new secretsmanager.Secret(this, "OauthPasscode", {
      secretName: "pis/oauth-passcode",
      generateSecretString: { excludePunctuation: true, passwordLength: 24 },
    });

    const image = new ecrAssets.DockerImageAsset(this, "ApiImage", {
      directory: "..",
      platform: ecrAssets.Platform.LINUX_AMD64,
    });

    const accessRole = new iam.Role(this, "AppRunnerAccessRole", {
      assumedBy: new iam.ServicePrincipal("build.apprunner.amazonaws.com"),
    });
    image.repository.grantPull(accessRole);

    const instanceRole = new iam.Role(this, "AppRunnerInstanceRole", {
      assumedBy: new iam.ServicePrincipal("tasks.apprunner.amazonaws.com"),
    });
    db.secret!.grantRead(instanceRole);
    ingestToken.grantRead(instanceRole);
    webhookSecret.grantRead(instanceRole);
    oauthPasscode.grantRead(instanceRole);
    bucket.grantReadWrite(instanceRole);
    instanceRole.addToPolicy(new iam.PolicyStatement({
      actions: ["bedrock:InvokeModel"],
      resources: [
        `arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-embed-text-v2:0`,
        `arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-*`,
        `arn:aws:bedrock:us-east-1:${this.account}:inference-profile/*`,
      ],
    }));

    // App Runner does not support use1-az3 (us-east-1e in this account)
    const connectorSubnets = vpc.publicSubnets.filter(
      (s) => s.availabilityZone !== "us-east-1e",
    );

    // Instances have no internet; Bedrock (embeddings + Phase 4 extraction)
    // needs an interface endpoint. Two AZs for availability.
    const endpointSg = new ec2.SecurityGroup(this, "BedrockEndpointSg", {
      vpc,
      description: "Bedrock runtime endpoint",
      allowAllOutbound: true,
    });
    endpointSg.addIngressRule(connectorSg, ec2.Port.tcp(443), "App Runner to Bedrock");
    new ec2.InterfaceVpcEndpoint(this, "BedrockRuntimeEndpoint", {
      vpc,
      service: new ec2.InterfaceVpcEndpointService(
        "com.amazonaws.us-east-1.bedrock-runtime", 443),
      subnets: {
        subnets: connectorSubnets.filter((s) =>
          ["us-east-1a", "us-east-1b"].includes(s.availabilityZone)),
      },
      securityGroups: [endpointSg],
      privateDnsEnabled: true,
    });
    const connector = new apprunner.CfnVpcConnector(this, "VpcConnector", {
      subnets: connectorSubnets.map((s) => s.subnetId),
      securityGroups: [connectorSg.securityGroupId],
    });

    // The App Runner service is created via CLI (scripts/create-service.sh)
    // while iterating on service-level failures; CFN's all-or-nothing
    // rollback otherwise recreates RDS on every attempt. Set to true and
    // redeploy to fold the service back into the stack.
    const INCLUDE_SERVICE = false;

    if (INCLUDE_SERVICE) {
    const service = new apprunner.CfnService(this, "Api", {
      sourceConfiguration: {
        authenticationConfiguration: { accessRoleArn: accessRole.roleArn },
        autoDeploymentsEnabled: false,
        imageRepository: {
          imageIdentifier: image.imageUri,
          imageRepositoryType: "ECR",
          imageConfiguration: {
            port: "8800",
            runtimeEnvironmentVariables: [
              { name: "PIS_OBJECT_STORE_BACKEND", value: "s3" },
              { name: "PIS_S3_BUCKET", value: bucket.bucketName },
              { name: "PIS_DB_SSLMODE", value: "require" },
            ],
            runtimeEnvironmentSecrets: [
              { name: "PIS_DB_SECRET", value: db.secret!.secretArn },
              { name: "PIS_INGEST_TOKEN", value: ingestToken.secretArn },
              { name: "PIS_GITHUB_WEBHOOK_SECRET", value: webhookSecret.secretArn },
            ],
          },
        },
      },
      instanceConfiguration: {
        cpu: "0.25 vCPU",
        memory: "0.5 GB",
        instanceRoleArn: instanceRole.roleArn,
      },
      networkConfiguration: {
        egressConfiguration: {
          egressType: "VPC",
          vpcConnectorArn: connector.attrVpcConnectorArn,
        },
      },
      healthCheckConfiguration: {
        protocol: "HTTP",
        path: "/healthz",
        healthyThreshold: 1,
        unhealthyThreshold: 5,
        interval: 10,
        timeout: 5,
      },
    });

    new cdk.CfnOutput(this, "ServiceUrl", { value: `https://${service.attrServiceUrl}` });
    }

    new cdk.CfnOutput(this, "BucketName", { value: bucket.bucketName });
    new cdk.CfnOutput(this, "DbEndpoint", { value: db.dbInstanceEndpointAddress });
    new cdk.CfnOutput(this, "DbSecretArn", { value: db.secret!.secretArn });
    new cdk.CfnOutput(this, "IngestTokenArn", { value: ingestToken.secretArn });
    new cdk.CfnOutput(this, "WebhookSecretArn", { value: webhookSecret.secretArn });
    new cdk.CfnOutput(this, "OauthPasscodeArn", { value: oauthPasscode.secretArn });
    new cdk.CfnOutput(this, "AccessRoleArn", { value: accessRole.roleArn });
    new cdk.CfnOutput(this, "InstanceRoleArn", { value: instanceRole.roleArn });
    new cdk.CfnOutput(this, "VpcConnectorArn", { value: connector.attrVpcConnectorArn });
    new cdk.CfnOutput(this, "ImageUri", { value: image.imageUri });
  }
}
