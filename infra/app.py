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
# Optional custom domain for the SPA. Supply at deploy time:
#   cdk deploy GarFrontendStack -c frontendDomain=gar.norkbb.net \
#     -c frontendCertArn=arn:aws:acm:us-east-1:<acct>:certificate/<id>
# The cert must be in us-east-1 and DNS-validated. Omit both to serve only on
# the *.cloudfront.net name. See docs/deploy.md "Custom domain".
FrontendStack(
    app,
    "GarFrontendStack",
    user_pool=auth.user_pool,
    issuer=auth.issuer,
    hosted_ui_domain=auth.hosted_ui_domain,
    api_url=backend.api_url,
    domain_name=app.node.try_get_context("frontendDomain"),
    certificate_arn=app.node.try_get_context("frontendCertArn"),
)

app.synth()
