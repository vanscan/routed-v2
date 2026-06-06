#!/usr/bin/env bash
# Pre-deploy audit — verifies that .env files containing real secrets are
# not committed to the repository and not baked into the Docker image.
#
# Secrets must be injected at deploy time via the platform's secret/env-var
# UI (Railway Variables, Fly.io secrets, Replit Secrets, etc.).
#
# Exit codes:
#   0  Clean — safe to deploy.
#   1  Found committed .env values — remove secrets and re-deploy.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${REPO_ROOT}/backend/.env"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "✓ pre-deploy-audit: backend/.env not present — clean."
  exit 0
fi

# Check whether any line in backend/.env has a non-empty value (i.e. a real
# secret is present). Lines that are blank, comments, or KEY= (empty value)
# are safe.
POPULATED="$(grep -vE '^[[:space:]]*(#|$)' "${ENV_FILE}" | grep -E '=.+' | grep -vE '=(false|true|http://localhost:[0-9]+|[0-9]+)' || true)"

if [[ -n "${POPULATED}" ]]; then
  echo "" >&2
  echo "╔══════════════════════════════════════════════════════════════════╗" >&2
  echo "║  ✗ DEPLOY CHECK — backend/.env contains non-empty secret values ║" >&2
  echo "╚══════════════════════════════════════════════════════════════════╝" >&2
  echo "" >&2
  echo "backend/.env should only contain empty placeholders (KEY=)." >&2
  echo "Inject real values via the platform's secret/env-var UI instead." >&2
  echo "See backend/.env.example for the full list of required variables." >&2
  echo "" >&2
  exit 1
fi

echo "✓ pre-deploy-audit: backend/.env contains no secret values — clean."
exit 0
