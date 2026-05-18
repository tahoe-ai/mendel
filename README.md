# patch-bot

A Cloud Run service that closes two gaps in Dependabot:

1. **Coverage** — pulls every open HIGH/CRITICAL Dependabot security alert across your target repos and ensures each has a PR. Enriches Dependabot's PR if it exists; opens its own if not.
2. **Decision support** — every PR body includes CVE/CVSS context, GAE runtime compatibility check, upstream changelog diff with breaking-change signals, transitive dependency delta, project-surface analysis (monkey-patches, wrapper vs spread imports, uncovered call sites, open issues), and an LLM-generated risk summary.

A Cloud Scheduler job hits `POST /scan` every 6 hours.

## Architecture

- **Cloud Run** service (Python 3.12 + Node 20, ~400 MB image) regenerates lockfiles for whichever ecosystem the target repo uses (Poetry / pip-tools / Pipenv / bare requirements.txt / npm / yarn / pnpm).
- **Cloud Scheduler** job triggers `POST /scan` with OIDC auth.
- **Secret Manager** holds the GitHub PAT and Anthropic API key.
- **No external state** — reconciliation reads alerts, PRs, labels, and hidden GHSA markers from GitHub on every run.

## Setup

### 1. Mint the GitHub PAT

Create a fine-grained PAT scoped to the target repos with:
- `Dependabot alerts: read`
- `Contents: write`
- `Pull requests: write`
- `Metadata: read`

### 2. Prepare the target-repo list

```bash
cp config/targets.yaml.example targets.yaml
# edit targets.yaml — list each owner/repo to scan
```

This file is uploaded to Secret Manager (not committed to the image), so updates take effect on the next scan without redeploying.

### 3. Bootstrap GCP

```bash
export PROJECT=patch-bot-prod
export REGION=us-central1
bash deploy/deploy.sh
```

The script creates the Artifact Registry repo, two service accounts (`patchbot-runtime`, `patchbot-scheduler`), three secrets (empty: `patchbot-github-token`, `patchbot-anthropic-key`, `patchbot-targets`), the Cloud Run service, and the Cloud Scheduler job. **It will not work until all three secrets have values.**

### 4. Add secret values

```bash
printf '%s' "$YOUR_GITHUB_PAT"     | gcloud secrets versions add patchbot-github-token --data-file=-
printf '%s' "$YOUR_ANTHROPIC_KEY"  | gcloud secrets versions add patchbot-anthropic-key --data-file=-
gcloud secrets versions add patchbot-targets --data-file=targets.yaml
```

### Updating the target list later

Just push a new secret version — no redeploy needed. The next `/scan` picks it up:

```bash
# edit targets.yaml, then:
gcloud secrets versions add patchbot-targets --data-file=targets.yaml
```

### 5. Re-run deploy.sh and trigger a scan

```bash
bash deploy/deploy.sh
gcloud scheduler jobs run patch-bot-scan --location=us-central1
gcloud run services logs tail patch-bot --region=us-central1
```

## Per-repo config

Drop a `.patchbot.yml` at the root of any target repo to override defaults. See [.patchbot.yml.example](./.patchbot.yml.example).

## Local development

```bash
uv sync
PATCHBOT_AUTH_DISABLE=1 \
PATCHBOT_GH_TOKEN=ghp_... \
ANTHROPIC_API_KEY=sk-ant-... \
TARGET_REPOS=your-org/your-repo \
uv run uvicorn patch_bot.web.app:app --reload --port 8080
curl -X POST http://localhost:8080/scan
```

Or run the CLI directly:

```bash
PATCHBOT_GH_TOKEN=... ANTHROPIC_API_KEY=... TARGET_REPOS=... \
uv run python -m patch_bot scan-alerts
```

## Suppressing a PR

Close the PR with the `patch-bot/wontfix` label — the bot will not re-open it on subsequent scans.

## Limitations (v1)

- Lockfile updaters cover Python (Poetry / pip-tools / Pipenv / requirements.txt) and Node (npm / yarn / pnpm). PDM, Hatch, conda, Cargo, and Go modules emit an enrichment-only comment on Dependabot's PR; the bot will not open one of its own.
- Code-impact analysis is import-level only. Vulnerable-function call-site analysis (libcst / tree-sitter) is v1.5.
- GAE runtime version data is a vendored YAML at `scripts/patch_bot/runtimes/data/gae_runtimes.yaml`; refresh quarterly.
- Real-time webhook reaction is v1.5. Today, the worst-case latency is ~6h.
- `vault_ids`, multi-`app.yaml` monorepos, and SARIF emission are out of scope for v1.
