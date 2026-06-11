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

## The stale `resolutions` override trap (frozen-lockfile fails even when all ranges resolve)
A yarn v1 `resolutions` entry in `frontend/package.json` that forces a version
**absent from `yarn.lock`** makes `yarn install --frozen-lockfile` fail with
`Your lockfile needs to be updated` — even when every direct/transitive dependency
range IS present in the lock. yarn fires a *fresh* network resolution to apply the
override (visible in `--verbose` as a `GET .../<pkg>` for the overridden package,
then the abort), which frozen-lockfile forbids.

**How to diagnose:** `yarn install --frozen-lockfile --verbose 2>&1 | grep "GET http"`
— the package(s) it fetches right before the abort are the unsatisfied override(s).
Forcing a version also cascades: e.g. `@react-navigation/native` 7.3.0 pulls
`@react-navigation/core@^7.19.0` + a new `standard-navigation` dep not in the lock.

**Fix (lowest risk):** if the override is stale collateral from a reverted bump,
remove the `resolutions` entry so the lock's existing resolved version is honored
(changes zero resolved versions). Only honor the override (upgrade the lock) if the
forced version is genuinely intended — that changes runtime versions.

**Why:** a frozen lock must already contain the exact resolution the override demands;
otherwise yarn must resolve fresh, which `--frozen-lockfile` treats as "out of date".

## versionCode drift from failed `eas build` attempts
`app.json` `android.versionCode` is auto-incremented locally by EAS at build start,
*before* the build can fail on the sandbox's git-clone/disk-quota blockers. So even a
build that never leaves the sandbox bumps it. Keep commits scoped: reset versionCode
to the deliberate baseline if a stray bump appears in the working diff but you didn't
intend it.

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

## Corrupt yarn.lock → MISLEADING SyntaxError line number
A stray char committed into `yarn.lock` (e.g. a digit prepended to an entry key like
`7"@react-navigation/native@^7.1.8":`, from a botched merge/edit) makes
`yarn install` die with `SyntaxError: Unknown token` at a line number that is WRONG
(yarn's tokenizer position drifts). Find the real spot with `rg -n '^[0-9]' yarn.lock`
— a bare digit at column 0 is never valid at the top level of a lockfile.

## Regenerating yarn.lock in-sandbox: use --ignore-scripts
A plain (non-frozen) `yarn install` is hard-killed in this repl by the `prepare`
script (`git config core.hooksPath .husky`): the git-protection wrapper intercepts
the `.git` write and kills the process *before* the script's `|| true` can swallow
it. The kill strands `/home/runner/workspace/.git/config.lock`, which CANNOT be
`rm`'d (wrapper blocks all `.git` writes) but is harmless — it only blocks future
`git config`, not commits/checkpoints. To regen the lock, run
`yarn install --ignore-scripts` (yarn writes yarn.lock during resolution, before any
script runs). On EAS there is no wrapper, so `prepare`/`postinstall` run fine.
A frozen-lockfile sync mismatch (a package.json range not satisfied by the lock)
shows as `Your lockfile needs to be updated`; verbose `GET http...` lines name the
unsatisfied package. **Dedup caveat:** an incremental regen keeps the *existing*
resolution and adds the new one, leaving two versions of the same package — merge
the ranges onto the higher version by hand (e.g. `"pkg@^a", "pkg@^b":`) or the split
copy can cause runtime bugs (notably a dual @react-navigation/native breaks
expo-router's navigation context).
