#!/usr/bin/env bash
# Send 100% of traffic back to the revision that served before this release.
#
#   scripts/rollback_cloudrun.sh <service-name>
#
# Reads PREVIOUS_REVISION / DEPLOY_MODE from the deploy job's dotenv artefact.
set -euo pipefail
SERVICE="$1"

if [ "${DEPLOY_MODE:-dry-run}" != "cloudrun" ]; then
  echo "Dry-run deployment: nothing to roll back on Cloud Run. The release stays unpromoted."
  exit 0
fi
if [ -z "${PREVIOUS_REVISION:-}" ]; then
  echo "No previous revision recorded (first deploy). Scaling the bad revision to zero traffic is not possible;"
  echo "the incident stays open for a human." >&2
  exit 1
fi

gcloud auth activate-service-account --key-file="$GCP_SA_KEY" --quiet
gcloud config set project "$GCP_PROJECT_ID" --quiet
gcloud run services update-traffic "$SERVICE" --region "${GCP_REGION:-asia-southeast1}" \
  --to-revisions "${PREVIOUS_REVISION}=100" --quiet
echo "Rolled $SERVICE back to $PREVIOUS_REVISION (bad revision: ${NEW_REVISION:-unknown})."
