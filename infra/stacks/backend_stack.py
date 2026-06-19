"""The FastAPI backend on Lambda, behind a Function URL (spec §9, seam #2).

The same `gar_backend.main:app` runs locally (uvicorn) and here (Mangum →
`gar_backend.main.handler`). The function is bundled with Docker (pip install
into a Lambda-compatible arm64 image) so compiled deps (pydantic-core) get
Linux wheels; boto3/botocore are dropped because the runtime provides them.

`GAR_RUNS_TABLE` / `GAR_STATE_BUCKET` point the DynamoDbRunStore at the
DataStack's resources; the function is granted read/write on both. The audit
log is written durably to the state bucket (`GAR_AUDIT_BUCKET`). The Anthropic
API key is not baked into the image or an env var — the function reads it at
cold start from a Secrets Manager secret (`GAR_ANTHROPIC_SECRET_ARN`); the
real value is set out-of-band (see README), never committed. The Function URL
is IAM-authed for now (no public endpoint) — the API-key scheme for
browser/MCP clients lands in a later slice.
"""

import os

from aws_cdk import (
    ArnFormat,
    BundlingOptions,
    CfnOutput,
    Duration,
    Stack,
)
from aws_cdk import (
    aws_dynamodb as dynamodb,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_lambda as lambda_,
)
from aws_cdk import (
    aws_s3 as s3,
)
from aws_cdk import (
    aws_secretsmanager as secretsmanager,
)
from constructs import Construct

_BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "backend")


class BackendStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        runs_table: dynamodb.ITable,
        state_bucket: s3.IBucket,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Holds the Anthropic API key. CDK creates it with a random placeholder
        # (no real credential in source or the template); the operator sets the
        # real value with `aws secretsmanager put-secret-value` after deploy.
        api_key_secret = secretsmanager.Secret(
            self,
            "AnthropicApiKey",
            description="Anthropic API key for the GAR backend (set post-deploy)",
        )

        fn = lambda_.Function(
            self,
            "ApiFunction",
            runtime=lambda_.Runtime.PYTHON_3_13,
            architecture=lambda_.Architecture.ARM_64,
            handler="gar_backend.main.handler",
            memory_size=1024,
            timeout=Duration.seconds(30),
            environment={
                "GAR_RUNS_TABLE": runs_table.table_name,
                "GAR_STATE_BUCKET": state_bucket.bucket_name,
                "GAR_AUDIT_BUCKET": state_bucket.bucket_name,  # durable audit log
                "GAR_AUDIT_LOG_PATH": "/tmp/audit.jsonl",  # file-sink fallback
                "GAR_ANTHROPIC_SECRET_ARN": api_key_secret.secret_arn,
            },
            code=lambda_.Code.from_asset(
                _BACKEND_DIR,
                bundling=BundlingOptions(
                    image=lambda_.Runtime.PYTHON_3_13.bundling_image,
                    platform="linux/arm64",
                    command=[
                        "bash",
                        "-c",
                        # Install the package + deps; drop boto3/botocore (the
                        # Lambda runtime provides them) to keep the zip small.
                        "pip install /asset-input -t /asset-output --no-cache-dir "
                        "&& rm -rf /asset-output/boto3 /asset-output/botocore "
                        "/asset-output/boto3-*.dist-info "
                        "/asset-output/botocore-*.dist-info",
                    ],
                ),
            ),
        )

        runs_table.grant_read_write_data(fn)  # table + its GSIs
        state_bucket.grant_read_write(fn)  # run-state pool + audit log
        api_key_secret.grant_read(fn)

        # Allow the function to invoke itself asynchronously to run a segment
        # off the request thread (api/segments.LambdaRunner). Scoped to this
        # stack's functions by name pattern rather than fn.function_arn — the
        # latter would make the role depend on the function it's attached to,
        # a CloudFormation circular dependency.
        fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["lambda:InvokeFunction"],
                resources=[
                    self.format_arn(
                        service="lambda",
                        resource="function",
                        resource_name=f"{self.stack_name}-*",
                        arn_format=ArnFormat.COLON_RESOURCE_NAME,
                    )
                ],
            )
        )

        url = fn.add_function_url(auth_type=lambda_.FunctionUrlAuthType.AWS_IAM)
        self.api_function = fn
        CfnOutput(self, "ApiFunctionUrl", value=url.url)
        CfnOutput(self, "AnthropicSecretArn", value=api_key_secret.secret_arn)
