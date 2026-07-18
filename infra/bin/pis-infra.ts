#!/usr/bin/env node
import * as cdk from "aws-cdk-lib";
import { PisCoreStack } from "../lib/pis-core-stack";

const app = new cdk.App();
new PisCoreStack(app, "PisCore", {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT || process.env.PIS_AWS_ACCOUNT,
    region: "us-east-1",
  },
  description: "Personal Intelligence System core: RDS Postgres, S3 object store, App Runner API",
});
