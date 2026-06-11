"""Async optimize job store and endpoints split from optimize.py.

    POST /optimize/jobs               → async kickoff (Mongo-backed job doc)
    GET  /optimize/jobs/{job_id}      → poll job status/result
    GET  /optimize/diagnostics        → last 10 jobs + solver availability

Why this exists: Cloudflare's edge proxy enforces a hard 100 s ceiling on
origin response time (HTTP 524). On a 200-stop manifest with active
No-Go zones, the synchronous `/api/optimize` endpoint can take 90-150 s
end-to-end (OSRM matrix + nogo OSRM-aware probe + PyVRP solve + 2-opt
tightener + final OSRM directions). The 524 fires before we can reply.

Fix: a fire-and-poll job pattern. The client POSTs to
`/api/optimize/jobs`, gets a 202 + `job_id` in <100 ms, then polls
`/api/optimize/jobs/{job_id}` every ~2 s until `status` flips from
`running` → `done` (or `error`). Each poll is well under the 100 s cap,
so Cloudflare can never time us out — we own the wall-clock budget.

IMPORTANT — Mongo-backed (NOT in-memory):
  Production runs multiple replicas behind the K8s ingress. An in-memory
  dict would silently fail when POST hits pod A and the subsequent
  GET poll hits pod B (job_id missing → "Job not found or expired").
  We persist the job in Mongo so any pod can serve any poll. A TTL
  index on `expires_at` auto-purges 10 min after creation; no GC code
  path needed in Python.

Cross-pod result delivery: the runner that owns the optimize task is
the *same* pod that handled the POST (asyncio.create_task is local).
That pod writes `status:"done"` + `result` to Mongo on completion.
Any poll hitting any pod reads from Mongo. If the owning pod crashes
mid-solve, the job stays in `running` until TTL — the driver's poll
loop times out client-side after 5 min and they retap Optimise.
"""
from __future__ import annotations

import asyncio as _asyncio_jobs
import logging
import traceback
import uuid
from datetime import datetime as _dt_jobs, timedelta as _td_jobs, timezone as _tz_jobs
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from models import OptimizationRequest, User
from routes.billing import require_pro as _billing_require_pro

logger = logging.getLogger("server")
router = APIRouter()

_OPTIMIZE_JOB_TTL_S = 600          # 10 min — driver retries within this

# Strong reference set for all in-flight runner tasks. asyncio's docs are
# explicit: "the event loop only keeps weak references to tasks" — so a
# bare `create_task(...)` can be silently cancelled by GC under memory
# pressure (which a 200-stop optimize generates lots of). The classic
# symptom is exactly what we hit on prod: kickoff returns 202, the
# runner starts, then disappears mid-solve, and the frontend polls
# `status: "running"` forever until its 5-minute ceiling. Holding a hard
# reference here keeps the task alive; the done-callback removes it
# once the runner finishes (success OR failure).
_OPTIMIZE_RUNNER_TASKS: set = set()


async def _current_user(request: Request):
    from server import get_current_user  # noqa: WPS433
    return await get_current_user(request)


async def _ensure_optimize_jobs_indexes() -> None:
    """Create the TTL index on `optimize_jobs.expires_at` once at startup.

    Mongo's TTL monitor sweeps roughly every minute; documents are deleted
    when `expires_at < now`. Called from the existing startup hook so the
    kickoff hot path is pure-insert (no schema work on the request flow,
    no lazy-init lock contention across pods that just spun up after a
    rolling deploy)."""
    from server import db  # noqa: WPS433
    try:
        await db.optimize_jobs.create_index("expires_at", expireAfterSeconds=0)
        await db.optimize_jobs.create_index("job_id", unique=True)
        logger.info("optimize_jobs indexes verified")
    except Exception as e:  # noqa: BLE001
        # Non-fatal: if the index already exists with a slightly different
        # spec, motor raises here. The collection still works without the
        # index; we'd just lose TTL auto-purge.
        logger.warning("optimize_jobs index create failed (non-fatal): %s", e)


async def _run_optimize_job(job_id: str, request: OptimizationRequest, current_user: User) -> None:
    """Background runner. Writes the resolved JSONResponse-equivalent dict
    (or an error description) into Mongo `optimize_jobs.{job_id}`.

    The wrapped `_optimize_route_inner` returns either a plain dict or a
    JSONResponse — we coerce both to a serialisable dict so the polling
    endpoint can return it directly without re-running anything."""
    from routes.optimize import _optimize_route_inner  # noqa: WPS433 — late bind avoids circular
    from server import db  # noqa: WPS433
    try:
        result = await _optimize_route_inner(request=request, current_user=current_user)
        if hasattr(result, "body"):
            try:
                import json as _json_resp
                payload = _json_resp.loads(result.body.decode("utf-8"))
            except Exception as e:
                payload = {"_raw_response_decode_error": type(e).__name__}
        else:
            payload = result
        await db.optimize_jobs.update_one(
            {"job_id": job_id},
            {"$set": {"status": "done", "result": payload,
                      "finished_at": _dt_jobs.now(_tz_jobs.utc)}},
        )
    except HTTPException as he:
        logger.error("[optimize/jobs] HTTPException job=%s status=%d:\n%s",
                     job_id, he.status_code, traceback.format_exc())
        await db.optimize_jobs.update_one(
            {"job_id": job_id},
            {"$set": {"status": "error",
                      "error": {"status": he.status_code, "detail": str(he.detail)},
                      "finished_at": _dt_jobs.now(_tz_jobs.utc)}},
        )
    except Exception as e:  # noqa: BLE001
        logger.error("[optimize/jobs] Unhandled crash job=%s:\n%s",
                     job_id, traceback.format_exc())
        await db.optimize_jobs.update_one(
            {"job_id": job_id},
            {"$set": {"status": "error",
                      "error": {"status": 500, "detail": f"Optimize crashed ({type(e).__name__})"},
                      "finished_at": _dt_jobs.now(_tz_jobs.utc)}},
        )


@router.post("/optimize/jobs", status_code=202)
async def create_optimize_job(
    request: OptimizationRequest = OptimizationRequest(),
    current_user: User = Depends(_current_user),
    _pro=Depends(_billing_require_pro),
):
    """Kick off an optimize run in the background and return a job_id.

    Gated behind the Pro paywall (`require_pro`) because this endpoint
    runs the heavy multi-engine optimizer (VROOM, LKH-3, OR-Tools,
    etc.). Free users get a 402 Payment Required with an
    `upgrade_required: true` detail; the RN client surfaces the
    paywall sheet from that signal. Admins (STRIPE_ADMIN_USER_IDS env
    var) bypass the check.

    The driver's client (RN app) polls `/api/optimize/jobs/{job_id}` until
    `status` is `done` (then reads `result`) or `error` (then reads
    `error.detail`). This shape is independent of how slow the underlying
    pipeline is — Cloudflare's 100 s ceiling can't bite us here because
    *this* endpoint always replies in <100 ms (pure Mongo insert; the
    TTL+unique indexes are created at app startup, NOT on the hot path).
    """
    from server import db  # noqa: WPS433
    try:
        job_id = str(uuid.uuid4())
        now = _dt_jobs.now(_tz_jobs.utc)
        await db.optimize_jobs.insert_one({
            "job_id": job_id,
            "user_id": current_user.user_id,
            "status": "running",
            "started_at": now,
            "expires_at": now + _td_jobs(seconds=_OPTIMIZE_JOB_TTL_S),
            "result": None,
            "error": None,
        })
        logger.info("[optimize/jobs] kickoff job_id=%s user=%s", job_id, current_user.user_id)
        # Fire-and-forget — runner writes back to the same Mongo document.
        # CRITICAL: hold a strong reference until the runner completes; without
        # this, GC under memory pressure can silently cancel the task and the
        # frontend will poll `status: "running"` forever.
        task = _asyncio_jobs.create_task(_run_optimize_job(job_id, request, current_user))
        _OPTIMIZE_RUNNER_TASKS.add(task)
        task.add_done_callback(_OPTIMIZE_RUNNER_TASKS.discard)
        return {"job_id": job_id, "status": "running"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[optimize/jobs] Kickoff crashed for user=%s:\n%s",
                     current_user.user_id, traceback.format_exc())
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": f"Optimization kickoff failed ({type(e).__name__})"},
        )


@router.get("/optimize/jobs/{job_id}")
async def get_optimize_job(
    job_id: str,
    current_user: User = Depends(_current_user),
):
    """Poll an optimize job. Returns `{status, result?, error?}`.

    Scoped to the calling user — forging another driver's job_id yields
    404 (same shape as a TTL'd-out job, so we don't leak existence).

    Bandwidth shape: while `status == "running"` we project the (potentially
    multi-megabyte) `result` field away so each poll is a tiny round-trip
    that finishes well inside the client's per-poll AbortController. Once
    the runner writes status=done, the full payload is shipped on the
    next poll — that single response can be 2-5 MB on a 200-stop manifest
    so the client uses a longer timeout for it (see frontend POLL_TIMEOUT_MS).
    """
    from server import db  # noqa: WPS433
    # First peek: status only. ~100 bytes over the wire even on a hot Atlas.
    head = await db.optimize_jobs.find_one(
        {"job_id": job_id, "user_id": current_user.user_id},
        {"_id": 0, "status": 1},
    )
    if head is None:
        raise HTTPException(status_code=404, detail="Job not found or expired")
    status = head.get("status", "running")
    if status == "running":
        return {"job_id": job_id, "status": "running", "result": None, "error": None}
    # Job has terminated — fetch the full doc (with result/error) for one
    # final response. After this the client stops polling.
    j = await db.optimize_jobs.find_one(
        {"job_id": job_id, "user_id": current_user.user_id},
        {"_id": 0},
    )
    if j is None:  # raced with TTL expiry between the two reads
        raise HTTPException(status_code=404, detail="Job not found or expired")
    return {
        "job_id": job_id,
        "status": j.get("status", status),
        "result": j.get("result") if j.get("status") == "done" else None,
        "error": j.get("error") if j.get("status") == "error" else None,
    }


@router.get("/optimize/diagnostics")
async def optimize_diagnostics(
    current_user: User = Depends(_current_user),
):
    """Return the last 10 optimize jobs for the calling user.

    Diagnostic endpoint for debugging "optimization keeps failing" reports.
    Shows job_id, status, algorithm, timing, error detail, and stop count
    without returning the full (multi-MB) result payload. Accessible from
    the app or a quick curl from the driver's phone browser.
    """
    from server import (  # noqa: WPS433
        LKH_AVAILABLE,
        ORTOOLS_AVAILABLE,
        OSRM_URL,
        PYVRP_AVAILABLE,
        VROOM_AVAILABLE,
        _osrm_enabled,
        db,
    )
    cursor = db.optimize_jobs.find(
        {"user_id": current_user.user_id},
        {
            "_id": 0,
            "job_id": 1,
            "status": 1,
            "started_at": 1,
            "finished_at": 1,
            "error": 1,
            # Lightweight result summary — NOT the full stops array.
            "result.algorithm": 1,
            "result.stop_count": 1,
            "result.total_distance_km": 1,
            "result.reasoning": 1,
        },
    ).sort("started_at", -1).limit(10)
    jobs = await cursor.to_list(length=10)

    for j in jobs:
        # Add elapsed_seconds for quick triage
        if j.get("started_at") and j.get("finished_at"):
            try:
                elapsed = (j["finished_at"] - j["started_at"]).total_seconds()
                j["elapsed_seconds"] = round(elapsed, 1)
            except Exception:
                pass
        # Flatten result summary
        if j.get("result"):
            j["result_summary"] = {
                "algorithm": j["result"].get("algorithm"),
                "stop_count": j["result"].get("stop_count"),
                "total_distance_km": j["result"].get("total_distance_km"),
                "reasoning": (j["result"].get("reasoning") or "")[:120],
            }
            del j["result"]
        # Convert datetimes to ISO strings for JSON
        for k in ("started_at", "finished_at"):
            if j.get(k):
                try:
                    j[k] = j[k].isoformat()
                except Exception:
                    pass

    # Also include stop count + OSRM status for context
    stop_count = await db.stops.count_documents({
        "user_id": current_user.user_id, "completed": False,
    })

    return {
        "user_id": current_user.user_id,
        "pending_stops": stop_count,
        "osrm_url": OSRM_URL,
        "osrm_enabled": _osrm_enabled(),
        "vroom_available": VROOM_AVAILABLE,
        "pyvrp_available": PYVRP_AVAILABLE,
        "ortools_available": ORTOOLS_AVAILABLE,
        "lkh_available": LKH_AVAILABLE,
        "recent_jobs": jobs,
    }
