#!/usr/bin/env bash
# Idempotent bootstrap + redeploy for the patch-bot Cloud Run service.
#
# Prerequisites (one-time, before first run):
#   - Authenticated `gcloud` with project-owner-equivalent IAM in $PROJECT
#   - The three secret VERSIONS already added (the secrets themselves are
#     created by this script if missing):
#       printf '%s' "$PAT"              | gcloud secrets versions add patchbot-github-token --data-file=-
#       printf '%s' "$ANTHROPIC_API_KEY"| gcloud secrets versions add patchbot-anthropic-key --data-file=-
#       gcloud secrets versions add patchbot-targets --data-file=config/targets.yaml
#
# Updating the target list later does NOT require redeploying — just push a
# new version of the patchbot-targets secret; the bot fetches it on every /scan.

set -euo pipefail

PROJECT="${PROJECT:-patch-bot-prod}"
REGION="${REGION:-us-central1}"
SERVICE="patch-bot"
REPO="patch-bot"
RUNTIME_SA="patchbot-runtime"
SCHEDULER_SA="patchbot-scheduler"

echo "==> project=$PROJECT region=$REGION"
gcloud config set project "$PROJECT" >/dev/null

echo "==> enabling APIs"
gcloud services enable \
    run.googleapis.com cloudbuild.googleapis.com cloudscheduler.googleapis.com \
    secretmanager.googleapis.com artifactregistry.googleapis.com >/dev/null

echo "==> Artifact Registry repo"
if ! gcloud artifacts repositories describe "$REPO" --location="$REGION" >/dev/null 2>&1; then
  gcloud artifacts repositories create "$REPO" \
      --repository-format=docker --location="$REGION"
fi

echo "==> service accounts"
for SA in "$RUNTIME_SA" "$SCHEDULER_SA"; do
  if ! gcloud iam service-accounts describe \
        "${SA}@${PROJECT}.iam.gserviceaccount.com" >/dev/null 2>&1; then
    gcloud iam service-accounts create "$SA"
  fi
done

echo "==> secrets"
has_secret_version() {
  gcloud secrets versions list "$1" --limit=1 --format='value(name)' 2>/dev/null | grep -q .
}

for S in patchbot-github-token patchbot-anthropic-key patchbot-targets; do
  if ! gcloud secrets describe "$S" >/dev/null 2>&1; then
    gcloud secrets create "$S" --replication-policy=automatic
  fi
  gcloud secrets add-iam-policy-binding "$S" \
      --member="serviceAccount:${RUNTIME_SA}@${PROJECT}.iam.gserviceaccount.com" \
      --role=roles/secretmanager.secretAccessor >/dev/null
done

# Cloud Run requires every --set-secrets reference to resolve to a real version.
# Bootstrap placeholders so the deploy succeeds; caller overrides them with real
# values before triggering the first /scan.
TARGETS_YAML="$(dirname "$0")/../targets.yaml"
if ! has_secret_version patchbot-targets; then
  if [[ -f "$TARGETS_YAML" ]]; then
    echo "   uploading $TARGETS_YAML to patchbot-targets"
    gcloud secrets versions add patchbot-targets --data-file="$TARGETS_YAML" >/dev/null
  else
    echo "   no targets.yaml found — adding empty placeholder"
    printf '%s' "repos: []" | gcloud secrets versions add patchbot-targets --data-file=- >/dev/null
  fi
fi
if ! has_secret_version patchbot-github-token; then
  echo "   patchbot-github-token has no versions — adding placeholder (replace before triggering /scan)"
  printf '%s' "PLACEHOLDER_REPLACE_BEFORE_USE" | gcloud secrets versions add patchbot-github-token --data-file=- >/dev/null
fi
if ! has_secret_version patchbot-anthropic-key; then
  echo "   patchbot-anthropic-key has no versions — adding placeholder (replace before triggering /scan)"
  printf '%s' "PLACEHOLDER_REPLACE_BEFORE_USE" | gcloud secrets versions add patchbot-anthropic-key --data-file=- >/dev/null
fi

echo "==> build + push image"
TAG="$(git -C "$(dirname "$0")/.." rev-parse --short HEAD 2>/dev/null || date +%Y%m%d-%H%M%S)"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/patch-bot:${TAG}"
gcloud builds submit --tag "$IMAGE" "$(dirname "$0")/.."

echo "==> deploy Cloud Run"
gcloud run deploy "$SERVICE" \
    --image="$IMAGE" \
    --region="$REGION" \
    --service-account="${RUNTIME_SA}@${PROJECT}.iam.gserviceaccount.com" \
    --no-allow-unauthenticated \
    --memory=8Gi --cpu=2 --timeout=1800 --max-instances=1 \
    --set-secrets="PATCHBOT_GH_TOKEN=patchbot-github-token:latest,ANTHROPIC_API_KEY=patchbot-anthropic-key:latest" \
    --set-env-vars="TARGET_REPOS_SECRET=projects/${PROJECT}/secrets/patchbot-targets/versions/latest,PATCHBOT_SCHEDULER_SA=${SCHEDULER_SA}@${PROJECT}.iam.gserviceaccount.com"

echo "==> grant scheduler invoke"
gcloud run services add-iam-policy-binding "$SERVICE" \
    --region="$REGION" \
    --member="serviceAccount:${SCHEDULER_SA}@${PROJECT}.iam.gserviceaccount.com" \
    --role=roles/run.invoker >/dev/null

URL="$(gcloud run services describe "$SERVICE" --region="$REGION" --format='value(status.url)')"
SCAN_URL="${URL}/scan"

echo "==> Cloud Scheduler job"
if gcloud scheduler jobs describe patch-bot-scan --location="$REGION" >/dev/null 2>&1; then
  CMD=update
else
  CMD=create
fi
gcloud scheduler jobs $CMD http patch-bot-scan \
    --location="$REGION" \
    --schedule="0 */6 * * *" \
    --http-method=POST \
    --uri="$SCAN_URL" \
    --oidc-service-account-email="${SCHEDULER_SA}@${PROJECT}.iam.gserviceaccount.com" \
    --oidc-token-audience="$SCAN_URL" \
    --attempt-deadline=1800s

# Update the runtime SA env var so OIDC verification expects the right audience
gcloud run services update "$SERVICE" --region="$REGION" \
    --update-env-vars="PATCHBOT_OIDC_AUDIENCE=${SCAN_URL}" >/dev/null

echo
echo "Done."
echo "  Service:  $URL"
echo "  Trigger:  gcloud scheduler jobs run patch-bot-scan --location=$REGION"
echo "  Logs:     gcloud run services logs tail $SERVICE --region=$REGION"
