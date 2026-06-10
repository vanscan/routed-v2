#!/bin/bash
# SessionStart hook: install backend (pip) + frontend (yarn) dependencies so
# pytest, black/flake8/isort, tsc, and yarn test:unit work in Claude Code on
# the web sessions. Idempotent — package managers skip already-satisfied deps.
set -euo pipefail

# Only needed in remote (web) containers; local machines manage their own envs.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

echo "[session-start] Installing backend Python dependencies..."
# Three known-bad pins in this frozen requirements.txt (verified against the
# working container image):
#   - google-ai-generativelanguage==0.12.0 conflicts with the pinned
#     google-generativeai==0.8.6, which requires exactly 0.6.15 (the version
#     the image ships).
#   - pydantic_core==2.47.0 is incompatible with the pinned pydantic==2.13.4,
#     which requires exactly 2.46.4 (the version the image ships). Installing
#     2.47.0 hard-breaks every pydantic import.
#   - openlocationcode==1.0.1 is sdist-only and fails to build under the
#     container's setuptools (install_layout bug); it is preinstalled in the
#     base image.
# Filter them out and install the rest with --no-deps (the file is a full
# freeze, so resolution is unnecessary and only re-surfaces the conflicts).
grep -vE '^(openlocationcode|google-ai-generativelanguage|pydantic_core)==' \
  backend/requirements.txt > /tmp/session-start-reqs.txt
pip install --quiet --no-deps -r /tmp/session-start-reqs.txt

# Sanity check: core backend imports must resolve.
python3 -c "import fastapi, pytest, motor, openlocationcode"

# Seed a minimal backend/.env so `import server` (which every pytest file
# does) survives `os.environ['MONGO_URL']`. The file is gitignored; tests
# never hit a live Mongo — Motor only connects lazily on first query.
if [ ! -f backend/.env ]; then
  cat > backend/.env <<'ENV'
MONGO_URL=mongodb://localhost:27017
DB_NAME=routed_test
DEV_MODE=true
ENV
  echo "[session-start] Created minimal backend/.env for tests"
fi

echo "[session-start] Installing frontend dependencies (yarn)..."
(cd frontend && yarn install --frozen-lockfile --non-interactive)

echo "[session-start] Done."
