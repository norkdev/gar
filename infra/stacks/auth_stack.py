"""Cognito User Pool. v1 creates the pool but the API uses pass-through auth.

Defined now so multi-tenant auth can be wired in later without retrofitting.
"""

from aws_cdk import Stack
from constructs import Construct


class AuthStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs: object) -> None:
        super().__init__(scope, construct_id, **kwargs)
