# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

**RouTeD** is a full-stack delivery route optimization platform for solo couriers. It combines a FastAPI backend with a multi-solver VRP/TSP pipeline, machine learning for delivery pattern learning, and a cross-platform Expo React Native mobile app with native MapLibre maps.

---

## Commands

### Backend

```bash
# Run development server (from backend/)
cd backend && uvicorn server:app --reload --port 8000

# Run all tests
cd backend && pytest

# Run a single test file
cd backend && pytest tests/test_optimize_api.py

# Run a single test by name
cd backend && pytest tests/test_optimize_api.py::test_optimize_basic -v

# Formatting / linting
cd backend && black . && flake8 . && isort .

# Type checking
cd backend && mypy .
```

### Frontend

```bash
# Install dependencies (always use yarn)
cd frontend && yarn install

# Start Expo dev server (web preview)
cd frontend && yarn web

# Start Expo dev server (with device tunnel)
cd frontend && yarn start

# Unit tests
cd frontend && yarn test:unit

# Type check
cd frontend && npx tsc --noEmit

# Run pre-deploy checks
cd frontend && yarn deploy:preflight
# Or skip pytest:
bash /app/scripts/predeploy.sh --skip-tests
```

### Deployment

See `memory/DEPLOY.md` for the full three-layer deploy playbook. Summary:

| Layer | Trigger |
|---|---|
| **Backend** | Emergent UI → "Save to GitHub" → "Native Deploy" (NOT `git push`) |
| **Android binary (AAB)** | `cd frontend && eas build --platform android --profile production` (from laptop, not container) |
| **JS bundle (OTA)** | `git tag v2026.XX.XX && git push --tags` (triggers `eas-ota-update.yml`) or `cd frontend && yarn update:prod` |

**95% of frontend changes ship via OTA.** Only rebuild the AAB binary when native modules, `app.json` plugins, icons, or the Expo SDK change.

**Pre-flight check (run before every deploy):**
```bash
bash /app/scripts/predeploy.sh
```

---

## Architecture

### Backend (`backend/`)

The entire API lives in a single `server.py` (~11,000 lines). All FastAPI routes, the OSRM matrix service, ML integration, and solver dispatch are defined there. Supporting modules are:

- **`solvers/`** — Individual solver implementations. `coord_clustering.py` wraps every solver with same-doorstep super-node deduplication (prevents "Zero-Cost Interleaving" bugs). `pyvrp_tsp_solver.py` wraps PyVRP. `alns_hybrid.py` is the ALNS+SA metaheuristic.
- **`vrp_solver.py`** — OR-Tools VRP/TSP wrapper with Guided Local Search.
- **`timefold_solver.py`** — Optional Timefold solver (Java-based, gated by `ENABLE_TIMEFOLD` env).
- **`osrm_matrix_service.py`** — OSRM routing matrix fetcher with circuit-breaker, adaptive timeouts, and multi-host fallback ordering: loopback → primary remote → `OSRM_URL_PROD` (default `pathpilot-osrm.fly.dev`) → public demo.
- **`ml/`** — Three learners: `sequence_learner.py` (stop order habits), `service_time_learner.py` (per-address dwell time), `road_segment_learner.py` (preferred road segments for "Route Telepathy").
- **`models/`** — Pydantic request/response models (optimize, stops, routes, alerts, van_layout).
- **`tests/`** — 74 pytest files. Tests marked with the known-failing `test_alns_solver.py::test_health_check` (endpoint doesn't exist — ignore).

**Solver cascade** (priority order for `/api/optimize`):
1. VROOM + 3-opt (primary, needs OSRM matrix)
2. LKH-3 (Lin-Kernighan Heuristic binary, compiled from source)
3. OR-Tools with Guided Local Search
4. PyVRP
5. ALNS hybrid
6. Haversine fallback

All imports are guarded — if a solver binary/library is missing at startup, the flag (e.g. `LKH_AVAILABLE`) is set to `False` and that solver is skipped silently.

**Smart insertion** for late freight (post-lock adds): `ortools_smart_insertion` branch in `_optimize_route_inner()` holds locked stop order via a Position dimension and routes the unscheduled stops into cheapest gaps.

**Route Telepathy** (ML reorder): post-solve `ml/sequence_learner.apply_preferences()` runs for users in `TELEPATHY_USER_IDS` (defaults to `STRIPE_ADMIN_USER_IDS`).

### Frontend (`frontend/`)

Expo Router file-based routing. All screens are in `app/`. Main delivery screen is `app/(tabs)/index.tsx`.

Key architectural patterns:

- **State**: Zustand stores in `src/store/`. `stopsStore` is the primary data store for delivery stops.
- **Map**: `src/components/map/DeliveryMapNative.native.tsx` is the live native `@maplibre/maplibre-react-native` v11 map. Metro resolves `.native.tsx` on device and `.tsx` (web stub) on web. **Never import with `.native` extension** — let Metro resolve automatically.
- **Map imperative ref**: `DeliveryMapRef` pattern — the parent holds a ref to the map component and calls methods like `flyTo`, `fitBounds`, `setClusters`, `setDrawingMode`. Side-effects driven through refs keep GPS ticks from re-rendering the map.
- **Feature flags**: `src/utils/featureFlags.ts` — `useNativeMap` flag (persisted, env `EXPO_PUBLIC_USE_NATIVE_MAP`). Currently defaults `true` (native map is the live path).
- **Auth**: Supabase via `src/lib/supabase.ts`. `authFetch` wrapper attaches the session bearer token to all API calls. `DEV_MODE=true` on backend bypasses auth.
- **Navigation**: `useNavigationCamera` hook drives the driving-mode camera at 250ms intervals via `easeTo` (pitch 55°, zoom 17, course bearing). Min-displacement gate (3m / 1.4 m/s) prevents puck jitter.
- **OTA safety**: All map code that uses `@maplibre/maplibre-react-native` is gated behind the feature flag with `require()` so OTA deploys to old binaries never evaluate the native import.

### Platform variants

Files ending `.native.tsx` run on iOS/Android; `.tsx` files are the web stubs. This split prevents native-only modules (MapLibre, camera) from entering the web/SSR bundle.

### Tile pipeline

Backend serves self-hosted QLD cadastral data via `/api/tiles/buildings`, `/api/tiles/parcels`, `/api/tiles/addresses`. The map's `mapTileLoaders.ts` fetches slippy tiles on camera-idle (`onRegionDidChange`) with bounded FIFO caches (64 tiles) and self-debounced by a "view key".

---

## Environment variables

Backend config lives in `backend/.env` (copy from `backend/.env.example`). Key variables:

| Variable | Purpose |
|---|---|
| `MONGO_URL` | MongoDB Atlas connection string |
| `DB_NAME` | Database name (default: `routed`) |
| `MAPBOX_TOKEN` | Geocoding + directions fallback |
| `OSRM_URL` / `OSRM_URL_PROD` | OSRM routing service URLs |
| `SUPABASE_URL` + `SUPABASE_JWT_SECRET` | Auth |
| `STRIPE_API_KEY` + `STRIPE_PRICE_*` | Payments |
| `DEV_MODE=true` | Bypass auth for local development |
| `ENABLE_TIMEFOLD=false` | Enable the Java-based Timefold solver |
| `TELEPATHY_USER_IDS` | CSV of user IDs with ML route reordering |
| `STRIPE_ADMIN_USER_IDS` | CSV of admin user IDs (paywall bypass + Telepathy default) |

Frontend uses `frontend/.env` with `EXPO_PUBLIC_*` prefix for client-visible vars and `EXPO_PUBLIC_BACKEND_URL` pointing at the API server.

---

## Key constraints and gotchas

- **Solver imports are optional**: Every solver is wrapped in a try/except at module load. If `LKH_AVAILABLE = False`, the solver is skipped. Don't assume all solvers are always present.
- **`coord_clustering.cluster_aware_solve`** wraps every solver call — it merges stops at the same coordinates into super-nodes before solving and expands them after. Any new solver must go through this wrapper.
- **OSRM matrix timeouts**: Remote OSRM hosts (Fly.io) can take 10–45 seconds for large matrices on a cold start. The circuit-breaker tracks consecutive failures; always call `_osrm_note_success()` on the success path of any OSRM call, or the breaker will drift open.
- **OTA vs binary**: Adding any new native module (anything requiring `expo prebuild`) requires a full EAS binary build, not OTA. Check `eas.json` profiles before deploying.
- **`runtimeVersion` policy**: `app.json` uses `"policy": "appVersion"`. When `expo.version` is bumped with a new binary, new OTAs auto-target that version and won't land on older binaries.
- **Map imports**: Import map components without `.native` extension. Metro resolves the correct variant. Web stubs must not import `@maplibre/maplibre-react-native`.
- **Pre-commit hook**: Husky's `pre-commit` runs `scripts/pre-deploy-audit.sh` and blocks commits that contain secrets or populated `.env` values.
- **`test_alns_solver.py::test_health_check`**: Pre-existing failing test (endpoint removed). Ignore it.
- **`server.py` line count**: The file is ~11,000 lines. Use `Grep` to navigate — don't read the whole file.
- **LKH binary path**: Resolved by `install_native_solvers.LKH_BIN_PATH`. Falls back to `/usr/local/bin/LKH`. In containers, the binary is compiled from source during Docker build.
- **`buildings.db` path**: The tile DB resolves via `_resolve_tile_db_path()` which checks both `/app/tiles/buildings.db` (dev layout) and `/tiles/buildings.db` (container layout). The Dockerfile copies it to `/tiles/buildings.db`.

---

## CI / GitHub Actions

| Workflow | Trigger | Purpose |
|---|---|---|
| `eas-build-android.yml` | Manual dispatch or `build-*` tags | Build Android APK/AAB via EAS |
| `eas-ota-update.yml` | `v*.*.*` or `ota-*` tags | Publish OTA JS bundle to Expo CDN |
| `keepalive.yml` | Scheduled | Pings backend health to prevent MongoDB timeout |
| `latency-probe.yml` | Scheduled | Monitors API latency from multiple regions |
