"""Step Functions state machine orchestrating the agent loop with HITL gates.

v1 skeleton — state machine definition pending. Uses wait-for-callback for HITL.
"""

from aws_cdk import Stack
from constructs import Construct


class WorkflowStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs: object) -> None:
        super().__init__(scope, construct_id, **kwargs)
