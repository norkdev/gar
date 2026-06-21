"""S3 + CloudFront hosting for the SPA, plus the Cognito browser app client
(v2.1, D-205).

The browser app client lives here (not AuthStack) because its OAuth callback is
the CloudFront URL, created in this stack — co-locating them resolves the
domain/callback chicken-and-egg. Deploy-time values (API URL, issuer, browser
client id, scope) are written to a runtime ``config.json`` in the bucket, so the
static build stays free of account-specific ids; the SPA fetches it on load.

The browser uses authorization-code + PKCE (a public client, no secret) and
requests the ``gar-api/access`` scope — so its access token is verified by the
backend exactly like an M2M token (one auth path, D-206).
"""

import os

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_deployment as s3deploy
from constructs import Construct

_DIST_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist")
API_SCOPE = "gar-api/access"


class FrontendStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        user_pool: cognito.IUserPool,
        issuer: str,
        api_url: str,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        bucket = s3.Bucket(
            self,
            "SpaBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,  # dev
            auto_delete_objects=True,
        )

        distribution = cloudfront.Distribution(
            self,
            "Distribution",
            default_root_object="index.html",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            ),
            # SPA client-side routing + the OAuth redirect land on index.html.
            error_responses=[
                cloudfront.ErrorResponse(
                    http_status=code,
                    response_http_status=200,
                    response_page_path="/index.html",
                )
                for code in (403, 404)
            ],
        )
        origin = f"https://{distribution.distribution_domain_name}"

        # Public SPA client: OAuth (auth-code + PKCE), no secret. Callback is this
        # distribution's URL. Granted the API scope so its token reaches the API.
        # Constructed here (not user_pool.add_client) so the resource lives in
        # this stack — add_client would attach it to the pool's (Auth) stack and
        # create a Backend→Auth→Frontend cycle via the callback domain.
        browser_client = cognito.UserPoolClient(
            self,
            "BrowserClient",
            user_pool=user_pool,
            generate_secret=False,
            auth_flows=cognito.AuthFlow(),  # OAuth only — no direct user-pool flows
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(authorization_code_grant=True),
                scopes=[
                    cognito.OAuthScope.OPENID,
                    cognito.OAuthScope.custom(API_SCOPE),
                ],
                callback_urls=[origin, f"{origin}/"],
                logout_urls=[origin, f"{origin}/"],
            ),
            supported_identity_providers=[
                cognito.UserPoolClientIdentityProvider.COGNITO
            ],
            access_token_validity=Duration.hours(1),
            id_token_validity=Duration.hours(1),
        )

        # Deploy the built SPA + a runtime config.json carrying the deploy-time
        # values (CDK resolves the tokens into the JSON). The SPA reads it on load.
        s3deploy.BucketDeployment(
            self,
            "DeploySpa",
            destination_bucket=bucket,
            distribution=distribution,
            distribution_paths=["/*"],
            sources=[
                s3deploy.Source.asset(_DIST_DIR),
                s3deploy.Source.json_data(
                    "config.json",
                    {
                        "apiUrl": api_url,
                        "cognito": {
                            "authority": issuer,
                            "clientId": browser_client.user_pool_client_id,
                            "scope": f"openid {API_SCOPE}",
                        },
                    },
                ),
            ],
        )

        CfnOutput(self, "DistributionUrl", value=origin)
        CfnOutput(self, "BrowserClientId", value=browser_client.user_pool_client_id)
