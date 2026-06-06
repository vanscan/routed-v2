#!/usr/bin/env bash
# Submit the latest EAS production build to Play Store internal testing.
# Run this after the EAS production build finishes:
#   cd frontend && bash scripts/submit-to-play-store.sh
set -e

echo "Checking EAS auth..."
eas whoami

echo "Checking play-service-account.json..."
if [ ! -f play-service-account.json ]; then
  echo "ERROR: play-service-account.json not found."
  echo "Follow EAS_SUBMIT_GUIDE.md Step 1-2 to create it, then re-run this script."
  exit 1
fi

python3 -c "import json,sys; d=json.load(open('play-service-account.json')); print('Service account OK:', d.get('client_email','?'))" \
  || { echo "ERROR: play-service-account.json is not valid JSON. Check the file."; exit 1; }

echo "Submitting latest production build to Play Store internal track..."
eas submit --platform android --profile internal --latest

echo "Done! Check Play Console → Internal Testing for the new release."
