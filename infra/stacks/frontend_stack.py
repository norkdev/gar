"""S3 + CloudFront for static frontend hosting.

v1 skeleton — bucket and distribution to be added.
"""

from aws_cdk import Stack
from constructs import Construct


class FrontendStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs: object) -> None:
        super().__init__(scope, construct_id, **kwargs)
