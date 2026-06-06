---
name: EAS Android build from Replit sandbox
description: Why EAS cloud builds of the frontend fail at "Install dependencies" and how to submit them correctly from this repl.
---

# EAS Android production-apk builds (frontend = Expo/React Native)

## The lockfile-registry trap (root cause of repeated "build failed to publish")
Regenerating `frontend/yarn.lock` *inside the Replit sandbox* bakes in resolved URLs
pointing at Replit's internal registry `http://package-firewall.replit.local/npm/...`
(seen on ~22 optional platform binaries: @expo/ngrok-bin-*, lightningcss-*, fsevents).
EAS cloud builders cannot resolve that host, so `yarn install --frozen-lockfile`
dies in the *Install dependencies* phase with `getaddrinfo ENOTFOUND
package-firewall.replit.local`.

**Fix:** after any yarn.lock regen, rewrite the host back to the public registry:
`sed -i 's#http://package-firewall.replit.local/npm/#https://registry.npmjs.org/#g' frontend/yarn.lock`
Verify `grep -c package-firewall frontend/yarn.lock` returns 0.

**Why:** the sandbox proxies npm through an internal firewall host; that host is
private to Replit and unreachable from Expo's build infra.

## How to submit a build from this repl (all required together)
```
cd frontend && EAS_SKIP_AUTO_FINGERPRINT=1 EAS_NO_VCS=1 \
  EAS_PROJECT_ROOT=/home/runner/workspace/frontend \
  eas build --platform android --profile production-apk --non-interactive --no-wait
```
- `EAS_NO_VCS=1` — Replit sandbox blocks git index writes, so VCS mode fails.
- `EAS_PROJECT_ROOT=.../frontend` — without it noVcs archives from git root
  (`git rev-parse --show-toplevel` = workspace) and reads the wrong `.easignore`,
  ballooning the archive to 160MB+. With it, archive is ~4.5MB.
- `EAS_SKIP_AUTO_FINGERPRINT=1` — the fingerprint step frequently hangs forever here.
- `--no-wait` — the foreground waiter often gets killed; submit then poll via GraphQL.

## .easignore gotchas
Use bare names without trailing slash (`.metro-cache`, not `.metro-cache/`) — the
`ignore` pkg won't match a dir path with a trailing slash during fs.cp filtering.
Also exclude `.expo`, `.expo-shared`, `package-lock.json` (EAS uses yarn, not npm).

## Reading build status / logs via GraphQL (logFiles are BROTLI, not gzip)
Status/error: POST https://api.expo.dev/graphql (Bearer $EXPO_TOKEN)
`{ builds { byId(buildId:"...") { status error{message errorCode} artifacts{buildUrl} } } }`
The `logFiles[0]` signed URL (expires ~900s) returns a **brotli** stream whose first
bytes are `8b1e5f00` (NOT gzip 1f8b). Decode with Node:
`zlib.brotliDecompressSync(fs.readFileSync(file))` → JSONL.

## eas-cli local patch (corrupt-file crash)
`makeShallowCopyAsync` filter in
`.config/npm/node_global/lib/node_modules/eas-cli/build/vcs/local.js` was patched to
early-return false for paths containing control chars or `@@` so archiving doesn't
crash on corrupt filenames in the tree.
