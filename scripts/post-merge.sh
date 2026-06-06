#!/bin/bash
set -e

echo "[post-merge] Installing backend dependencies..."
pip install -r backend/requirements.txt --no-deps -q 2>&1 | tail -5 || true

echo "[post-merge] Installing frontend dependencies..."
cd frontend && npm install --legacy-peer-deps -q 2>&1 | tail -5
cd ..

echo "[post-merge] Done."
