# RouTeD Backend — Coolify Deployment Playbook

> Last updated: 2026-06-06
> VPS: Vultr ($6/mo, Ubuntu 24.04, 2 GB RAM)
> Orchestrator: Coolify (self-hosted)
> Source: github.com/vanscan/routed-v2 (previously referenced as xmltvg-create/RouTeD)

---

## 0 — Current deployment status

**Production domain:** `https://api.getrouted.xyz`

### Smoke test results — 2026-06-06

All key endpoints verified healthy:

| Endpoint | Result |
|---|---|
| `GET /api/health` | `{"status":"healthy","database":"connected","supabase_configured":true}` |
| `GET /api/directions?coordinates=…` | `source: "osrm"` — OSRM circuit-breaker fix is live |
| `GET /api/tiles/buildings/metadata` | `{"total_buildings":"564624"}` |
| `GET /api/tiles/parcels/{z}/{x}/{y}.json` | HTTP 200, valid GeoJSON |
| `GET /api/tiles/addresses/{z}/{x}/{y}.json` | HTTP 200, valid GeoJSON |
| `GET /api/housenumbers?bbox=…` | HTTP 200, FeatureCollection with real features |

### GitHub sync status (as of 2026-06-06)

The Replit workspace `main` is **1 commit ahead** of `github.com/vanscan/routed-v2 main`. The Replit "Save to GitHub" action fails with a non-fast-forward 500 error. Outbound `git push` from the Replit container also times out (network restriction).

**To fix manually** (from a machine with GitHub credentials):
```bash
git clone https://github.com/vanscan/routed-v2
cd routed-v2
# Apply the workspace-only commit (title: "Update backend dependencies for improved functionality")
# which removes the emergentintegrations package from backend/requirements.txt
# Then:
git add backend/requirements.txt
git commit -m "Remove emergentintegrations dep; clean requirements for Coolify build"
git push origin main
```

Once pushed, trigger a Coolify redeploy (dashboard → Redeploy, or via API — see §2 below).
The production container is already healthy, so this sync is for repo hygiene only.

**Alternative — store a GitHub PAT in Replit secrets:**
```
Secret name: GITHUB_TOKEN
Value: ghp_<your Personal Access Token with repo write scope>
```
Then from Replit shell:
```bash
git remote set-url origin https://$GITHUB_TOKEN@github.com/vanscan/routed-v2
git push origin main
```

---

## 1 — Initial deploy (one-time, ~15 min)

### 1a. Vultr server
- vultr.com → Deploy New Server → Cloud Compute Regular
- OS: **Ubuntu 24.04 LTS x64**
- Plan: **$6/mo** (1 vCPU, 2 GB RAM)
- Region: closest to you
- Hostname: `routed-prod`
- Note the IPv4 and root password

### 1b. Install Coolify (one command, Vultr web console)
Vultr dashboard → server → **View Console** → log in as `root`:
```
curl -fsSL https://cdn.coolify.io/install.sh | bash
```
Takes 5-10 min. Dashboard URL printed at the end (e.g. `http://YOUR_IP:8000`).

### 1c. Coolify project setup
1. Create admin account in browser
2. **+ New Resource → Public Repository**
3. Repository URL: `https://github.com/xmltvg-create/RouTeD`
4. Branch: `main`
5. Build Pack: **Dockerfile**
6. Dockerfile path: `Dockerfile`
7. Port: `8080`
8. Health check path: `/api/health`

### 1d. Environment variables
Add these in Coolify's env-var settings (copy from existing Emergent backend `.env`):

| Key | Value |
|---|---|
| `MONGO_URL` | (your Atlas connection string) |
| `DB_NAME` | (your db name) |
| `EMERGENT_LLM_KEY` | (from Emergent profile) |
| `MAPBOX_TOKEN` | (your token) |
| `OSRM_URL` | `https://router.project-osrm.org` |
| `OSRM_PUBLIC_URL` | `https://router.project-osrm.org` |
| `STRIPE_API_KEY` | (your stripe key) |
| `STRIPE_PRICE_MONTHLY` | (your price id) |
| `STRIPE_PRICE_ANNUAL` | (your price id) |
| `STRIPE_WEBHOOK_SECRET` | (your webhook secret) |
| `ENABLE_TIMEFOLD` | `false` |
| `DEV_MODE` | `false` |
| `PORT` | `8080` |

### 1e. Deploy
Click **Deploy**. Watch the build log (~5 min). Test:
```
curl https://YOUR_COOLIFY_DOMAIN/api/health
```
Should return `{"status":"healthy", "database":"connected"}`.

### 1f. Point Android app at it
Once verified, run from the Emergent shell:
```
cd /app/frontend
EXPO_PUBLIC_BACKEND_URL=https://YOUR_COOLIFY_DOMAIN \
  eas update --branch production --message "Switch to Coolify"
```

---

## 2 — Updating later (each deploy)

### Path A — Once GitHub Save-to-Github is fixed (the normal path)

1. In Emergent chat, click **Save to GitHub**
   - Pushes changes to `github.com/xmltvg-create/RouTeD`
2. In Coolify dashboard → your app → click **Redeploy**
3. ~3-5 min build → live

**Optional: auto-deploy on push**
In Coolify: Application → Webhooks → **copy the webhook URL**
Then on GitHub: Repo Settings → Webhooks → Add webhook → paste the URL → events: "Just the push event"
After this, every Save-to-Github auto-triggers a Coolify redeploy. Zero-click updates.

### Path B — Manual sync (when GitHub is still broken)

If GitHub is still rejecting Save-to-Github:

1. From the Emergent shell, build a deployable archive:
   ```
   cd /app
   tar --exclude='frontend' \
       --exclude='node_modules' \
       --exclude='__pycache__' \
       --exclude='.git' \
       --exclude='.expo' \
       --exclude='*.log' \
       -czf /tmp/routed-deploy.tar.gz backend/ tiles/ Dockerfile railway.json
   ```
2. Transfer the archive to the Vultr server (replace YOUR_IP):
   ```
   scp /tmp/routed-deploy.tar.gz root@YOUR_IP:/opt/routed-update.tar.gz
   ```
3. SSH to Vultr (or use Vultr web console):
   ```
   cd /opt/coolify_workdir/routed && \
     tar -xzf /opt/routed-update.tar.gz && \
     curl -X POST https://YOUR_COOLIFY_URL/api/v1/applications/YOUR_APP_ID/deploy \
          -H "Authorization: Bearer YOUR_COOLIFY_API_TOKEN"
   ```

(Get the Coolify API token from Coolify settings → API Tokens.)

### Path C — Upload one file at a time (emergency hotfix)

For tiny hotfixes you can edit files directly in Coolify's web shell:
1. Coolify → your app → **Terminal** (web shell button)
2. Edit file (`nano server.py` etc.)
3. Trigger a redeploy from the dashboard

---

## 3 — Uptime monitoring (do this on day 1)

uptimerobot.com → free signup → Add Monitor:
- Type: HTTP(s)
- URL: `https://YOUR_COOLIFY_DOMAIN/api/health`
- Interval: 5 min
- Alert contacts: your email + phone

If your backend ever goes down (Coolify or VPS issue), you'll know within 5 min.

---

## 4 — Backup strategy (set up week 1)

### Vultr automatic backups
Vultr dashboard → server → **Backups** → enable. Costs +$1.20/mo. Daily snapshots, 1-click restore.

### MongoDB Atlas backups
Already on by default for Atlas paid tiers (M0 free tier has no backup — upgrade to M2 ~$9/mo for continuous backups). Not strictly needed if you don't lose mission-critical data daily.

---

## 5 — Cost rollup

| Item | Monthly cost |
|---|---|
| Vultr VPS ($6 plan) | $6.00 |
| Vultr backups (optional) | $1.20 |
| MongoDB Atlas (free M0) | $0 |
| UptimeRobot (free tier) | $0 |
| **Total (minimum viable)** | **$6.00** |
| **Total (recommended)** | **$7.20** |

vs. Fly.io (~$5/mo with the issues we hit) and Emergent Deploy ($10/mo).

---

## 6 — Troubleshooting

| Symptom | Fix |
|---|---|
| Build fails on `emergentintegrations` | The Dockerfile MUST include `--extra-index-url https://d33sy5i8bnduwe.cloudfront.net/simple/` — see Dockerfile in repo |
| Container exits with code 0 immediately | CMD is wrong — verify last line of Dockerfile starts uvicorn |
| Health check fails with 404 | Check Coolify path setting is `/api/health` (not `/health`) |
| `/api/tiles/buildings/metadata` returns "not available" | The 64 MB `tiles/buildings.db` didn't copy — check `.dockerignore` doesn't exclude `tiles/` |
| Container OOMs | Bump VPS to $12/mo plan (4 GB RAM) or set `ENABLE_TIMEFOLD=false` |

---

## 7 — Custom domain (when ready)

Buy a cheap domain ($1-12/year on Namecheap/Porkbun):
1. Create A record pointing your subdomain at the Vultr IP
2. In Coolify → Application → **Domains** → add your subdomain
3. Coolify auto-provisions a Let's Encrypt SSL cert (~30 sec)
4. Update `EXPO_PUBLIC_BACKEND_URL` via EAS OTA to use the new domain

Done — you own the URL forever, no platform lock-in.
