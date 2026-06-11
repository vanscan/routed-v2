"""Health / readiness endpoints under the /api prefix.

    GET|HEAD /health          → legacy health check (always 200, reports mongo state)
    GET      /healthz         → readiness probe (503 on degraded, build + cache stats)
    GET      /healthz/version → version-only sub-path, zero I/O

Split out of server.py for maintainability. The build SHA and process
start time are captured ONCE at module import (this module is imported
while server.py is loading, so the values match the old in-server
behaviour). `db` and `SUPABASE_JWT_SECRET` are lazy-imported from
`server` at request time.

The root-level (no /api prefix) probes — `/health`, `/ready`, `/live` —
stay in server.py: they're bound to `app`, not `api_router`.
"""
from __future__ import annotations

import logging
import os
import time as _time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Response

logger = logging.getLogger("server")
router = APIRouter()

_BACKEND_DIR = Path(__file__).resolve().parent.parent  # …/backend


# ── Readiness probe ──────────────────────────────────────────────────────
# Captured ONCE at module load — cheap, deterministic, and means a single
# git fork later in the day doesn't drift the value during a pod's lifetime.
# Source priority:
#   1. GIT_SHA / RELEASE_SHA / EMERGENT_BUILD_SHA env vars (CI bakes them in)
#   2. `git rev-parse --short HEAD` if a .git dir is present in the image
#   3. literal "unknown" — never fail the import on this
def _resolve_build_sha() -> str:
    for env_key in ("GIT_SHA", "RELEASE_SHA", "EMERGENT_BUILD_SHA", "SOURCE_VERSION"):
        v = os.environ.get(env_key)
        if v:
            return v.strip()[:12]
    try:
        import subprocess
        out = subprocess.check_output(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=str(_BACKEND_DIR.parent),
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
        return out.decode("ascii", "ignore").strip() or "unknown"
    except Exception:
        return "unknown"


_BUILD_SHA = _resolve_build_sha()
_PROCESS_STARTED_AT = datetime.now(timezone.utc)


@router.get("/health")
@router.head("/health")
async def health_check():
    """Health check endpoint with MongoDB connection verification.
    Supports both GET and HEAD methods for uptime monitoring services."""
    from server import SUPABASE_JWT_SECRET, db  # noqa: WPS433
    try:
        # Verify MongoDB connection
        await db.command('ping')
        return {
            "status": "healthy",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "database": "connected",
            "supabase_configured": bool(SUPABASE_JWT_SECRET),
        }
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")
        # Static literal only — anything derived from the exception (even the
        # class name) trips CodeQL py/stack-trace-exposure, and raw str(e)
        # genuinely leaks connection details (hosts, URIs) to unauthenticated
        # callers. Full detail is in the log line above.
        return {
            "status": "unhealthy",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "database": "disconnected",
            "error": "database_unavailable"
        }


@router.get("/healthz")
async def readiness_probe(response: Response):
    """Lightweight readiness probe for the deploy panel.

    Designed to be polled by Emergent's K8s readiness check. Returns:
      • build.sha — captured once at module load (env var preferred,
        falls back to local `git rev-parse`, then to 'unknown'). Lets you
        eyeball deploy-time vs runtime config drift at a glance.
      • build.uptime_sec — process uptime since the worker started.
      • mongo — Atlas connection state with round-trip latency. The
        actual blocker if anything's wrong in production.
      • tile_cache — row count + bytes on disk + hit rate (from the
        existing stats_sync()). Useful for spotting cold-pod vs warm-pod
        traffic differences in the deploy panel without log diving.
      • solvers — availability flag per optimizer engine, read from the
        guarded-import flags in server.py. One curl tells you whether the
        deployed image actually has the solver an app build is about to
        request (a stale deploy silently falls back otherwise). Booleans
        only — the *_IMPORT_ERROR strings stay server-side.
      • status — 'ok' if mongo ping succeeded, 'degraded' otherwise.
        HTTP 503 on degraded so the K8s probe can mark the pod
        unready and pull it out of the load-balancer rotation.

    Deliberately distinct from /api/health (which always returns 200,
    even when mongo is down) so we don't break anything that relies
    on the older endpoint's contract.
    """
    from server import db  # noqa: WPS433

    # ── Mongo ping with latency ─────────────────────────────────────────
    mongo_block: Dict[str, Any]
    mongo_ok = False
    t0 = _time.perf_counter()
    try:
        await db.command("ping")
        mongo_ok = True
        mongo_block = {
            "connected": True,
            "db_name": os.environ.get("DB_NAME", ""),
            "ping_ms": round((_time.perf_counter() - t0) * 1000, 2),
        }
    except Exception as e:
        logger.warning("readiness probe mongo ping failed: %s", e)
        # Static literal only in the response body — see health_check above.
        mongo_block = {
            "connected": False,
            "db_name": os.environ.get("DB_NAME", ""),
            "ping_ms": round((_time.perf_counter() - t0) * 1000, 2),
            "error": "mongo_ping_failed",
        }

    # ── Tile cache stats (best-effort, never fail the probe) ───────────
    tile_block: Dict[str, Any]
    try:
        from routes import _tile_cache as _tc
        s = _tc.stats_sync()
        if "error" in s:
            # stats_sync() embeds str(e) + the DB path in its error dict —
            # don't forward those to unauthenticated callers (CodeQL
            # py/stack-trace-exposure). Numeric coercion below likewise
            # guarantees no string from that dict reaches the response.
            tile_block = {"error": "tile_cache_stats_unavailable"}
        else:
            tile_block = {
                "rows": int(s.get("rows", 0) or 0),
                "bytes_on_disk": int(s.get("bytes_on_disk", 0) or 0),
                "hit_rate": float(s.get("hit_rate", 0.0) or 0.0),
                "hits": int(s.get("hits", 0) or 0),
                "misses": int(s.get("misses", 0) or 0),
            }
    except Exception as e:
        logger.warning("readiness probe tile-cache stats failed: %s", e)
        tile_block = {"error": "tile_cache_stats_unavailable"}

    # ── Solver availability (best-effort, never fail the probe) ─────────
    solvers_block: Dict[str, Any]
    try:
        import server as _srv  # noqa: WPS433
        solvers_block = {
            "vroom": bool(_srv.VROOM_AVAILABLE),
            "lkh": bool(_srv.LKH_AVAILABLE),
            "elkai": bool(_srv.ELKAI_AVAILABLE),
            "ortools": bool(_srv.ORTOOLS_AVAILABLE),
            "pyvrp": bool(_srv.PYVRP_AVAILABLE),
            "alns": bool(_srv.ALNS_AVAILABLE),
            "timefold": bool(_srv.TIMEFOLD_AVAILABLE),
        }
    except Exception as e:
        logger.warning("readiness probe solver flags failed: %s", e)
        solvers_block = {"error": "solver_flags_unavailable"}

    now = datetime.now(timezone.utc)
    uptime = (now - _PROCESS_STARTED_AT).total_seconds()
    status = "ok" if mongo_ok else "degraded"

    # 503 on degraded so K8s readiness probe pulls the pod from rotation.
    if not mongo_ok:
        response.status_code = 503

    return {
        "status": status,
        "timestamp": now.isoformat(),
        "build": {
            "sha": _BUILD_SHA,
            "started_at": _PROCESS_STARTED_AT.isoformat(),
            "uptime_sec": round(uptime, 1),
        },
        "mongo": mongo_block,
        "tile_cache": tile_block,
        "solvers": solvers_block,
    }


@router.get("/")
async def api_root():
    return {"message": "Circuit Route Optimizer API", "status": "healthy"}


@router.get("/healthz/version")
async def readiness_version():
    """Lightweight version-only sub-path for load balancers / monitoring
    (Datadog, Grafana, uptime pings) that just want to spot a deploy rollover
    cheaply. Skips the mongo ping + tile-cache stats — no I/O at all, just
    the two module-level constants captured once at import. ~10 µs to serve.
    Always returns 200; if you need degraded-pod ejection, poll /api/healthz
    instead."""
    return {
        "sha": _BUILD_SHA,
        "started_at": _PROCESS_STARTED_AT.isoformat(),
    }
