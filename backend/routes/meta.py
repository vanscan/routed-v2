"""Meta / diagnostics / analytics endpoints.

    GET  /_meta/build                      → build & runtime info (public)
    GET  /_meta/telemetry-rollup           → no-PII rollup of caller's telemetry
    POST /_meta/ml/train                    → retrain service-time learner
    GET  /_meta/ml/model                    → service-time model summary
    POST /_meta/ml/building-side/train      → retrain building-side corrector
    GET  /_meta/ml/building-side/model      → building-side model summary

Moved verbatim from server.py. `db`/`app` are lazy-imported from `server`
inside the handlers; the ML logic stays lazily imported from `ml.*`.

NOTE on the auth dependency: the authed endpoints depend on
`server.get_current_user` *directly* (not a local wrapper) because
`tests/test_building_side_endpoints.py` overrides it via
`app.dependency_overrides[server.get_current_user]`; the dependency callable
must be that exact object. `get_current_user` is defined early in server.py,
well before this module loads at the include-router block, so the module-level
import is safe. `/_meta/build` is deliberately public (no auth).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from server import get_current_user  # noqa: E402 — defined before this module loads

logger = logging.getLogger("server")
router = APIRouter()

# Captured when this module is imported at server startup (the include-router
# block runs during app construction), matching the previous module-load
# semantics of server.py's _BUILD_STARTED_AT.
_BUILD_STARTED_AT = time.time()


@router.get("/_meta/build")
async def meta_build():
    """Return build / runtime info so the operator can verify what's
    actually deployed without SSH-ing into a pod. Fields chosen to map
    1:1 onto the questions we've been guessing at:

    * `started_at_iso` / `uptime_s` — was this pod just spun up?
    * `has_optimize_jobs_endpoint` — is the async-job pattern live?
    * `optimize_jobs_index_ok` / `optimize_jobs_count` — Mongo healthy?
    * `git_sha` — does prod match what's checked in (best-effort).
    """
    from server import app, db  # noqa: WPS433

    git_sha = "unknown"
    try:
        import subprocess
        git_sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd="/app", stderr=subprocess.DEVNULL,
        ).decode().strip() or "unknown"
    except Exception:  # noqa: BLE001
        pass

    optimize_jobs_count = -1
    optimize_jobs_index_ok = False
    try:
        optimize_jobs_count = await db.optimize_jobs.count_documents({})
        idx_info = await db.optimize_jobs.index_information()
        optimize_jobs_index_ok = any(
            "expires_at" in str(spec.get("key", []))
            for spec in idx_info.values()
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("meta_build: optimize_jobs probe failed: %s", e)

    has_jobs_endpoint = any(
        getattr(r, "path", None) == "/api/optimize/jobs"
        for r in app.routes
    )

    return {
        "started_at_iso": datetime.fromtimestamp(_BUILD_STARTED_AT, timezone.utc).isoformat(),
        "uptime_s": int(time.time() - _BUILD_STARTED_AT),
        "git_sha": git_sha,
        "has_optimize_jobs_endpoint": has_jobs_endpoint,
        "optimize_jobs_index_ok": optimize_jobs_index_ok,
        "optimize_jobs_count": optimize_jobs_count,
        "now_utc": datetime.now(timezone.utc).isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────
# Per-user telemetry rollup — debugging surface for production
# ─────────────────────────────────────────────────────────────────────
# Why this exists:
#   The agent debugging this codebase lives in a preview pod that
#   cannot connect to production's Mongo Atlas. When the user asks
#   "which algorithm did I use today?" or "is the geofence actually
#   firing?", we have no way to answer except by guessing from logs.
#
#   This endpoint exposes aggregate, no-PII rollups computed over the
#   caller's OWN route_history. The user can curl it (or surface it
#   in-app) and paste output back to the agent for diagnosis.
#
# Privacy posture:
#   * Auth-gated (caller's user_id is the ONLY filter on the query).
#   * Returns counts, percentiles, and the algorithm string — NEVER
#     addresses, lat/lng, names, or raw stop bodies.
#   * Forging another user's user_id is structurally impossible: the
#     filter is hard-bound to `current_user.user_id`.

def _today_utc_iso() -> str:
    """Start of the current UTC day in ISO format. Routes archived since
    this moment count as 'today'. We use UTC (not local time) because
    that's what `archived_at` is stored in."""
    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.isoformat()


def _seven_days_ago_iso() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()


def _percentile(sorted_arr: List[float], q: float) -> Optional[float]:
    """Cheap nearest-rank percentile. `sorted_arr` MUST already be sorted."""
    if not sorted_arr:
        return None
    idx = min(len(sorted_arr) - 1, int(q * (len(sorted_arr) - 1)))
    return round(sorted_arr[idx], 1)


def _aggregate_rollup(route_docs: List[dict]) -> Dict[str, Any]:
    """Compute the rollup shape from a list of archived route docs.

    Each `route_docs` entry has `summary.telemetry` (per-route rollup
    computed at archive time) plus optional `summary.algorithm` (which
    we started persisting alongside this endpoint — older archives
    will report `None`).
    """
    if not route_docs:
        return {
            "archived_routes": 0,
            "best_route": None,
            "geofence_count": 0,
            "geofence_inferred_count": 0,
            "fallback_count": 0,
            "geofence_rate": None,
            "arrival_proximity_rate": None,
            "completion_distance_p50_m": None,
            "completion_distance_p95_m": None,
            "service_seconds_p50": None,
            "service_seconds_p95": None,
            "distance_samples": 0,
            "service_samples": 0,
        }

    # Roll up per-stop telemetry across all archived routes in the window.
    geofence_n = 0
    inferred_n = 0
    fallback_n = 0
    distances: List[float] = []
    service_seconds: List[float] = []

    for route in route_docs:
        for s in route.get("stops") or []:
            method = s.get("arrival_method")
            if method == "geofence":
                geofence_n += 1
            elif method == "geofence_inferred":
                inferred_n += 1
            elif method == "fallback_completion":
                fallback_n += 1
            cd = s.get("completion_distance_m")
            if isinstance(cd, (int, float)):
                distances.append(float(cd))
            # Real service-time sample: geofence-arrival + completion both
            # timed. `geofence_inferred` has a constant 30s back-date, so
            # it's deliberately excluded to keep the ML distribution clean.
            if method == "geofence":
                a, c = s.get("arrived_at"), s.get("completed_at")
                if a and c:
                    try:
                        if isinstance(a, str):
                            a = datetime.fromisoformat(a.replace("Z", "+00:00"))
                        if isinstance(c, str):
                            c = datetime.fromisoformat(c.replace("Z", "+00:00"))
                        service_seconds.append((c - a).total_seconds())
                    except Exception:
                        pass

    distances.sort()
    service_seconds.sort()

    # "Best route" in the window = lowest total_distance_km among routes
    # that actually have a distance recorded. If none, falls back to the
    # route with the most delivered stops (a coarser proxy for "good day").
    best = None
    for r in route_docs:
        summary = r.get("summary") or {}
        algo = summary.get("algorithm")  # may be None for older archives
        # Heuristic: many archives carry a `stats.total_distance_km` on
        # the route doc itself, but we wrote it only into the optimise
        # response, not the archive. Use delivered count as the
        # comparable signal for now.
        delivered = (summary.get("delivered") or 0)
        candidate = {
            "archived_at": r.get("archived_at"),
            "algorithm": algo,
            "stops": summary.get("total_stops"),
            "delivered": delivered,
            "skipped": summary.get("skipped"),
            "failed": summary.get("failed"),
            "total_weight_kg": summary.get("total_weight_kg"),
        }
        if best is None or delivered > (best.get("delivered") or 0):
            best = candidate

    return {
        "archived_routes": len(route_docs),
        "best_route": best,
        "geofence_count": geofence_n,
        "geofence_inferred_count": inferred_n,
        "fallback_count": fallback_n,
        "geofence_rate": (
            round(geofence_n / (geofence_n + inferred_n + fallback_n), 3)
            if (geofence_n + inferred_n + fallback_n) > 0
            else None
        ),
        "arrival_proximity_rate": (
            round((geofence_n + inferred_n) / (geofence_n + inferred_n + fallback_n), 3)
            if (geofence_n + inferred_n + fallback_n) > 0
            else None
        ),
        "completion_distance_p50_m": _percentile(distances, 0.5),
        "completion_distance_p95_m": _percentile(distances, 0.95),
        "service_seconds_p50": _percentile(service_seconds, 0.5),
        "service_seconds_p95": _percentile(service_seconds, 0.95),
        "distance_samples": len(distances),
        "service_samples": len(service_seconds),
    }


@router.get("/_meta/telemetry-rollup")
async def meta_telemetry_rollup(current_user=Depends(get_current_user)):
    """Aggregate, no-PII rollup of the caller's archived route telemetry.

    Two windows:
      * `today`        — archives since 00:00 UTC of the current day
      * `last_7_days`  — archives in the trailing 7-day window
    Plus an `ml_readiness` block summarising whether the Phase-1
    service-time learner has enough clean samples to train.

    Each window contains:
      archived_routes, best_route { algorithm, stops, delivered, ... },
      geofence_count, fallback_count, geofence_rate,
      completion_distance_p50_m, completion_distance_p95_m,
      service_seconds_p50, service_seconds_p95,
      distance_samples, service_samples
    """
    from server import db  # noqa: WPS433

    today_iso = _today_utc_iso()
    week_iso = _seven_days_ago_iso()

    # One query, broadest window — slice client-side for "today".
    cursor = db.route_history.find(
        {
            "user_id": current_user.user_id,
            "archived_at": {"$gte": week_iso},
        },
        {"_id": 0},
    ).sort("archived_at", -1)
    week_docs = await cursor.to_list(500)
    today_docs = [d for d in week_docs if (d.get("archived_at") or "") >= today_iso]

    today_rollup = _aggregate_rollup(today_docs)
    week_rollup = _aggregate_rollup(week_docs)

    # ML readiness: Phase-1 service-time learner needs ≥50 real
    # (geofence-arrival, geofence-completion) samples spread across
    # ≥10 distinct shifts to train without overfitting one day's
    # parking habits.
    PHASE_1_THRESHOLD = 50
    real_samples = week_rollup["service_samples"]
    blocked_on = None
    if real_samples < PHASE_1_THRESHOLD:
        inferred = week_rollup.get("geofence_inferred_count", 0)
        if week_rollup["geofence_count"] == 0 and week_rollup["fallback_count"] > 0 and inferred == 0:
            blocked_on = (
                "geofence not firing — every completion in the last 7 days "
                "used arrival_method='fallback_completion' and no proximity "
                "inferences either. Likely cause: viewMode is 'planning' "
                "(not 'navigating') when the user taps Delivered, so "
                "useGeofenceArrival.ts never gates open."
            )
        elif week_rollup["geofence_count"] == 0 and inferred > 0:
            blocked_on = (
                f"geofence hook still not firing ({inferred} 'geofence_inferred' "
                f"samples in the last 7 days back-stamped via the 150 m proximity "
                f"backstop). Real service-time samples need an actual hook fire — "
                f"check useGeofenceArrival.ts radius vs. completion_distance_p50_m."
            )
        elif week_rollup["geofence_count"] == 0:
            blocked_on = (
                "no completions recorded yet — drive a route and tap "
                "Delivered to start collecting samples."
            )
        else:
            blocked_on = (
                f"insufficient samples ({real_samples}/{PHASE_1_THRESHOLD}) "
                "— keep driving; learner will unlock automatically."
            )

    return {
        "user_id": current_user.user_id,
        "today_utc_start": today_iso,
        "today": today_rollup,
        "last_7_days": week_rollup,
        "ml_readiness": {
            "real_geofence_samples_last_7d": real_samples,
            "needed_for_phase_1": PHASE_1_THRESHOLD,
            "ready_to_train": real_samples >= PHASE_1_THRESHOLD,
            "blocked_on": blocked_on,
        },
    }


# ─────────────────────────────────────────────────────────────────────────
# Phase 1 ML — Service-Time Learner
# ─────────────────────────────────────────────────────────────────────────
#
# Pulls every archived route's `arrival_method='geofence'` samples for
# this user, computes bucketed-median service times by (suburb, hour).


@router.post("/_meta/ml/train")
async def train_ml_service_time(current_user=Depends(get_current_user)):
    """Re-train the service-time learner from this user's archived routes.

    Returns the trained model summary + the count of buckets that
    survived the BUCKET_MIN_SAMPLES filter. The driver can refresh the
    Profile telemetry tile to see the model is now active.
    """
    from server import db  # noqa: WPS433
    from ml.service_time_learner import (
        collect_samples_from_archive,
        build_model_from_samples,
        summarize_model,
        BUCKET_MIN_SAMPLES,
        DEFAULT_SECONDS,
    )

    # Pull every archived route for this user (no time window — we
    # want the broadest sample set). _id excluded so the response can
    # be returned directly.
    routes: List[dict] = []
    cursor = db.route_history.find(
        {"user_id": current_user.user_id},
        {"_id": 0, "stops": 1, "archived_at": 1},
    )
    async for r in cursor:
        routes.append(r)

    samples = collect_samples_from_archive(routes)
    if len(samples) == 0:
        raise HTTPException(
            status_code=400,
            detail=(
                "No real geofence samples found. Drive a route, tap "
                "Save Route, then train. Geofence_inferred / "
                "fallback_completion are not used (they have constant "
                "back-dated arrival times)."
            ),
        )

    model = build_model_from_samples(samples)

    # Persist. One doc per user_id, replaced on every retrain so the
    # collection never grows beyond N(users).
    await db.ml_service_time_models.replace_one(
        {"user_id": current_user.user_id},
        {"user_id": current_user.user_id, **model},
        upsert=True,
    )

    summary = summarize_model(model)
    logger.info(
        "[ml] Trained service-time model for user=%s: %d samples → "
        "%d suburbs, global_median=%.1fs, fastest=%s, slowest=%s",
        current_user.user_id,
        summary["sample_count"],
        summary["suburbs_covered"],
        summary["global_median_seconds"],
        summary["fastest_bucket_seconds"],
        summary["slowest_bucket_seconds"],
    )

    return {
        "ok": True,
        "trained_at": model["trained_at"],
        "sample_count": model["sample_count"],
        "bucket_count": len(model.get("buckets") or {}),
        "bucket_min_samples": BUCKET_MIN_SAMPLES,
        "default_seconds": DEFAULT_SECONDS,
        "summary": summary,
    }


@router.get("/_meta/ml/model")
async def get_ml_service_time_model(current_user=Depends(get_current_user)):
    """Return the current model summary (driver-friendly) for the
    Profile tile. If no model has been trained, returns trained=false
    so the UI can show "Train now" instead of metrics."""
    from server import db  # noqa: WPS433
    from ml.service_time_learner import summarize_model

    doc = await db.ml_service_time_models.find_one(
        {"user_id": current_user.user_id},
        {"_id": 0},
    )
    return {
        "user_id": current_user.user_id,
        "model": summarize_model(doc),
    }


# ─────────────────────────────────────────────────────────────────────────
# Phase 2 ML — Building-Side Corrector
# ─────────────────────────────────────────────────────────────────────────
#
# Mapbox centroids land on the rooftop, but drivers park at the kerb. We
# observe that offset every time a driver taps Delivered with GPS on. The
# per-suburb median (Δlat, Δlng) is the predicted real arrival point for
# every new stop in that suburb, even when Mapbox didn't supply an
# `access_navigation_point`.
#
# Same per-user model pattern as Phase 1: one doc per user, replaced on
# every retrain. Source rows accept both `geofence` AND `geofence_inferred`
# arrival_method values (both supply real completion GPS); fallback_completion
# is excluded.


@router.post("/_meta/ml/building-side/train")
async def train_ml_building_side(current_user=Depends(get_current_user)):
    """Re-train the building-side corrector from this user's archived
    routes. Returns the trained model summary + the count of suburbs that
    survived the BUCKET_MIN_SAMPLES filter."""
    from server import db  # noqa: WPS433
    from ml.building_side_corrector import (
        collect_samples_from_archive,
        build_model_from_samples,
        summarize_model,
        BUCKET_MIN_SAMPLES,
        OUTLIER_MAX_METRES,
    )

    routes: List[dict] = []
    cursor = db.route_history.find(
        {"user_id": current_user.user_id},
        {"_id": 0, "stops": 1, "archived_at": 1},
    )
    async for r in cursor:
        routes.append(r)

    samples = collect_samples_from_archive(routes)
    if len(samples) == 0:
        raise HTTPException(
            status_code=400,
            detail=(
                "No samples with completion GPS found. Drive a route with "
                "location services enabled, tap Save Route, then train. "
                "Only geofence/geofence_inferred stops contribute — "
                "fallback_completion rows have no GPS."
            ),
        )

    model = build_model_from_samples(samples)

    await db.ml_building_side_models.replace_one(
        {"user_id": current_user.user_id},
        {"user_id": current_user.user_id, **model},
        upsert=True,
    )

    summary = summarize_model(model)
    logger.info(
        "[ml/building-side] Trained for user=%s: %d samples → %d suburbs, "
        "global_offset=%.1fm, largest_suburb_offset=%sm",
        current_user.user_id,
        summary["sample_count"],
        summary["suburbs_covered"],
        summary["global_offset_metres"],
        summary["largest_suburb_offset_metres"],
    )

    return {
        "ok": True,
        "trained_at": model["trained_at"],
        "sample_count": model["sample_count"],
        "suburb_count": len(model.get("suburbs") or {}),
        "bucket_min_samples": BUCKET_MIN_SAMPLES,
        "outlier_max_metres": OUTLIER_MAX_METRES,
        "summary": summary,
    }


@router.get("/_meta/ml/building-side/model")
async def get_ml_building_side_model(current_user=Depends(get_current_user)):
    """Return the current building-side correction model summary."""
    from server import db  # noqa: WPS433
    from ml.building_side_corrector import summarize_model

    doc = await db.ml_building_side_models.find_one(
        {"user_id": current_user.user_id},
        {"_id": 0},
    )
    return {
        "user_id": current_user.user_id,
        "model": summarize_model(doc),
    }
