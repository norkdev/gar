"""Data plane: the runs table and the private state bucket (spec §9).

Realizes scale seam #3 (externalized agent state) for v2:
- a DynamoDB table for `RunState` (PK `run_id`; `tenant-index` GSI for
  list-by-tenant, newest-first), consumed by `DynamoDbRunStore`;
- a private S3 bucket for the offloaded candidate pool (plan §10 D-204) and,
  later, reports / audit JSONL. The shared public *search cache* is a
  separate bucket added in a later slice.

Removal policies are DESTROY for the v2.0 dev account (clean teardown); switch
the table to RETAIN before holding real data.
"""

from aws_cdk import (
    CfnOutput,
    RemovalPolicy,
    Stack,
)
from aws_cdk import (
    aws_dynamodb as dynamodb,
)
from aws_cdk import (
    aws_s3 as s3,
)
from constructs import Construct

RUNS_TABLE_NAME = "gar-runs"
TENANT_INDEX = "tenant-index"


class DataStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs: object) -> None:
        super().__init__(scope, construct_id, **kwargs)

        runs = dynamodb.Table(
            self,
            "RunsTable",
            table_name=RUNS_TABLE_NAME,
            partition_key=dynamodb.Attribute(
                name="run_id", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,  # dev; RETAIN before real data
        )
        runs.add_global_secondary_index(
            index_name=TENANT_INDEX,
            partition_key=dynamodb.Attribute(
                name="tenant_id", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="updated_at", type=dynamodb.AttributeType.STRING
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        state_bucket = s3.Bucket(
            self,
            "StateBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,  # dev teardown
            auto_delete_objects=True,
        )

        # Exposed for cross-stack wiring (the backend Lambda's env, a later slice):
        #   GAR_RUNS_TABLE   = RunsTableName
        #   GAR_STATE_BUCKET = StateBucketName
        self.runs_table = runs
        self.state_bucket = state_bucket
        CfnOutput(self, "RunsTableName", value=runs.table_name)
        CfnOutput(self, "StateBucketName", value=state_bucket.bucket_name)
