"""DynamoDB tables and S3 buckets (audit log / private content / search cache).

v1 skeleton — resources to be added per spec §9.
"""

from aws_cdk import Stack
from constructs import Construct


class DataStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs: object) -> None:
        super().__init__(scope, construct_id, **kwargs)
