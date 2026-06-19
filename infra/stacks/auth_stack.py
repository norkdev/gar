"""Cognito identity for GAR (v2.1, D-203 / D-206).

A User Pool plus the pieces for OAuth2 client-credentials (M2M): a resource
server with a custom ``access`` scope, an app client with a secret that can
request that scope, and a domain (the OAuth token endpoint lives on it). The
backend verifies the resulting JWTs (``api/auth.CognitoVerifier``); machine
clients (MCP / CLI) exchange client_id/secret for a token at the endpoint.

The browser app client (auth-code + Hosted UI) is added in the browser-hosting
slice; this stack ships the machine path first.
"""

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_cognito as cognito
from constructs import Construct

# The resource server identifier + scope name compose the scope string the
# backend checks: "gar-api/access".
RESOURCE_SERVER_ID = "gar-api"
ACCESS_SCOPE = "access"
API_SCOPE = f"{RESOURCE_SERVER_ID}/{ACCESS_SCOPE}"


class AuthStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs: object) -> None:
        super().__init__(scope, construct_id, **kwargs)

        pool = cognito.UserPool(
            self,
            "UserPool",
            self_sign_up_enabled=False,  # admin/invite-created users for now
            sign_in_aliases=cognito.SignInAliases(email=True),
            removal_policy=RemovalPolicy.DESTROY,  # dev; RETAIN before real users
        )

        access_scope = cognito.ResourceServerScope(
            scope_name=ACCESS_SCOPE, scope_description="Call the GAR API"
        )
        resource_server = pool.add_resource_server(
            "ResourceServer",
            identifier=RESOURCE_SERVER_ID,
            scopes=[access_scope],
        )

        # OAuth flows need a domain; the token endpoint lives on it. The prefix
        # must be globally unique — scope it with the account id.
        domain = pool.add_domain(
            "Domain",
            cognito_domain=cognito.CognitoDomainOptions(
                domain_prefix=f"gar-{self.account}"
            ),
        )

        # Machine-to-machine client: a secret + client-credentials grant only.
        m2m_client = pool.add_client(
            "M2mClient",
            generate_secret=True,
            auth_flows=cognito.AuthFlow(),  # no user (password/SRP) flows
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(client_credentials=True),
                scopes=[
                    cognito.OAuthScope.resource_server(resource_server, access_scope)
                ],
            ),
            access_token_validity=Duration.hours(1),
        )

        # Exposed for cross-stack wiring into BackendStack (env config).
        self.user_pool = pool
        self.issuer = (
            f"https://cognito-idp.{self.region}.amazonaws.com/{pool.user_pool_id}"
        )
        self.m2m_client = m2m_client
        self.api_scope = API_SCOPE

        CfnOutput(self, "UserPoolId", value=pool.user_pool_id)
        CfnOutput(self, "Issuer", value=self.issuer)
        CfnOutput(self, "M2mClientId", value=m2m_client.user_pool_client_id)
        CfnOutput(self, "TokenEndpoint", value=f"{domain.base_url()}/oauth2/token")
        CfnOutput(self, "ApiScope", value=API_SCOPE)
