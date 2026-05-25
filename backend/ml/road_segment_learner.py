"""
Route Telepathy — Phase B: Road Segment Preference Learner.

Mechanism
─────────
After each completed route, the user's actual GPS breadcrumb is map-matched
to OSRM's road network. Each matched edge (= a pair of consecutive OSM
node IDs) increments a per-user counter. On reroute, alternative routes
are scored against this counter and the most familiar (within an
acceptable distance budget) wins.

Why edges (node pairs), not ways or routes
   - Way IDs are coarse (a single way can be 5 km long).
   - Edge-pair keys are stable across OSM updates as long as the geometry
     hasn't been split — good enough for personal preference learning.
   - Storage: ~5 KB / completed route after dedup.

Storage shape (collection: `road_preferences`)
   {
     user_id: "...",
     edge_key: "<smaller_node>:<larger_node>",
     used_count: int,
     last_used: ISO datetime,
   }
   Compound index on (user_id, edge_key) makes lookups O(1).

Hot paths
   record_route_breadcrumb()  – run async after archive
   score_polyline()           – called for each candidate alternative
   reset()                    – per-user wipe (privacy)
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

# Tunables. OSRM /match recommends ≤ 100 coords per call; we decimate
# the raw breadcrumb to roughly one point per `DECIMATE_METRES` so we
# stay under the limit AND avoid wasting OSRM CPU on near-duplicate
# fixes during traffic stops.
DECIMATE_METRES = 50.0
MATCH_BATCH_SIZE = 95  # safely under OSRM's 100-coord limit
OSRM_URL = os.environ.get("OSRM_URL", "https://router.project-osrm.org")
OSRM_TIMEOUT_S = 15.0


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Rough metres between two coords. Sufficient for 50 m decimation."""
    import math
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _decimate(breadcrumb: List[Dict[str, float]]) -> List[Dict[str, float]]:
    """Keep one point per DECIMATE_METRES along the path.

    breadcrumb: [{lat, lng} ...] in order. Output preserves first + last.
    """
    if len(breadcrumb) < 2:
        return list(breadcrumb)
    kept = [breadcrumb[0]]
    last = breadcrumb[0]
    for p in breadcrumb[1:-1]:
        if _haversine_m(last["lat"], last["lng"], p["lat"], p["lng"]) >= DECIMATE_METRES:
            kept.append(p)
            last = p
    kept.append(breadcrumb[-1])
    return kept


async def _osrm_match(coords: List[Tuple[float, float]]) -> Optional[List[int]]:
    """Call OSRM /match with the given (lng, lat) pairs.

    Returns the flattened list of OSM node IDs along the matched route,
    or None on any failure. Caller handles that gracefully.
    """
    if len(coords) < 2:
        return None
    coord_str = ";".join(f"{lng:.6f},{lat:.6f}" for lng, lat in coords)
    url = f"{OSRM_URL}/match/v1/driving/{coord_str}"
    # We need OSM node IDs for edge keys — annotation=nodes gives us that.
    # radiuses=25 widens OSRM's snap tolerance modestly (public OSRM caps
    # this; 25 m is the largest the demo server accepts before TooBig).
    # 25 m is enough for typical GPS noise; under dense tree cover the
    # caller can omit/lower this and accept lower match coverage.
    radiuses = ";".join("25" for _ in coords)
    params = {
        "overview": "false",
        "annotations": "nodes",
        "geometries": "geojson",
        "tidy": "true",
        "radiuses": radiuses,
    }
    try:
        async with httpx.AsyncClient(timeout=OSRM_TIMEOUT_S) as client:
            r = await client.get(url, params=params)
        if r.status_code != 200:
            try:
                body = r.text[:200]
            except Exception:
                body = ""
            logger.warning("[road_learner] OSRM /match HTTP %s url=%s body=%s", r.status_code, str(r.url)[:300], body)
            return None
        data = r.json()
        if data.get("code") != "Ok":
            return None
        nodes: List[int] = []
        # /match returns `matchings` (1-N). Concatenate every matched leg.
        for m in data.get("matchings", []) or []:
            for leg in m.get("legs", []) or []:
                leg_nodes = (leg.get("annotation") or {}).get("nodes") or []
                if not leg_nodes:
                    continue
                # Avoid double-counting the boundary node between legs.
                if nodes and nodes[-1] == leg_nodes[0]:
                    nodes.extend(leg_nodes[1:])
                else:
                    nodes.extend(leg_nodes)
        return nodes if nodes else None
    except (httpx.TimeoutException, httpx.HTTPError, ValueError, KeyError) as e:
        logger.warning("[road_learner] OSRM /match failed: %s", e)
        return None


def _nodes_to_edge_keys(nodes: List[int]) -> List[str]:
    """Turn a flat node list into a deduplicated list of edge_key strings.

    edge_key = "<smaller>:<larger>" so A→B and B→A collapse to one entry.
    Drivers usually traverse a street in both directions over time; we
    don't care about direction for familiarity scoring.
    """
    seen = set()
    out = []
    for a, b in zip(nodes, nodes[1:]):
        if a == b:
            continue
        lo, hi = (a, b) if a < b else (b, a)
        k = f"{lo}:{hi}"
        if k in seen:
            continue
        seen.add(k)
        out.append(k)
    return out


async def record_route_breadcrumb(
    db,
    user_id: str,
    breadcrumb: List[Dict[str, float]],
) -> Dict[str, Any]:
    """Map-match the given breadcrumb and increment per-edge counters.

    Intended to be called in a `asyncio.create_task(...)` from the route
    archive endpoint — DO NOT block the archive HTTP response on this.
    """
    if not breadcrumb or len(breadcrumb) < 2:
        return {"recorded": False, "reason": "breadcrumb_too_short"}

    decimated = _decimate(breadcrumb)
    if len(decimated) < 2:
        return {"recorded": False, "reason": "decimation_too_aggressive"}

    # Batch into chunks of ≤ MATCH_BATCH_SIZE so OSRM doesn't reject us.
    # Each chunk overlaps by 1 point with the next so the edges at the
    # boundary still get matched.
    chunks: List[List[Tuple[float, float]]] = []
    i = 0
    while i < len(decimated):
        slice_ = decimated[i:i + MATCH_BATCH_SIZE]
        chunks.append([(p["lng"], p["lat"]) for p in slice_])
        # Step forward by (size - 1) so the next chunk's first point is
        # the last point of this chunk — a 1-coord overlap.
        i += MATCH_BATCH_SIZE - 1
        if len(slice_) < MATCH_BATCH_SIZE:
            break

    all_nodes: List[int] = []
    for ch in chunks:
        chunk_nodes = await _osrm_match(ch)
        if not chunk_nodes:
            continue
        # Bridge boundary like in _osrm_match.
        if all_nodes and all_nodes[-1] == chunk_nodes[0]:
            all_nodes.extend(chunk_nodes[1:])
        else:
            all_nodes.extend(chunk_nodes)

    if not all_nodes:
        return {"recorded": False, "reason": "osrm_no_match"}

    edge_keys = _nodes_to_edge_keys(all_nodes)
    if not edge_keys:
        return {"recorded": False, "reason": "no_edges"}

    now = datetime.now(timezone.utc).isoformat()
    from pymongo import UpdateOne
    bulk = [
        UpdateOne(
            {"user_id": user_id, "edge_key": k},
            {"$inc": {"used_count": 1}, "$set": {"last_used": now}},
            upsert=True,
        )
        for k in edge_keys
    ]
    await db.road_preferences.bulk_write(bulk, ordered=False)
    logger.info(
        "[road_learner] recorded user=%s edges=%d (from %d breadcrumb / %d decimated points)",
        user_id, len(edge_keys), len(breadcrumb), len(decimated),
    )
    return {
        "recorded": True,
        "edges": len(edge_keys),
        "breadcrumb_points": len(breadcrumb),
        "decimated_points": len(decimated),
    }


async def score_polyline(
    db,
    user_id: str,
    coords: List[Tuple[float, float]],
) -> Tuple[float, int, int]:
    """Compute a familiarity score for the given polyline.

    coords: [(lng, lat), ...] – the candidate route's geometry.
    Returns (score, matched_edges, total_edges).
        score = matched_edges / total_edges, in [0, 1].
        0 if total_edges == 0.
    """
    nodes = await _osrm_match(coords[:MATCH_BATCH_SIZE])
    if not nodes:
        return 0.0, 0, 0
    edge_keys = _nodes_to_edge_keys(nodes)
    if not edge_keys:
        return 0.0, 0, 0

    cursor = db.road_preferences.find(
        {"user_id": user_id, "edge_key": {"$in": edge_keys}},
        {"_id": 0, "edge_key": 1, "used_count": 1},
    )
    docs = await cursor.to_list(length=len(edge_keys))
    matched = len(docs)
    return matched / len(edge_keys), matched, len(edge_keys)


async def get_stats(db, user_id: str) -> Dict[str, Any]:
    """Stats for the Telepathy UI panel."""
    total = await db.road_preferences.count_documents({"user_id": user_id})
    if total == 0:
        return {"total_edges": 0, "frequent_edges": 0, "ready": False}
    # "Frequent" = edges traversed ≥ 3 times (real preference, not noise).
    frequent = await db.road_preferences.count_documents({
        "user_id": user_id,
        "used_count": {"$gte": 3},
    })
    return {
        "total_edges": total,
        "frequent_edges": frequent,
        "ready": frequent > 0,
    }


async def reset(db, user_id: str) -> int:
    """Wipe all learned road preferences for this user."""
    result = await db.road_preferences.delete_many({"user_id": user_id})
    return int(result.deleted_count or 0)
