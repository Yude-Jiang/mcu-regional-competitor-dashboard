#!/usr/bin/env bash
# deploy.sh — Build & deploy MCU dashboard to Cloud Run (run in Google Cloud Shell)
set -euo pipefail

PROJECT="st-china-ai-force"
REGION="asia-east1"
SERVICE="mcu-regional-competitor-dashboard"
IMAGE="asia-east1-docker.pkg.dev/${PROJECT}/mcu/${SERVICE}"

gcloud config set project "${PROJECT}"

COMMIT="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
BRANCH="$(git branch --show-current 2>/dev/null || echo unknown)"
echo "==> Deploying from branch: ${BRANCH} @ ${COMMIT}"
if [[ "${BRANCH}" != "main" ]]; then
  echo "WARNING: not on main — run: git checkout main && git pull"
fi

echo "==> [1/2] Building image and pushing to Artifact Registry..."
gcloud builds submit --project "${PROJECT}"

if ! gcloud secrets describe FLASK_SECRET_KEY --project="${PROJECT}" &>/dev/null; then
  echo "==> Creating FLASK_SECRET_KEY secret (one-time)..."
  openssl rand -hex 32 | gcloud secrets create FLASK_SECRET_KEY --data-file=- --project="${PROJECT}"
fi

echo "==> [2/2] Deploying to Cloud Run..."
gcloud run deploy "${SERVICE}" \
  --image "${IMAGE}" \
  --region "${REGION}" \
  --platform managed \
  --allow-unauthenticated \
  --set-env-vars "GCP_PROJECT=${PROJECT},BQ_DATASET=mcu,GCS_BUCKET=st-finance-reports,AUTH_EMAIL_DOMAIN=@st.com" \
  --set-secrets "VITE_DEEPSEEK_API_KEY=VITE_DEEPSEEK_API_KEY:latest,FLASK_SECRET_KEY=FLASK_SECRET_KEY:latest" \
  --project "${PROJECT}"

echo ""
echo "==> Deploy complete. Service URL:"
gcloud run services describe "${SERVICE}" \
  --region "${REGION}" \
  --project "${PROJECT}" \
  --format='value(status.url)'
echo ""
echo "NOTE: Access gate enabled — users must enter an @st.com email on /login."
echo "      Create FLASK_SECRET_KEY secret if missing:"
echo "        openssl rand -hex 32 | gcloud secrets create FLASK_SECRET_KEY --data-file=- --project=${PROJECT}"
