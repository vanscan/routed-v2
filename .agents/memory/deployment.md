---
name: Replit publish / deployment setup
description: How RouTr is published on Replit and the env-file quirks that bite during deploys
---

## What Replit publishes
RouTr is published on Replit as a **static Expo web export**, NOT the full stack.

- Deployment target: `static`; build runs `expo export -p web` (output dir `frontend/dist`, set as `publicDir`).
- The web app talks to the **already-running production backend at `api.getrouted.xyz`** (Coolify-hosted), confirmed live. The FastAPI backend is intentionally NOT run on Replit's autoscale.

**Why:** The old config tried to run backend + Expo *dev* server together on autoscale. That can't work: Expo's web command binds to `localhost` (and refuses `0.0.0.0`) so the health probe never gets a 200; autoscale exposes only one port; the browser was pointed at `localhost:8000` (the visitor's machine); and the backend would crash on startup because its secrets aren't in the published bundle. Static + external backend sidesteps all of it.

## Production web config
`frontend/.env.production` (committed; contains only **public** EXPO_PUBLIC_* client values that ship in the JS bundle anyway) holds the prod backend URL + Supabase + Google web client id. Expo auto-loads `.env.production` in production mode (`expo export` defaults to production), so dev keeps using `.env` (local backend) untouched.

**How to apply:** If prod web breaks on config (missing Supabase, wrong backend URL), check `frontend/.env.production`, not Replit env vars — the static builder bakes values from that file at export time.

## .env file quirks (these bite after any git sync)
- `.env` files are blocked by the **system-level `/etc/.gitignore`**, so they never commit even though the repo's own `.gitignore` says it intentionally tracks them. They live on disk only.
- A git pane Pull/checkout (or any branch reset) **wipes the on-disk `backend/.env` and `frontend/.env`**, which silently breaks the backend (`KeyError: MONGO_URL`) and frontend config. Restore them from git history: `git show <ancestor>:backend/.env > backend/.env`.
- The `frontend/.env` stored in history carries stale **Emergent dev-tunnel vars** (`EXPO_PACKAGER_PROXY_URL`, `EXPO_PACKAGER_HOSTNAME`, `EXPO_TUNNEL_SUBDOMAIN`, `METRO_CACHE_ROOT`). Leaving `EXPO_PACKAGER_PROXY_URL` (points at emergentagent.com) in place makes Metro wait on an external proxy and **port 5000 never serves locally**. Strip those four keys; keep `EXPO_PUBLIC_*` and set `EXPO_PUBLIC_BACKEND_URL=http://localhost:8000` for dev.
- Expo CLI rejects host `0.0.0.0`; dev web uses `--host localhost`.

## Security hardening vs. the static deploy (recurring conflict)
A security pass set `.gitignore` to ignore `.env`, `.env.*`, `*.env` (only `!*.env.example` allowed) to keep secret `.env` files out of git. That rule also matches `frontend/.env.production` and **untracks it**, so the static deploy builds from a tree with no production config → published web app loses its backend URL / Supabase / Google IDs (or the build is misconfigured).

**Fix kept in place:** a scoped `.gitignore` negation `!frontend/.env.production`. That file holds ONLY public `EXPO_PUBLIC_*` client values (compiled into the browser bundle anyway — not secret), and the security audit/husky hooks only inspect `backend/.env`, so re-tracking it is safe and doesn't trip the guards.

**Why:** `EXPO_PUBLIC_*` vars are baked into the client at export time; the deploy builder only sees git-committed files, not gitignored on-disk ones.
**How to apply:** If a future security/lint pass re-adds a blanket `.env*` ignore, re-assert the `!frontend/.env.production` exception or the next publish silently ships without prod config. `git add` is blocked for the main agent — rely on the end-of-task auto-commit (it respects the updated `.gitignore`) to track the file.
