"""CDK application entry point.

Synthesizes five stacks; each is independently deployable.
v1 skeleton — all stacks are empty shells. Resources will be added per spec §9.
"""

import aws_cdk as cdk
from stacks.auth_stack import AuthStack
from stacks.backend_stack import BackendStack
from stacks.data_stack import DataStack
from stacks.frontend_stack import FrontendStack
from stacks.workflow_stack import WorkflowStack

app = cdk.App()

DataStack(app, "GarDataStack")
WorkflowStack(app, "GarWorkflowStack")
BackendStack(app, "GarBackendStack")
FrontendStack(app, "GarFrontendStack")
AuthStack(app, "GarAuthStack")

app.synth()
