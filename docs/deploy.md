# Deploy / re-deploy / destroy runbook (v2.0)

The v2.0 backend is plain CDK, so the cloud is reproducible from git: deploy is
`cdk deploy`, teardown is `cdk destroy`. This page is the bring-it-back guide —
the few things that **aren't** in code (secret values, the freshly-minted
Function URL and app key) and how to re-supply them.

Account/region come from the `deploy` profile (see CLAUDE.md). All commands
assume that profile resolves to `ap-northeast-1`.

## What's deployed

| Stack | Resources |
|---|---|
| `GarDataStack` | DynamoDB `gar-runs` (PK `run_id`, `tenant-index` GSI) · S3 state bucket (run-state pool + audit JSONL) — both `RemovalPolicy.DESTROY` |
| `GarBackendStack` | Lambda (arm64, Mangum) · Function URL (`NONE` + CORS) · self-invoke IAM · 2 Secrets Manager secrets (Anthropic key, app API key) |

`GarWorkflowStack` / `GarFrontendStack` / `GarAuthStack` are still scaffolds
(v2.1+). The `CDKToolkit` bootstrap stack is independent and is **not** removed
by destroying the app stacks — no re-bootstrap needed on re-deploy.

## Prerequisites

- `uv sync --all-packages` done; CDK CLI installed (`npm i -g aws-cdk`).
- **Docker running** — the Lambda asset is Docker-bundled (arm64).
- Fresh `deploy` creds in the env (the profile's session is short-lived):
  ```bash
  eval "$(aws configure export-credentials --profile deploy --format env)"
  ```
  Run this in the same shell right before each `cdk` / `aws` command below.

## Deploy / re-deploy

```bash
cd infra
eval "$(aws configure export-credentials --profile deploy --format env)"
cdk deploy GarDataStack GarBackendStack --require-approval never
```

The deploy prints CfnOutputs: `ApiFunctionUrl`, `AnthropicSecretArn`,
`AppApiKeySecretArn` (also `RunsTableName`, `StateBucketName`). Fetch them later
with:

```bash
aws cloudformation describe-stacks --profile deploy --stack-name GarBackendStack \
  --query 'Stacks[0].Outputs' --output table
```

> A fresh deploy creates a **new Lambda → new Function URL**, and CDK
> **regenerates** the app API key. Re-supply both to clients (below). The
> DynamoDB table keeps its fixed name `gar-runs`; the S3 bucket gets a new
> generated name (wired into the Lambda env automatically). **Past runs +
> audit history do not survive a destroy.**

## Post-deploy configuration (the 3 manual steps)

```bash
eval "$(aws configure export-credentials --profile deploy --format env)"
URL=$(aws cloudformation describe-stacks --profile deploy --stack-name GarBackendStack \
  --query "Stacks[0].Outputs[?OutputKey=='ApiFunctionUrl'].OutputValue" --output text)
ANTHROPIC_ARN=$(aws cloudformation describe-stacks --profile deploy --stack-name GarBackendStack \
  --query "Stacks[0].Outputs[?OutputKey=='AnthropicSecretArn'].OutputValue" --output text)
APIKEY_ARN=$(aws cloudformation describe-stacks --profile deploy --stack-name GarBackendStack \
  --query "Stacks[0].Outputs[?OutputKey=='AppApiKeySecretArn'].OutputValue" --output text)

# 1) Set the real Anthropic key (the deployed secret is a random placeholder):
aws secretsmanager put-secret-value --profile deploy --secret-id "$ANTHROPIC_ARN" \
  --secret-string "$(grep ANTHROPIC_API_KEY ../.env | cut -d= -f2- | tr -d '"'"'"'\r')"

# 2) Read the generated app API key (configure clients with it):
KEY=$(aws secretsmanager get-secret-value --profile deploy --secret-id "$APIKEY_ARN" \
  --query SecretString --output text)

# 3) Point clients at $URL with $KEY (next section).
echo "Function URL: $URL"
```

## Point clients at the cloud

**MCP server** — set on the `gar` MCP server entry, then restart the client:
```
GAR_API_URL=<ApiFunctionUrl>
GAR_API_KEY=<app key from step 2>
```
Unset both to go back to a local backend. (See `docs/mcp.md` / the config helper.)

**Frontend** — local dev needs nothing (the Vite proxy hits a local backend).
Public browser hosting against the cloud is **v2.1** (with Cognito) — see
`plan.md` D-205. To point a local dev build at the cloud anyway:
`VITE_GAR_API_URL=<url> VITE_GAR_API_KEY=<key> npm run dev`.

## Verify

```bash
curl -s -o /dev/null -w "healthz: %{http_code}\n" "$URL/healthz"          # 200 (open)
curl -s -o /dev/null -w "runs no key: %{http_code}\n" "$URL/runs"          # 401
curl -s -o /dev/null -w "runs keyed: %{http_code}\n" -H "X-GAR-API-Key: $KEY" "$URL/runs"  # 200
```

## Destroy (back to ~zero cost)

```bash
cd infra
eval "$(aws configure export-credentials --profile deploy --format env)"
cdk destroy GarBackendStack GarDataStack --force
```

`auto_delete_objects` empties the S3 bucket first; the DynamoDB table is
deleted immediately. **Secrets Manager** is the one lingering cost: `destroy`
schedules the two secrets for deletion with a 30-day recovery window (~$0.40/mo
each until it expires). To zero them now:

```bash
for ARN in "$ANTHROPIC_ARN" "$APIKEY_ARN"; do
  aws secretsmanager delete-secret --profile deploy --force-delete-without-recovery --secret-id "$ARN"
done
```

## Notes

- **Before there's any real data**, flip the table + bucket to
  `RemovalPolicy.RETAIN` in `infra/stacks/data_stack.py` (the code comments flag
  this) so a `destroy` can't wipe it.
- `git push` over HTTPS can hang on the macOS keychain in some shells; push via
  gh's token instead: `git -c credential.helper='!gh auth git-credential' push …`.
