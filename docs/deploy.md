# Deploy / re-deploy / destroy runbook

The backend is plain CDK, so the cloud is reproducible from git: deploy is
`cdk deploy`, teardown is `cdk destroy`. This page is the bring-it-back guide —
the few things that **aren't** in code (the Anthropic key value, the freshly
minted Function URL, the Cognito client secret) and how to re-supply them.

Account/region come from the `deploy` profile (see CLAUDE.md). All commands
assume that profile resolves to `ap-northeast-1`.

## What's deployed

| Stack | Resources |
|---|---|
| `GarDataStack` | DynamoDB `gar-runs` (PK `run_id`, `tenant-index` GSI) · S3 state bucket (run-state pool + audit JSONL) — both `RemovalPolicy.DESTROY` |
| `GarAuthStack` | Cognito User Pool · resource server (`gar-api`) + `access` scope · domain (OAuth token endpoint) · M2M app client (secret, client-credentials) |
| `GarBackendStack` | Lambda (arm64, Mangum) · Function URL (`NONE` + CORS) · self-invoke IAM · Anthropic-key secret · **Cognito JWT gate** (verifies tokens from `GarAuthStack`) |
| `GarFrontendStack` | S3 (private) + CloudFront (OAC) serving the SPA · browser Cognito app client (auth-code + PKCE) · runtime `config.json` |

`GarWorkflowStack` is still a scaffold. The `CDKToolkit` bootstrap stack is
independent and is **not** removed by destroying the app stacks — no
re-bootstrap needed on re-deploy.

## Prerequisites

- `uv sync --all-packages` done; CDK CLI installed (`npm i -g aws-cdk`).
- **Docker running** — the Lambda asset is Docker-bundled (arm64).
- **Frontend built** — `GarFrontendStack` deploys `frontend/dist`:
  ```bash
  (cd frontend && npm install && npm run build)
  ```
- Fresh `deploy` creds in the env (the profile's session is short-lived):
  ```bash
  eval "$(aws configure export-credentials --profile deploy --format env)"
  ```
  Run this in the same shell right before each `cdk` / `aws` command below.

## Deploy / re-deploy

The stacks import each other's resources (Backend ← Data + Auth; Frontend ←
Auth + Backend), so deploy all four; CDK orders them:

```bash
cd infra
eval "$(aws configure export-credentials --profile deploy --format env)"
cdk deploy GarDataStack GarAuthStack GarBackendStack GarFrontendStack --require-approval never
```

Fetch a stack's outputs any time:

```bash
aws cloudformation describe-stacks --profile deploy --stack-name GarAuthStack \
  --query 'Stacks[0].Outputs' --output table   # Issuer / M2mClientId / TokenEndpoint / ApiScope / UserPoolId
aws cloudformation describe-stacks --profile deploy --stack-name GarBackendStack \
  --query 'Stacks[0].Outputs' --output table   # ApiFunctionUrl / AnthropicSecretArn
```

> A fresh deploy creates a **new Lambda → new Function URL**, a **new Cognito
> pool/client** (new ids + a new client secret), and a placeholder Anthropic
> secret. Re-supply all three to clients (below). The DynamoDB table keeps its
> fixed name `gar-runs`; the S3 bucket gets a new generated name. **Past runs +
> audit history do not survive a destroy.**

## Post-deploy configuration

```bash
eval "$(aws configure export-credentials --profile deploy --format env)"
out() { aws cloudformation describe-stacks --profile deploy --stack-name "$1" \
  --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue" --output text; }

URL=$(out GarBackendStack ApiFunctionUrl)
ANTHROPIC_ARN=$(out GarBackendStack AnthropicSecretArn)
POOL_ID=$(out GarAuthStack UserPoolId)
CLIENT_ID=$(out GarAuthStack M2mClientId)
TOKEN_ENDPOINT=$(out GarAuthStack TokenEndpoint)
SCOPE=$(out GarAuthStack ApiScope)

# 1) Set the real Anthropic key (the deployed secret is a random placeholder):
aws secretsmanager put-secret-value --profile deploy --secret-id "$ANTHROPIC_ARN" \
  --secret-string "$(grep ANTHROPIC_API_KEY ../.env | cut -d= -f2- | tr -d '"'"'"'\r')"

# 2) Read the M2M client secret (Cognito generated it; not in the template):
CLIENT_SECRET=$(aws cognito-idp describe-user-pool-client --profile deploy \
  --user-pool-id "$POOL_ID" --client-id "$CLIENT_ID" \
  --query 'UserPoolClient.ClientSecret' --output text)

echo "Function URL:   $URL"
echo "Token endpoint: $TOKEN_ENDPOINT"
echo "Client id:      $CLIENT_ID   (secret fetched into \$CLIENT_SECRET)"
```

## Point clients at the cloud

**MCP server** — set on the `gar` MCP server entry, then restart the client:
```
GAR_API_URL=<ApiFunctionUrl>
GAR_COGNITO_TOKEN_ENDPOINT=<TokenEndpoint>
GAR_COGNITO_CLIENT_ID=<M2mClientId>
GAR_COGNITO_CLIENT_SECRET=<client secret from step 2>
GAR_COGNITO_SCOPE=<ApiScope, e.g. gar-api/access>
```
The client fetches a short-lived bearer token (client-credentials) and sends it
as `Authorization: Bearer`. Unset all of these to go back to a local backend
(auth disabled). See `docs/mcp.md`.

**Browser** — the SPA is served from CloudFront (`DistributionUrl` output) and
logs in via Cognito Hosted UI. Self-signup is disabled, so create a user first:

```bash
APP_URL=$(out GarFrontendStack DistributionUrl)
aws cognito-idp admin-create-user --profile deploy --user-pool-id "$POOL_ID" \
  --username you@example.com --user-attributes Name=email,Value=you@example.com Name=email_verified,Value=true
aws cognito-idp admin-set-user-password --profile deploy --user-pool-id "$POOL_ID" \
  --username you@example.com --password 'A-strong-passw0rd!' --permanent
echo "Open: $APP_URL"
```

Local dev needs none of this — the Vite proxy hits a local backend with auth
disabled, so the SPA skips login.

## Verify

```bash
# A token via client-credentials, then a gated call:
TOKEN=$(curl -s -u "$CLIENT_ID:$CLIENT_SECRET" \
  -d "grant_type=client_credentials&scope=$SCOPE" "$TOKEN_ENDPOINT" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

curl -s -o /dev/null -w "healthz:        %{http_code}\n" "$URL/healthz"                         # 200 (open)
curl -s -o /dev/null -w "runs no token:  %{http_code}\n" "$URL/runs"                            # 401
curl -s -o /dev/null -w "runs w/ token:  %{http_code}\n" -H "Authorization: Bearer $TOKEN" "$URL/runs"  # 200
```

## Custom domain (e.g. `https://gar.example.com`)

DNS lives at an external registrar (not Route 53), so validation + the alias
record are added there by hand; CDK only attaches the cert + alternate name to
CloudFront. One-time cert setup, then a `-c`-parameterised deploy. Substitute
your own subdomain for `gar.example.com` throughout.

**1. Request the cert in `us-east-1`** (CloudFront only accepts certs from that
region, regardless of where the app runs):

```bash
eval "$(aws configure export-credentials --profile deploy --format env)"
aws acm request-certificate --region us-east-1 \
  --domain-name gar.example.com --validation-method DNS \
  --query CertificateArn --output text          # → save as CERT_ARN
```

**2. Add the validation CNAME at your registrar.** Read the record ACM wants:

```bash
aws acm describe-certificate --region us-east-1 --certificate-arn "$CERT_ARN" \
  --query 'Certificate.DomainValidationOptions[0].ResourceRecord' --output table
```

In the registrar's DNS panel add that `Name → Value` as a **CNAME** (strip the
trailing dot; many panels append the zone automatically, so enter the host label
only). Wait until the cert is `ISSUED` (minutes to a few hours):

```bash
aws acm wait certificate-validated --region us-east-1 --certificate-arn "$CERT_ARN"
```

**3. Deploy the frontend with the domain + cert:**

```bash
cd infra
cdk deploy GarFrontendStack \
  -c frontendDomain=gar.example.com -c frontendCertArn="$CERT_ARN"
```

This adds `gar.example.com` as a CloudFront alternate name, attaches the cert, and
registers the domain in the SPA's Cognito callback/logout URLs (the CloudFront
name keeps working too). Note the stack's `SiteUrl` output.

**4. Point the domain at CloudFront** — add the final CNAME at your registrar:

```
gar.example.com  CNAME  <distribution>.cloudfront.net   # from the DistributionUrl output
```

Apex domains (`example.com` with no host) can't be a CNAME — use a subdomain like
`gar.` (as here), or front it with Route 53 / an ALIAS-capable provider.

Once DNS propagates, `https://gar.example.com` serves the SPA and OAuth login
round-trips on it. The cert is **independent of the stacks** — it survives a
`cdk destroy`, so re-deploys only repeat step 3 (reuse the same `CERT_ARN`).
Keep passing both `-c` flags on every `GarFrontendStack` deploy; omitting them
drops the alias and reverts to the CloudFront name.

## Destroy (back to ~zero cost)

```bash
cd infra
eval "$(aws configure export-credentials --profile deploy --format env)"
cdk destroy GarFrontendStack GarBackendStack GarAuthStack GarDataStack --force
```

`auto_delete_objects` empties the S3 bucket first; the DynamoDB table and the
Cognito pool are deleted immediately. **Secrets Manager** is the one lingering
cost: `destroy` schedules the Anthropic secret for deletion with a 30-day
recovery window (~$0.40/mo until it expires). To zero it now:

```bash
aws secretsmanager delete-secret --profile deploy \
  --force-delete-without-recovery --secret-id "$ANTHROPIC_ARN"
```

## Notes

- **Before there's any real data / users**, flip the table + bucket
  (`infra/stacks/data_stack.py`) and the User Pool (`infra/stacks/auth_stack.py`)
  to `RemovalPolicy.RETAIN` (the code comments flag this) so a `destroy` can't
  wipe them.
- `git push` over HTTPS can hang on the macOS keychain in some shells; push via
  gh's token instead: `git -c credential.helper='!gh auth git-credential' push …`.
