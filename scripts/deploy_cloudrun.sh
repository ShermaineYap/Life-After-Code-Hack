#!/usr/bin/env bash
# Deploy one data release to Cloud Run and record what it replaced.
#
#   scripts/deploy_cloudrun.sh <service-name> <image>
#
# Needs: GCP_PROJECT_ID, GCP_REGION, and GCP_SA_KEY (a CI "File" variable
# holding a service-account JSON key). Writes deploy.env (dotenv artefact) with
# SERVICE_URL, NEW_REVISION and PREVIOUS_REVISION so the monitor and rollback
# jobs know what to check and what to restore.
#
# Without GCP variables it runs in dry-run mode: nothing is deployed, the
# monitor checks the built warehouse instead, and the job says so.
set -euo pipefail
SERVICE="$1"; IMAGE="$2"

if [ -z "${GCP_PROJECT_ID:-}" ] || [ -z "${GCP_SA_KEY:-}" ]; then
  echo "Cloud Run not configured (GCP_PROJECT_ID / GCP_SA_KEY unset): dry-run deploy of $SERVICE."
  { echo "SERVICE_URL="; echo "NEW_REVISION=dry-run"; echo "PREVIOUS_REVISION="; echo "DEPLOY_MODE=dry-run"; } > deploy.env
  exit 0
fi

gcloud auth activate-service-account --key-file="$GCP_SA_KEY" --quiet
gcloud config set project "$GCP_PROJECT_ID" --quiet
REGION="${GCP_REGION:-asia-southeast1}"

PREVIOUS=$(gcloud run services describe "$SERVICE" --region "$REGION" \
  --format='value(status.traffic[0].revisionName)' 2>/dev/null || true)

gcloud run deploy "$SERVICE" --image "$IMAGE" --region "$REGION" \
  --allow-unauthenticated --port 8080 --memory 512Mi --cpu 1 \
  --min-instances 0 --max-instances 2 \
  --labels "app=downstream,release=$(echo "${RELEASE_VERSION:-dev}" | tr '.' '-' | tr 'A-Z' 'a-z')" \
  --quiet

URL=$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')
NEW=$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.latestReadyRevisionName)')
{ echo "SERVICE_URL=$URL"; echo "NEW_REVISION=$NEW"; echo "PREVIOUS_REVISION=$PREVIOUS"; echo "DEPLOY_MODE=cloudrun"; } > deploy.env
echo "Deployed $IMAGE to $URL as $NEW (previous: ${PREVIOUS:-none})"
