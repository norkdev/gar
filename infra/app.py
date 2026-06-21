"""CDK application entry point.

Synthesizes the stacks; each is independently deployable. Data + Auth + Backend
carry real resources (v2.x); Workflow + Frontend are scaffolds.
"""

import aws_cdk as cdk
from stacks.auth_stack import AuthStack
from stacks.backend_stack import BackendStack
from stacks.data_stack import DataStack
from stacks.frontend_stack import FrontendStack
from stacks.workflow_stack import WorkflowStack

app = cdk.App()

data = DataStack(app, "GarDataStack")
auth = AuthStack(app, "GarAuthStack")
WorkflowStack(app, "GarWorkflowStack")
backend = BackendStack(
    app,
    "GarBackendStack",
    runs_table=data.runs_table,
    state_bucket=data.state_bucket,
    cognito_issuer=auth.issuer,
    cognito_scope=auth.api_scope,
)
FrontendStack(
    app,
    "GarFrontendStack",
    user_pool=auth.user_pool,
    issuer=auth.issuer,
    api_url=backend.api_url,
)

app.synth()
