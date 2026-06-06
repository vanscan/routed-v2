# Threat Model

## Project Overview

RouTeD is a route-optimization and delivery-management application with a public FastAPI backend and an Expo/React Native client. Drivers authenticate through either a legacy session flow backed by an external Emergent OAuth/session service or bearer tokens from Supabase/Google, then manage stops, optimize routes, review history, export data, and interact with map/telemetry features. The intended production backend is internet-reachable and talks directly to MongoDB plus several third-party services (Stripe, Supabase JWKS, Google token verification, ArcGIS, Mapbox, OSRM, and OpenAI TTS).

Production-scoping assumptions for future scans:
- Focus on vulnerabilities reachable in production, not local/dev-only tooling.
- `NODE_ENV` can be assumed to be `production` in production deployments.
- TLS for deployed traffic is handled by the platform.
- This deployment is public, so unauthenticated endpoints are internet-exposed.
- On 2026-06-06, the advertised public deployment URL returned Replit's "This app isn't live yet" placeholder, so runtime validation was limited; findings in this scan are based on production-reachable code paths that would become exposed when the deployment is live.

## Assets

- **User accounts and active sessions** — session cookies, bearer tokens, reviewer/admin identities, and any account-linking data in MongoDB. Compromise allows impersonation and access to route/workflow data.
- **Driver route data** — stops, route history, navigation progress, delivery notes, van layouts, and export files. This data can include sensitive addresses and operational details.
- **Billing and entitlement state** — subscription records, reviewer/admin bypasses, and Stripe customer/subscription identifiers. Unauthorized changes could unlock paid features or expose business data.
- **Operational diagnostics and deployment artifacts** — debug endpoints, build metadata, logs, temporary exports, and sync archives. These can leak internal state or code if exposed publicly.
- **Application secrets and third-party credentials** — Mongo connection info, Stripe secrets, Mapbox token, Supabase configuration, reviewer passcodes, and TTS/API keys.

## Trust Boundaries

- **Client to backend API** — every request from the mobile app, web client, or arbitrary internet caller is untrusted until authenticated and authorized server-side.
- **Backend to MongoDB** — backend code has broad access to tenant data; injection or authorization flaws here become full data-access issues.
- **Backend to external services** — the backend fetches/validates data from external services and must not treat upstream responses as implicitly safe.
- **Unauthenticated to authenticated** — health, map/tile, waitlist, and other public endpoints are exposed without login; authenticated endpoints must not be reachable via alternate public paths.
- **Authenticated to admin/reviewer bypasses** — reviewer/admin allowlists and paywall bypasses create privileged paths that must be tightly scoped and never become generic backdoors.
- **Production to dev-only/test surfaces** — map-test, cluster-test, diagnostics, migration helpers, and similar endpoints should be treated as out of scope unless they remain reachable in the production app.

## Scan Anchors

- **Production entry points:** `backend/server.py`, `backend/routes/auth.py`, `backend/routes/stops.py`, `backend/routes/billing.py`, `backend/routes/waitlist.py`, `backend/routes/tiles.py`.
- **Highest-risk areas:** auth/session parsing in `backend/server.py`, legacy auth exchange in `backend/routes/auth.py`, public operational endpoints near the bottom of `backend/server.py`, unauthenticated alert and routing helpers, billing/waitlist privilege checks, and any file export/import paths.
- **Public surfaces:** root probes, `/api/health*`, `/api/waitlist/*` public routes, `/api/alerts*`, `/api/directions`, map/tile endpoints, build/debug/meta endpoints, and any unauthenticated download/test routes.
- **Authenticated surfaces:** stop CRUD, optimize/import jobs, route history, telemetry, van layout, billing status/checkout.
- **Dev-only areas usually ignorable unless reachable:** Expo/mobile local storage helpers, standalone test pages, benchmark/test harnesses, and scripts under `backend/tests`, `frontend/scripts`, `scripts/`.

## Threat Categories

### Spoofing

Authentication is split across multiple trust paths: legacy cookies/sessions, Supabase bearer tokens, Google ID tokens, and special reviewer flows. The backend must verify every token against the intended audience and issuer, must reject tokens minted for other clients or environments, and must ensure reviewer/admin bypasses cannot be used by general users.

### Tampering

Drivers can modify stops, route order, optimization inputs, imports, alerts, and billing-triggered actions. The backend must treat all client data as attacker-controlled, enforce ownership checks on every write, and ensure expensive or privileged operations cannot be triggered or altered cross-tenant or cross-origin.

### Information Disclosure

The system stores route history, stop addresses, notes, export files, operational telemetry, and deployment diagnostics. Public or weakly protected debug/download endpoints, over-broad API responses, and any cross-origin exposure of authenticated responses could leak sensitive route and operational data.

### Denial of Service

Several endpoints trigger heavy work: route optimization, imports, geocoding, directions, tile fetches, and TTS generation. Public or weakly gated access to these paths can increase compute or third-party API consumption, so production routes must enforce authentication, entitlement checks, and reasonable bounds on user-controlled input.

### Elevation of Privilege

The project contains privileged reviewer/admin flows and code paths that bypass normal paywalls or gating. Server-side authorization must be independent of the client, and no temporary migration, reviewer, debug, or operational endpoint may provide access to code, data, or features beyond the caller’s intended privilege level.
