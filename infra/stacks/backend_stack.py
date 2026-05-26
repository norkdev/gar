"""Lambda + Function URLs + IAM for the FastAPI backend (via Mangum).

v1 skeleton — Lambda function and routing to be added.
"""

from aws_cdk import Stack
from constructs import Construct


class BackendStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs: object) -> None:
        super().__init__(scope, construct_id, **kwargs)
