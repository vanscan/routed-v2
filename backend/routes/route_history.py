"""Route History, Learning, and Telepathy endpoints.

Endpoints moved here from server.py (lines 946–1622):

    POST /routes/archive               → snapshot active stops into history
    GET  /routes/history               → list archived routes (summary only)
    GET  /routes/history/{id}          → full route detail
    DELETE /routes/history/{id}        → delete a route from history
    GET  /routes/history/{id}/export.gpx  → GPX export
    GET  /routes/history/{id}/export.kml  → KML export
    POST /routes/history/{id}/resume   → restore archived route as active stops
    GET  /routes/stats                 → lifetime aggregate stats

    GET  /learn/sequence-stats         → learned stop-order preferences
    POST /learn/sequence-reset         → wipe sequence preferences
    GET  /learn/road-stats             → learned road-segment preferences
    POST /learn/road-reset             → wipe road preferences
    POST /route/preferred-polyline     → Route Telepathy: score OSRM alternatives by familiarity

All dependencies on `db`, `get_current_user`, `TELEPATHY_USER_IDS`, `OSRM_URL`
are lazy-imported from `server` inside each endpoint — same deferred-import
pattern as other route modules — so this file loads cleanly before `server.py`
finishes initialising.
"""
from __future__ import annotations

import asyncio
import logging
import math
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

logger = logging.getLogger("server")
router = APIRouter()


async def _current_user(request: Request):
    """Dep wrapper — defers the `server` import until the first request."""
    from server import get_current_user  # noqa: WPS433
    return await get_current_user(request)


# ── Route geometry normaliser (Python port of src/utils/routeGeometry.ts) ───
# Same contract as the frontend utility: validate shape, auto-flip [lat, lng]
# pairs if detected, strip non-finite + out-of-range values, dedupe coincident
# vertices. Used by the GPX / KML exporters so downstream GPS tooling never
# receives a malformed polyline.

def _looks_like_latlng_swap(coords: List[List[float]]) -> bool:
    sample = coords[:8]
    first_inside_lat = 0
    second_outside_lat = 0
    for p in sample:
        if not isinstance(p, (list, tuple)) or len(p) < 2:
            continue
        a, b = p[0], p[1]
        if not (isinstance(a, (int, float)) and isinstance(b, (int, float))):
            continue
        if abs(a) <= 90:
            first_inside_lat += 1
        if 90 < abs(b) <= 180:
            second_outside_lat += 1
    return first_inside_lat == len(sample) and second_outside_lat >= 1


def normalise_line_coordinates(
    coords: Optional[List[List[float]]], auto_flip: bool = True
) -> List[List[float]]:
    if not coords:
        return []
    should_flip = auto_flip and _looks_like_latlng_swap(coords)
    out: List[List[float]] = []
    prev: Optional[List[float]] = None
    for raw in coords:
        if not isinstance(raw, (list, tuple)) or len(raw) < 2:
            continue
        lng = raw[1] if should_flip else raw[0]
        lat = raw[0] if should_flip else raw[1]
        try:
            lng = float(lng)
            lat = float(lat)
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(lng) and math.isfinite(lat)):
            continue
        if lng < -180 or lng > 180 or lat < -90 or lat > 90:
            continue
        if prev and prev[0] == lng and prev[1] == lat:
            continue
        out.append([lng, lat])
        prev = [lng, lat]
    return out


def _escape_xml(s: Any) -> str:
    if s is None:
        return ""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


# ── Route History endpoints ──────────────────────────────────────────────────

@router.post("/routes/archive")
async def archive_route(
    request: Request,
    current_user=Depends(_current_user),
):
    """Snapshot current stops into route_history before clearing them.

    Optional JSON body:
      { "algorithm": "vroom_lkh_3opt", "total_distance_km": 187.3,
        "total_duration_seconds": 24720 }
    Any provided fields are persisted under `summary.*` so the
    `/api/_meta/telemetry-rollup` endpoint can answer "which algorithm
    did I use today?" without us having to wire algorithm into every
    intermediate state. All fields are optional and backwards-
    compatible — older clients posting no body still archive cleanly.
    """
    from server import db, TELEPATHY_USER_IDS  # noqa: WPS433

    # Parse the optional body. We use Request directly (not a Pydantic
    # model) so the endpoint stays backwards-compatible with clients
    # that POST with no body / no Content-Type header.
    optional_body: Dict[str, Any] = {}
    try:
        if request.headers.get("content-length") and int(request.headers["content-length"]) > 0:
            optional_body = await request.json()
            if not isinstance(optional_body, dict):
                optional_body = {}
    except Exception:
        optional_body = {}

    all_stops = await db.stops.find(
        {"user_id": current_user.user_id}, {"_id": 0}
    ).sort("order", 1).to_list(5000)

    if not all_stops:
        return {"archived": False, "message": "No stops to archive"}

    delivered = [s for s in all_stops if s.get("completed")]
    skipped = [s for s in all_stops if s.get("delivery_status") == "skipped"]
    failed = [s for s in all_stops if s.get("delivery_status") == "failed"]
    pending = [s for s in all_stops if not s.get("completed") and s.get("delivery_status", "pending") == "pending"]

    total_weight = sum(s.get("weight") or 0 for s in all_stops)
    total_quantity = sum(s.get("quantity") or 0 for s in all_stops)

    # ── Telemetry rollup: surface the geofence-vs-fallback ratio + distance
    # percentiles so we can answer "is the 100 m geofence radius the right
    # fit for this driver's parking habits?" without instrumenting on the
    # device. This data populates the Phase-1 ML readiness check.
    geofence_n = sum(1 for s in delivered if s.get("arrival_method") == "geofence")
    inferred_n = sum(1 for s in delivered if s.get("arrival_method") == "geofence_inferred")
    fallback_n = sum(1 for s in delivered if s.get("arrival_method") == "fallback_completion")
    distances = [
        s["completion_distance_m"] for s in delivered
        if isinstance(s.get("completion_distance_m"), (int, float))
    ]
    distances.sort()

    def _pct(arr, q):
        if not arr:
            return None
        idx = min(len(arr) - 1, int(q * (len(arr) - 1)))
        return round(arr[idx], 1)

    # Service-seconds samples MUST come from real geofence arrivals only.
    # `geofence_inferred` rows back-date arrived_at by a constant 30s, so
    # including them would pollute the service-time distribution with a
    # degenerate p50/p95 of 30.
    service_seconds = []
    for s in delivered:
        if s.get("arrival_method") != "geofence":
            continue
        a, c = s.get("arrived_at"), s.get("completed_at")
        if not (a and c):
            continue
        try:
            if isinstance(a, str):
                a = datetime.fromisoformat(a.replace("Z", "+00:00"))
            if isinstance(c, str):
                c = datetime.fromisoformat(c.replace("Z", "+00:00"))
            service_seconds.append((c - a).total_seconds())
        except Exception:
            pass
    service_seconds.sort()

    total_arrivals = geofence_n + inferred_n + fallback_n
    telemetry = {
        "geofence_count": geofence_n,
        "geofence_inferred_count": inferred_n,
        "fallback_count": fallback_n,
        # `geofence_rate` is the strict ratio (real geofence hits only) —
        # the diagnostic for whether the hook itself is firing.
        "geofence_rate": (
            round(geofence_n / (geofence_n + fallback_n + inferred_n), 3)
            if total_arrivals > 0
            else None
        ),
        # `arrival_proximity_rate` adds inferred geofence samples — the
        # driver-friendly "we tracked your arrival" metric. Bumps from
        # ~0% to ~80% expected once the inference backstop kicks in.
        "arrival_proximity_rate": (
            round((geofence_n + inferred_n) / total_arrivals, 3)
            if total_arrivals > 0
            else None
        ),
        "completion_distance_p50_m": _pct(distances, 0.5),
        "completion_distance_p95_m": _pct(distances, 0.95),
        "service_seconds_p50": _pct(service_seconds, 0.5),
        "service_seconds_p95": _pct(service_seconds, 0.95),
        "distance_samples": len(distances),
        "service_samples": len(service_seconds),
    }

    # Compute timestamps
    completed_times = [s["completed_at"] for s in delivered if s.get("completed_at")]
    started_at = min((s.get("created_at") for s in all_stops if s.get("created_at")), default=None)
    finished_at = max(completed_times, default=None) if completed_times else None

    route_doc = {
        "id": str(uuid.uuid4()),
        "user_id": current_user.user_id,
        "archived_at": datetime.now(timezone.utc).isoformat(),
        "started_at": started_at.isoformat() if isinstance(started_at, datetime) else started_at,
        "finished_at": finished_at.isoformat() if isinstance(finished_at, datetime) else finished_at,
        "stops": all_stops,
        "summary": {
            "total_stops": len(all_stops),
            "delivered": len(delivered),
            "skipped": len(skipped),
            "failed": len(failed),
            "pending": len(pending),
            "total_weight_kg": round(total_weight, 2),
            "total_quantity": total_quantity,
            "telemetry": telemetry,
            # Persisted from the optional archive body so the
            # `/api/_meta/telemetry-rollup` endpoint can answer
            # "which algorithm did I use today?". Older clients
            # omit these, and we keep them None for backwards-compat.
            "algorithm": optional_body.get("algorithm") if isinstance(optional_body.get("algorithm"), str) else None,
            "total_distance_km": optional_body.get("total_distance_km") if isinstance(optional_body.get("total_distance_km"), (int, float)) else None,
            "total_duration_seconds": optional_body.get("total_duration_seconds") if isinstance(optional_body.get("total_duration_seconds"), (int, float)) else None,
        },
    }

    await db.route_history.insert_one(route_doc)
    # Remove the MongoDB _id that insert_one adds to the dict
    route_doc.pop("_id", None)

    # ── Route Telepathy (Phase A): record sequence preferences ──
    # Gated to users in TELEPATHY_USER_IDS.
    # Any errors are swallowed — learning failures must never break
    # the archival flow, which is a critical path for the driver.
    try:
        if current_user.user_id in TELEPATHY_USER_IDS:
            from ml.sequence_learner import record_completion as _seq_record
            seq_stats = await _seq_record(db, current_user.user_id, route_doc)
            logger.info(
                "[sequence_learner] recorded route for user=%s: %s",
                current_user.user_id, seq_stats,
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("[sequence_learner] record_completion failed: %s", e)

    # ── Route Telepathy (Phase B): map-match the GPS breadcrumb ──
    # Breadcrumb arrives in optional_body["breadcrumb"] as a list of
    # {lat, lng} dicts. We kick off the map-matching in a background task
    # because OSRM /match can take 2-5 s for long routes, and we don't
    # want to block the archival HTTP response on that. The task is
    # fire-and-forget; errors only log.
    try:
        breadcrumb = optional_body.get("breadcrumb")
        if (
            current_user.user_id in TELEPATHY_USER_IDS
            and isinstance(breadcrumb, list)
            and len(breadcrumb) >= 2
        ):
            from ml.road_segment_learner import record_route_breadcrumb as _road_record
            # Coerce shape — frontend sometimes sends {longitude, latitude}.
            normalised = []
            for p in breadcrumb:
                if not isinstance(p, dict):
                    continue
                lat = p.get("lat") if "lat" in p else p.get("latitude")
                lng = p.get("lng") if "lng" in p else p.get("longitude")
                if lat is None or lng is None:
                    continue
                try:
                    normalised.append({"lat": float(lat), "lng": float(lng)})
                except (TypeError, ValueError):
                    continue
            if len(normalised) >= 2:
                asyncio.create_task(
                    _road_record(db, current_user.user_id, normalised)
                )
                logger.info(
                    "[road_learner] queued breadcrumb processing user=%s points=%d",
                    current_user.user_id, len(normalised),
                )
    except Exception as e:  # noqa: BLE001
        logger.warning("[road_learner] record_route_breadcrumb queue failed: %s", e)

    return {"archived": True, "route": route_doc}


@router.get("/routes/history")
async def get_route_history(current_user=Depends(_current_user)):
    """List all archived routes (summary only, no full stop list)."""
    from server import db  # noqa: WPS433
    cursor = db.route_history.find(
        {"user_id": current_user.user_id},
        {"_id": 0, "stops": 0}  # exclude heavy stops array
    ).sort("archived_at", -1)

    routes = await cursor.to_list(500)
    return {"routes": routes}


# ── Route Telepathy (Phase A) — sequence preference endpoints ──────────
# Both endpoints are scoped strictly to the calling user; we never expose
# another user's preferences. The owner-only gating happens inside
# server.py's optimize/archive hooks, so these endpoints work for every
# user (they just return zero data until the owner gate is widened).

@router.get("/learn/sequence-stats")
async def learn_sequence_stats(current_user=Depends(_current_user)):
    """Stats on learned sequence preferences for the calling user.

    Used by the frontend to render a "🧠 Telepathy" badge once enough
    high-confidence rules exist.
    """
    from server import db, TELEPATHY_USER_IDS  # noqa: WPS433
    from ml.sequence_learner import get_stats as _seq_stats
    stats = await _seq_stats(db, current_user.user_id)
    # Echo whether this user is currently in the learning whitelist so the
    # UI can show "Coming soon" vs "Active".
    stats["enabled_for_user"] = current_user.user_id in TELEPATHY_USER_IDS
    return stats


@router.post("/learn/sequence-reset")
async def learn_sequence_reset(current_user=Depends(_current_user)):
    """Wipe all learned sequence preferences for the calling user."""
    from server import db  # noqa: WPS433
    from ml.sequence_learner import reset as _seq_reset
    deleted = await _seq_reset(db, current_user.user_id)
    return {"deleted": deleted}


@router.get("/learn/road-stats")
async def learn_road_stats(current_user=Depends(_current_user)):
    """Stats on learned road-segment preferences for the calling user.

    Surfaced by the Telepathy UI to show how many edges of the OSM
    network the driver has traversed.
    """
    from server import db, TELEPATHY_USER_IDS  # noqa: WPS433
    from ml.road_segment_learner import get_stats as _road_stats
    stats = await _road_stats(db, current_user.user_id)
    stats["enabled_for_user"] = current_user.user_id in TELEPATHY_USER_IDS
    return stats


@router.post("/learn/road-reset")
async def learn_road_reset(current_user=Depends(_current_user)):
    """Wipe all learned road-segment preferences for the calling user."""
    from server import db  # noqa: WPS433
    from ml.road_segment_learner import reset as _road_reset
    deleted = await _road_reset(db, current_user.user_id)
    return {"deleted": deleted}


@router.post("/route/preferred-polyline")
async def preferred_polyline(
    request: Request,
    current_user=Depends(_current_user),
):
    """Return the user's preferred polyline between two coords.

    Fetches up to 3 OSRM alternatives, scores each by historical road
    familiarity, and picks the most-familiar one provided it's within
    +15% of the fastest alternative's duration. Falls back to the
    fastest if no preferences exist or all alternatives score 0.

    Request body:
        { "from": [lng, lat], "to": [lng, lat] }

    Response: same shape as /api/directions (geometry / distance /
    duration / steps / legs) PLUS a `telepathy` field describing
    whether familiarity preferences were applied, so the frontend can
    show a "🧠 Telepathy: routing via your familiar roads" badge.
    """
    from server import db, OSRM_URL  # noqa: WPS433
    body = await request.json()
    src = body.get("from")
    dst = body.get("to")
    if not (isinstance(src, list) and isinstance(dst, list) and len(src) == 2 and len(dst) == 2):
        raise HTTPException(400, "from/to must be [lng, lat] pairs")

    # Fetch OSRM alternatives. Reuse existing OSRM_URL. Ask for steps so
    # we can return turn-by-turn instructions matching /api/directions.
    coord = f"{src[0]:.6f},{src[1]:.6f};{dst[0]:.6f},{dst[1]:.6f}"
    url = f"{OSRM_URL}/route/v1/driving/{coord}"
    params = {
        "alternatives": "3",
        "overview": "full",
        "geometries": "geojson",
        "steps": "true",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(url, params=params)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"OSRM unavailable: {e}")
    if r.status_code != 200:
        raise HTTPException(502, f"OSRM HTTP {r.status_code}")
    data = r.json()
    if data.get("code") != "Ok":
        raise HTTPException(502, f"OSRM returned {data.get('code')}")
    routes = data.get("routes") or []
    if not routes:
        raise HTTPException(404, "No routes from OSRM")

    # Score each alternative by familiarity.
    from ml.road_segment_learner import score_polyline as _score
    fastest_duration = routes[0].get("duration") or 0.0
    scored: List[Dict[str, Any]] = []
    for idx, rt in enumerate(routes):
        coords_geo = rt.get("geometry", {}).get("coordinates") or []
        if not coords_geo:
            continue
        score, matched, total = await _score(
            db, current_user.user_id,
            [(c[0], c[1]) for c in coords_geo],
        )
        scored.append({
            "idx": idx,
            "route": rt,
            "duration": rt.get("duration", 0.0),
            "distance": rt.get("distance", 0.0),
            "coords": coords_geo,
            "score": score,
            "matched": matched,
            "total": total,
        })

    if not scored:
        raise HTTPException(404, "No usable routes")

    # Pick the highest-score route whose duration is ≤ 1.15x of fastest.
    # This budget prevents wildly slower scenic detours from winning.
    budget = fastest_duration * 1.15
    eligible = [s for s in scored if s["duration"] <= budget]
    if not eligible:
        eligible = scored
    eligible.sort(key=lambda s: (-s["score"], s["duration"]))
    chosen = eligible[0]

    # Gate the preference logic to the owner account for now — others
    # always get the fastest route until we widen the rollout.
    fastest = scored[0]
    applied = (
        current_user.user_id == "user_2a7d88cbb419"
        and chosen["idx"] != 0
        and chosen["score"] > 0
    )
    if not applied:
        chosen = fastest

    # Build the directions-style response shape (same fields the frontend
    # consumes from /api/directions) so callers can swap endpoints with
    # zero shape changes downstream.
    # _extract_steps lives in routes/routing.py (chunk F); lazy-import from
    # server so the call works regardless of whether chunk F has landed yet.
    from server import _extract_steps  # noqa: WPS433
    osrm_legs = chosen["route"].get("legs", [])
    steps = _extract_steps(osrm_legs)
    legs = [
        {
            "distance": leg.get("distance", 0),
            "duration": leg.get("duration", 0),
            "summary": leg.get("summary", ""),
        }
        for leg in osrm_legs
    ]
    geometry = chosen["route"].get("geometry") or {
        "type": "LineString",
        "coordinates": chosen["coords"],
    }

    return {
        # /api/directions-compatible fields
        "geometry": geometry,
        "distance": chosen["distance"],
        "duration": chosen["duration"],
        "steps": steps,
        "legs": legs,
        "waypoints": data.get("waypoints", []),
        "source": "osrm",
        # Telepathy metadata for the badge / debugging
        "telepathy": {
            "applied": applied,
            "familiarity": round(chosen["score"], 3),
            "fastest_familiarity": round(fastest["score"], 3),
            "matched_edges": chosen["matched"],
            "total_edges": chosen["total"],
            "chose_alternative": chosen["idx"],
            "alternatives_considered": len(scored),
            "time_cost_s": round(chosen["duration"] - fastest["duration"], 1),
        },
        # Legacy fields for any older clients still on the old shape
        "polyline": chosen["coords"],
        "duration_s": chosen["duration"],
        "distance_m": chosen["distance"],
        "familiarity": round(chosen["score"], 3),
        "matched_edges": chosen["matched"],
        "total_edges": chosen["total"],
        "chose_alternative": chosen["idx"],
    }


@router.get("/routes/history/{route_id}")
async def get_route_detail(route_id: str, current_user=Depends(_current_user)):
    """Get full detail of a specific archived route including all stops."""
    from server import db  # noqa: WPS433
    route = await db.route_history.find_one(
        {"id": route_id, "user_id": current_user.user_id}, {"_id": 0}
    )
    if not route:
        raise HTTPException(status_code=404, detail="Route not found")
    return route


@router.delete("/routes/history/{route_id}")
async def delete_route_history(route_id: str, current_user=Depends(_current_user)):
    """Delete a specific route from history."""
    from server import db  # noqa: WPS433
    result = await db.route_history.delete_one(
        {"id": route_id, "user_id": current_user.user_id}
    )
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Route not found")
    return {"deleted": True, "route_id": route_id}


@router.get("/routes/history/{route_id}/export.gpx")
async def export_route_gpx(route_id: str, current_user=Depends(_current_user)):
    """Export a saved route as GPX (Garmin, Strava, Komoot, most GPS devices)."""
    from server import db  # noqa: WPS433
    route = await db.route_history.find_one(
        {"id": route_id, "user_id": current_user.user_id}, {"_id": 0}
    )
    if not route:
        raise HTTPException(status_code=404, detail="Route not found")

    stops = route.get("stops") or []
    name = _escape_xml(route.get("name") or f"RouTeD route {route_id[:8]}")

    # GPX 1.1 — waypoints only (no <trkseg>). A full trackline would duplicate
    # the routing engine's output and many GPS devices treat trackpoints very
    # differently from user-planned routes; waypoints are the universal truth.
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="RouTeD" xmlns="http://www.topografix.com/GPX/1/1">',
        f"  <metadata><name>{name}</name></metadata>",
    ]
    for s in stops:
        try:
            lat, lng = float(s.get("latitude")), float(s.get("longitude"))
        except (TypeError, ValueError):
            continue
        lines.append(
            f'  <wpt lat="{lat}" lon="{lng}"><name>{_escape_xml(s.get("name") or s.get("address") or "")}</name></wpt>'
        )
    lines.append("</gpx>")
    body = "\n".join(lines).encode("utf-8")
    return Response(
        content=body,
        media_type="application/gpx+xml",
        headers={"Content-Disposition": f'attachment; filename="route-{route_id[:8]}.gpx"'},
    )


@router.get("/routes/history/{route_id}/export.kml")
async def export_route_kml(route_id: str, current_user=Depends(_current_user)):
    """Export a saved route as KML (Google Earth, Google My Maps, most GIS tools)."""
    from server import db  # noqa: WPS433
    route = await db.route_history.find_one(
        {"id": route_id, "user_id": current_user.user_id}, {"_id": 0}
    )
    if not route:
        raise HTTPException(status_code=404, detail="Route not found")

    stops = route.get("stops") or []
    name = _escape_xml(route.get("name") or f"RouTeD route {route_id[:8]}")

    # KML — placemarks for each stop only (no <LineString> track). GIS tools
    # and Google Earth will render clean pins the user can reorder / click;
    # a track polyline would pin the user to our routing engine's decisions.
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>',
        f"  <name>{name}</name>",
    ]
    # Stops as Placemarks (KML expects lng,lat[,alt] — opposite of GPX).
    for s in stops:
        try:
            lat, lng = float(s.get("latitude")), float(s.get("longitude"))
        except (TypeError, ValueError):
            continue
        label = _escape_xml(s.get("name") or s.get("address") or "")
        lines.append(
            f"  <Placemark><name>{label}</name><Point><coordinates>{lng},{lat},0</coordinates></Point></Placemark>"
        )
    lines.append("</Document></kml>")
    body = "\n".join(lines).encode("utf-8")
    return Response(
        content=body,
        media_type="application/vnd.google-earth.kml+xml",
        headers={"Content-Disposition": f'attachment; filename="route-{route_id[:8]}.kml"'},
    )


@router.post("/routes/history/{route_id}/resume")
async def resume_route(route_id: str, current_user=Depends(_current_user)):
    """Restore an archived route back into active stops (resets completion status).

    Hardened against:
      - Cross-tenant access: lookup is always scoped to current_user.user_id;
        no fallback path that bypasses ownership is permitted.
      - Stops carrying completion telemetry fields that must be cleared so the
        resumed route shows as pristine pending.
      - Duplicate stop ids inside the same archive (would collide with the
        unique (id, user_id) index).
      - Generic exceptions — we now surface the real reason to the client.
    """
    from server import db  # noqa: WPS433
    try:
        # Lookup scoped strictly to the current user — no cross-tenant fallback.
        route = await db.route_history.find_one(
            {"id": route_id, "user_id": current_user.user_id}, {"_id": 0}
        )
        if not route:
            raise HTTPException(status_code=404, detail="Route not found in history")

        archived_stops = route.get("stops", []) or []
        if not archived_stops:
            raise HTTPException(status_code=400, detail="Archived route has no stops")

        # Fields that must be wiped so a resumed stop is treated as pending.
        # Anything left behind (completion coords, arrival_method, photo proof,
        # service-time samples) would make the UI render the stop as "done".
        completion_fields = (
            "completed_at", "arrived_at", "departed_at",
            "completion_lat", "completion_lng", "completion_accuracy",
            "arrival_method", "arrival_distance_m", "arrival_confidence",
            "failure_reason", "failure_code", "skip_reason",
            "proof_photo_url", "proof_photo_uploaded_at",
            "service_time_seconds", "service_time_source",
            "delivered_at", "skipped_at", "failed_at",
        )

        # Dedupe stop ids inside the archive — the (id, user_id) index is
        # unique so duplicates would 500 the insert_many.
        seen_ids: set[str] = set()
        cleaned: list[dict] = []
        for i, raw in enumerate(archived_stops):
            stop = dict(raw)  # don't mutate the archive document
            stop.pop("_id", None)
            # If id is missing or duplicated, mint a new one so the index
            # constraint is satisfied.
            sid = stop.get("id")
            if not sid or sid in seen_ids:
                sid = str(uuid.uuid4())
                stop["id"] = sid
            seen_ids.add(sid)

            stop["user_id"] = current_user.user_id
            stop["completed"] = False
            stop["delivery_status"] = "pending"
            stop["order"] = i
            for f in completion_fields:
                stop.pop(f, None)
            cleaned.append(stop)

        # Clear current active stops + replace atomically-ish.
        await db.stops.delete_many({"user_id": current_user.user_id})
        if cleaned:
            await db.stops.insert_many(cleaned, ordered=False)

        return {"resumed": True, "stops_count": len(cleaned)}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[resume_route] failed route_id={route_id} user={current_user.user_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Resume failed: {type(e).__name__}: {e}")


@router.get("/routes/stats")
async def get_route_stats(current_user=Depends(_current_user)):
    """Aggregate lifetime stats across all archived routes."""
    from server import db  # noqa: WPS433
    pipeline = [
        {"$match": {"user_id": current_user.user_id}},
        {"$group": {
            "_id": None,
            "total_routes": {"$sum": 1},
            "total_delivered": {"$sum": "$summary.delivered"},
            "total_skipped": {"$sum": "$summary.skipped"},
            "total_failed": {"$sum": "$summary.failed"},
            "total_stops": {"$sum": "$summary.total_stops"},
            "total_weight_kg": {"$sum": "$summary.total_weight_kg"},
            "total_quantity": {"$sum": "$summary.total_quantity"},
            "avg_stops_per_route": {"$avg": "$summary.total_stops"},
            "avg_delivered_per_route": {"$avg": "$summary.delivered"},
        }},
    ]
    results = await db.route_history.aggregate(pipeline).to_list(1)
    if not results:
        return {
            "total_routes": 0, "total_delivered": 0, "total_skipped": 0,
            "total_failed": 0, "total_stops": 0, "total_weight_kg": 0,
            "total_quantity": 0, "avg_stops_per_route": 0, "avg_delivered_per_route": 0,
        }
    stats = results[0]
    stats.pop("_id", None)
    return stats
