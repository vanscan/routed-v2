#!/usr/bin/env bash
# gitignore-autoheal — no-op stub retained for call-site compatibility.
#
# The old version of this script stripped .env* blocking patterns from
# .gitignore because the Emergent deploy pipeline required .env files to
# be committed. That approach exposed live production secrets in the
# repository. The deploy pipeline now injects secrets via platform
# environment variables (Railway Variables / Fly.io secrets / Replit
# Secrets), so .env files must NOT be committed and .env* patterns in
# .gitignore are intentional and correct.
#
# This script now exits cleanly without modifying anything.

exit 0
