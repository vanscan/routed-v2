"""Directions, Navigation, Geocode, and Mapbox-token endpoints.

Endpoints moved here from server.py (lines 7939–8536):

    GET /geocode           → address search with full metadata
    GET /directions        → OSRM (primary) / Mapbox (fallback) route
    GET /navigation        → full navigation data for all active stops
    GET /mapbox-token      → exposes the Mapbox token to the frontend

Private helpers also moved here:
    _maneuver_instruction() — formats OSRM maneuver type/modifier into text
    _extract_steps()        — shared step extractor (re-exported via server.py
                              so route_history.py's lazy import still works)
    _round_coord()          — rounds coordinate strings for cache-key stability

`_directions_cache` stays in server.py because the /cache/stats endpoint
references it; this module lazy-imports it from server on first request.
All other server-level symbols (db, MAPBOX_TOKEN, OSRM_URL, circuit-breaker
helpers) are also lazy-imported inside each endpoint so this file loads
cleanly before server.py finishes initialising.
"""
from __future__ import annotations

import logging
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response

from routes._geocoding import _extract_rich_feature, get_user_geocoding_context
from routes._route_constraints import inject_sugar_bag_waypoints, needs_sugar_bag_injection

logger = logging.getLogger("server")
router = APIRouter()


async def _current_user(request: Request):
    """Dep wrapper — defers the `server` import until the first request."""
    from server import get_current_user  # noqa: WPS433
    return await get_current_user(request)


def _maneuver_instruction(maneuver: dict, name: str) -> str:
    """Build a human-readable instruction from OSRM maneuver type/modifier + street name."""
    mtype = maneuver.get("type", "")
    modifier = maneuver.get("modifier", "")
    road = f" onto {name}" if name else ""
    lookup = {
        ("depart", ""): f"Head{road}",
        ("arrive", ""): "You have arrived",
        ("turn", "left"): f"Turn left{road}",
        ("turn", "right"): f"Turn right{road}",
        ("turn", "slight left"): f"Slight left{road}",
        ("turn", "slight right"): f"Slight right{road}",
        ("turn", "sharp left"): f"Sharp left{road}",
        ("turn", "sharp right"): f"Sharp right{road}",
        ("turn", "uturn"): f"Make a U-turn{road}",
        ("continue", "straight"): f"Continue straight{road}",
        ("continue", ""): f"Continue{road}",
        ("merge", "slight left"): f"Merge left{road}",
        ("merge", "slight right"): f"Merge right{road}",
        ("new name", ""): f"Continue{road}",
        ("roundabout", ""): f"Enter the roundabout{road}",
        ("rotary", ""): f"Enter the roundabout{road}",
        ("exit roundabout", ""): f"Exit the roundabout{road}",
        ("exit rotary", ""): f"Exit the roundabout{road}",
        ("fork", "left"): f"Keep left{road}",
        ("fork", "right"): f"Keep right{road}",
        ("end of road", "left"): f"Turn left{road}",
        ("end of road", "right"): f"Turn right{road}",
    }
    key = (mtype, modifier)
    if key in lookup:
        return lookup[key]
    key_type = (mtype, "")
    if key_type in lookup:
        return lookup[key_type]
    if modifier:
        return f"{modifier.replace('_', ' ').title()}{road}"
    return f"Continue{road}"


def _extract_steps(legs: list) -> list:
    """Extract steps from OSRM/Mapbox route legs — shared by batch and single paths.

    Works with both OSRM and Mapbox response formats (OSRM is the origin of the format).
    Generates human-readable instructions from maneuver type/modifier for OSRM responses.
    """
    all_steps = []
    for leg_idx, leg in enumerate(legs):
        for step in leg.get("steps", []):
            maneuver = step.get("maneuver", {})
            instruction = maneuver.get("instruction", "")
            if not instruction:
                instruction = _maneuver_instruction(maneuver, step.get("name", ""))
            all_steps.append({
                "leg_index": leg_idx,
                "distance": step.get("distance", 0),
                "duration": step.get("duration", 0),
                "instruction": instruction,
                "type": maneuver.get("type", ""),
                "modifier": maneuver.get("modifier", ""),
                "bearing_before": maneuver.get("bearing_before", 0),
                "bearing_after": maneuver.get("bearing_after", 0),
                "location": maneuver.get("location", []),
                "name": step.get("name", ""),
                "geometry": step.get("geometry", {}),
                "driving_side": step.get("driving_side", "right"),
                "mode": step.get("mode", "driving"),
                "voice_instruction": step.get("voiceInstructions", [{}])[0].get("announcement", "") if step.get("voiceInstructions") else "",
                "banner_instruction": step.get("bannerInstructions", [{}])[0] if step.get("bannerInstructions") else {}
            })
    return all_steps


def _round_coord(c: str, precision: int = 4) -> str:
    """Round a coordinate string for cache key (reduces cache misses from GPS jitter)"""
    parts = c.split(",")
    return ",".join(f"{float(p):.{precision}f}" for p in parts)


@router.get("/geocode")
async def geocode_address(query: str, current_user=Depends(_current_user)):
    """Search for addresses using Mapbox Geocoding API with full metadata.
    Returns: rooftop centroid, access/navigation point, plus code, interpolation status, and rich metadata"""
    from server import MAPBOX_TOKEN  # noqa: WPS433
    if not MAPBOX_TOKEN:
        raise HTTPException(status_code=500, detail="Mapbox token not configured")

    geo_context = await get_user_geocoding_context(current_user.user_id)

    params = {
        "q": query,
        "access_token": MAPBOX_TOKEN,
        "limit": 5,
        "types": "address,street,place",
        "routing": "true",
    }
    if geo_context.get("proximity"):
        params["proximity"] = geo_context["proximity"]
    if geo_context.get("country"):
        params["country"] = geo_context["country"]

    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://api.mapbox.com/search/geocode/v6/forward",
            params=params
        )

        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail="Geocoding failed")

        data = response.json()
        return [_extract_rich_feature(f) for f in data.get("features", [])]


@router.get("/directions")
async def get_directions(coordinates: str, response: Response, current_user=Depends(_current_user)):
    """Get route directions from local OSRM Route API (zero-cost, no API key).
    coordinates format: lng1,lat1;lng2,lat2;lng3,lat3
    No waypoint limits — OSRM handles hundreds of waypoints natively.
    Falls back to Mapbox if OSRM is unavailable.
    """
    from server import (  # noqa: WPS433
        MAPBOX_TOKEN, OSRM_URL,
        _directions_cache,
        _osrm_enabled, _osrm_log_failure, _osrm_note_success,
    )

    # Check TTL cache (rounded to 4 decimal places ~ 11m precision to absorb GPS jitter)
    coord_list = coordinates.split(";")
    cache_key = ";".join(_round_coord(c) for c in coord_list)
    cached = _directions_cache.get(cache_key)
    if cached is not None:
        response.headers["X-Cache"] = "HIT"
        return cached

    response.headers["X-Cache"] = "MISS"

    # ── Sugar Bag Rd injection: for any consecutive LM ↔ Aroona transition,
    # insert the Sugarbag Rd Reservoir midpoint so OSRM routes via Sugar Bag
    # Rd (bypassing the traffic-lighted Caloundra Rd corridor). We track a
    # `leg_map` so the downstream response still reports ONE leg per original
    # stop transition — this preserves the frontend's nav.legs[i] contract.
    stops_for_sb: List[dict] = []
    for c in coord_list:
        parts = c.split(",")
        try:
            stops_for_sb.append({"longitude": float(parts[0]), "latitude": float(parts[1])})
        except (ValueError, IndexError):
            stops_for_sb.append({"longitude": 0.0, "latitude": 0.0})

    needs_sb = [False] + [
        needs_sugar_bag_injection(stops_for_sb[i - 1], stops_for_sb[i])
        for i in range(1, len(stops_for_sb))
    ]
    if any(needs_sb):
        injected_coords = inject_sugar_bag_waypoints(coord_list, stops_for_sb)
        osrm_coord_str = ";".join(injected_coords)
        # leg_map[i] = list of OSRM leg indices that collectively form the
        # ORIGINAL leg between stop i-1 and stop i. Injected legs produce a
        # [pre, post] pair; non-injected legs produce a single index.
        leg_map: List[List[int]] = []
        osrm_idx = 0
        for i in range(1, len(stops_for_sb)):
            if needs_sb[i]:
                leg_map.append([osrm_idx, osrm_idx + 1])
                osrm_idx += 2
            else:
                leg_map.append([osrm_idx])
                osrm_idx += 1
        logger.info(
            "Sugar Bag Rd injection: added %d waypoints across %d legs",
            sum(1 for n in needs_sb if n), len(leg_map),
        )
    else:
        osrm_coord_str = coordinates
        leg_map = None

    # --- Primary: Local OSRM Route API ---
    if _osrm_enabled():
        try:
            url = f"{OSRM_URL}/route/v1/driving/{osrm_coord_str}"
            params = {
                "overview": "full",
                "geometries": "geojson",
                "steps": "true",
            }
            # Generous connect headroom: the prod OSRM (pathpilot-osrm.fly.dev)
            # can cold-start, and a 1-2 s TCP/TLS stall must NOT be counted as
            # a failure — otherwise a few cold connects trip the circuit
            # breaker and force every direction onto Mapbox for 5 minutes.
            _dir_timeout = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=5.0)
            async with httpx.AsyncClient(timeout=_dir_timeout) as client:
                resp = await client.get(url, params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("code") == "Ok" and data.get("routes"):
                        route = data["routes"][0]
                        osrm_legs = route.get("legs", [])
                        # Coalesce injected Sugar Bag legs back into the
                        # original 1-leg-per-stop-transition shape so the
                        # frontend's nav.legs[i] contract is preserved.
                        if leg_map is not None:
                            coalesced: list = []
                            for indices in leg_map:
                                coalesced.append({
                                    "distance": sum(osrm_legs[i].get("distance", 0) for i in indices),
                                    "duration": sum(osrm_legs[i].get("duration", 0) for i in indices),
                                    "summary": osrm_legs[indices[0]].get("summary", ""),
                                })
                            legs_for_response = coalesced
                            all_steps = _extract_steps(osrm_legs)  # keep all steps — incl. the Sugar Bag turn
                        else:
                            legs_for_response = [
                                {
                                    "distance": leg.get("distance", 0),
                                    "duration": leg.get("duration", 0),
                                    "summary": leg.get("summary", ""),
                                }
                                for leg in osrm_legs
                            ]
                            all_steps = _extract_steps(osrm_legs)
                        result = {
                            "geometry": route["geometry"],
                            "distance": route.get("distance", 0),
                            "duration": route.get("duration", 0),
                            "steps": all_steps,
                            "legs": legs_for_response,
                            "waypoints": data.get("waypoints", []),
                            "source": "osrm"
                        }
                        # Reset the circuit breaker on success. Directions is
                        # the highest-frequency OSRM caller (fires every few
                        # metres while driving); without this reset, a couple
                        # of transient failures would accumulate uncleared and
                        # trip the breaker into 5 min of Mapbox even though
                        # OSRM is healthy.
                        _osrm_note_success()
                        _directions_cache.set(cache_key, result)
                        return result
        except Exception as e:
            _osrm_log_failure("OSRM directions failed, falling back to Mapbox", e)

    # --- Fallback: Mapbox Directions API ---
    if not MAPBOX_TOKEN:
        raise HTTPException(status_code=503, detail="OSRM unavailable and no Mapbox token configured")

    MAX_WAYPOINTS = 25

    if len(coord_list) > MAX_WAYPOINTS:
        all_legs = []
        total_distance = 0
        total_duration = 0
        combined_geometry = {"type": "LineString", "coordinates": []}

        async with httpx.AsyncClient() as client:
            for i in range(0, len(coord_list), MAX_WAYPOINTS - 1):
                chunk = coord_list[i:i + MAX_WAYPOINTS]
                if len(chunk) < 2:
                    break

                chunk_coords = ";".join(chunk)
                resp = await client.get(
                    f"https://api.mapbox.com/directions/v5/mapbox/driving/{chunk_coords}",
                    params={
                        "access_token": MAPBOX_TOKEN,
                        "geometries": "geojson",
                        "overview": "full",
                        "steps": "true",
                    }
                )

                if resp.status_code != 200:
                    raise HTTPException(status_code=resp.status_code, detail=f"Mapbox fallback failed for batch {i}")

                data = resp.json()
                if data.get("routes") and len(data["routes"]) > 0:
                    route = data["routes"][0]
                    total_distance += route.get("distance", 0)
                    total_duration += route.get("duration", 0)

                    if route.get("geometry", {}).get("coordinates"):
                        if combined_geometry["coordinates"]:
                            combined_geometry["coordinates"].extend(route["geometry"]["coordinates"][1:])
                        else:
                            combined_geometry["coordinates"] = route["geometry"]["coordinates"]

                    all_legs.extend(route.get("legs", []))

        all_steps = _extract_steps(all_legs)

        result = {
            "geometry": combined_geometry,
            "distance": total_distance,
            "duration": total_duration,
            "steps": all_steps,
            "legs": all_legs,
            "waypoints": [],
            "source": "mapbox_fallback"
        }
        _directions_cache.set(cache_key, result)
        return result

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://api.mapbox.com/directions/v5/mapbox/driving/{coordinates}",
            params={
                "access_token": MAPBOX_TOKEN,
                "geometries": "geojson",
                "overview": "full",
                "steps": "true",
            }
        )

        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail="Mapbox fallback failed")

        data = resp.json()
        if data.get("routes") and len(data["routes"]) > 0:
            route = data["routes"][0]

            all_steps = _extract_steps(route.get("legs", []))

            legs_summary = []
            for leg in route.get("legs", []):
                legs_summary.append({
                    "distance": leg.get("distance", 0),
                    "duration": leg.get("duration", 0),
                    "summary": leg.get("summary", "")
                })

            result = {
                "distance": route["distance"],
                "duration": route["duration"],
                "geometry": route["geometry"],
                "steps": all_steps,
                "legs": legs_summary,
                "waypoints": data.get("waypoints", []),
                "source": "mapbox_fallback"
            }
            _directions_cache.set(cache_key, result)
            return result

    return {"error": "No route found"}


@router.get("/navigation")
async def get_navigation_route(
    current_user=Depends(_current_user),
    current_lat: Optional[float] = Query(None, description="Current latitude"),
    current_lng: Optional[float] = Query(None, description="Current longitude")
):
    """Get full navigation data for all stops in order with waypoint splitting for large routes"""
    from server import db, MAPBOX_TOKEN, OSRM_URL, _osrm_enabled, _osrm_log_failure  # noqa: WPS433

    all_user_stops = await db.stops.find({"user_id": current_user.user_id}, {"_id": 0}).sort("order", 1).to_list(1000)
    completed_stops = [s for s in all_user_stops if s.get("completed")]
    stops = [s for s in all_user_stops if not s.get("completed")]

    if len(stops) < 1:
        return {"error": "Need at least 1 stop for navigation", "stops": all_user_stops}

    if not MAPBOX_TOKEN:
        raise HTTPException(status_code=500, detail="Mapbox token not configured")

    # Fetch any saved optimization hubs
    hubs = await db.optimization_hubs.find({"user_id": current_user.user_id}, {"_id": 0}).sort("order", 1).to_list(100)

    # Create a virtual "current location" stop if coordinates provided
    navigation_stops = []
    if current_lat is not None and current_lng is not None:
        current_location_stop = {
            "id": "current_location",
            "name": "Current Location",
            "address": "Your current location",
            "latitude": current_lat,
            "longitude": current_lng,
            "is_current_location": True
        }
        navigation_stops.append(current_location_stop)

    # If we have hubs, we need to interleave them with stops based on which segment each stop belongs to
    if hubs and len(hubs) > 0:
        # Build navigation order: stops before hub1, hub1, stops before hub2, hub2, etc.
        # First, assign each stop to a hub segment based on proximity
        sorted_hubs = sorted(hubs, key=lambda h: h['order'])

        # Create waypoint list including hubs
        waypoint_coords = []
        if current_lat is not None and current_lng is not None:
            waypoint_coords.append((current_lat, current_lng))
        for hub in sorted_hubs:
            waypoint_coords.append((hub['latitude'], hub['longitude']))

        # Assign each stop to the segment it's closest to
        stop_segments = {i: [] for i in range(len(sorted_hubs) + 1)}

        for stop in stops:
            stop_coord = (stop['latitude'], stop['longitude'])
            best_segment = 0
            best_score = float('inf')

            for seg_idx in range(len(sorted_hubs) + 1):
                # Calculate distance to segment boundaries
                if seg_idx < len(waypoint_coords):
                    dist_to_start = ((stop_coord[0] - waypoint_coords[seg_idx][0])**2 +
                                    (stop_coord[1] - waypoint_coords[seg_idx][1])**2)**0.5

                    if seg_idx + 1 < len(waypoint_coords):
                        dist_to_end = ((stop_coord[0] - waypoint_coords[seg_idx + 1][0])**2 +
                                      (stop_coord[1] - waypoint_coords[seg_idx + 1][1])**2)**0.5
                        score = min(dist_to_start, dist_to_end)
                    else:
                        score = dist_to_start
                else:
                    score = float('inf')

                if score < best_score:
                    best_score = score
                    best_segment = seg_idx

            stop_segments[best_segment].append(stop)

        # Sort stops within each segment by their order field
        for seg_idx in stop_segments:
            stop_segments[seg_idx].sort(key=lambda s: s.get('order', 0))

        # Build final navigation order: segment0 stops, hub1, segment1 stops, hub2, ...
        for seg_idx in range(len(sorted_hubs) + 1):
            # Add stops in this segment
            for stop in stop_segments[seg_idx]:
                navigation_stops.append(stop)

            # Add hub after this segment (if not the last segment)
            if seg_idx < len(sorted_hubs):
                hub = sorted_hubs[seg_idx]
                hub_waypoint = {
                    "id": f"hub_{hub['id']}",
                    "name": f"Hub {hub['order']}",
                    "address": f"Optimization waypoint {hub['order']}",
                    "latitude": hub['latitude'],
                    "longitude": hub['longitude'],
                    "is_hub": True
                }
                navigation_stops.append(hub_waypoint)
    else:
        # No hubs, just use stops in order
        navigation_stops.extend(stops)

    if len(navigation_stops) < 2:
        return {"error": "Need at least 2 points for navigation (current location + 1 stop)", "stops": stops}

    # Mapbox limit is 25 waypoints per request
    MAX_WAYPOINTS = 25

    async def fetch_route_chunk(chunk_stops: List[dict]) -> Optional[dict]:
        """Fetch route for a chunk of stops using OSRM (primary) or Mapbox (fallback)"""
        coordinates = ";".join([f"{s['longitude']},{s['latitude']}" for s in chunk_stops])

        # --- Primary: OSRM ---
        if _osrm_enabled():
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.get(
                        f"{OSRM_URL}/route/v1/driving/{coordinates}",
                        params={"overview": "full", "geometries": "geojson", "steps": "true"},
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        if data.get("code") == "Ok" and data.get("routes"):
                            return data["routes"][0]
            except Exception as e:
                _osrm_log_failure("OSRM navigation chunk failed", e)

        # --- Fallback: Mapbox ---
        if not MAPBOX_TOKEN:
            return None

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"https://api.mapbox.com/directions/v5/mapbox/driving/{coordinates}",
                    params={
                        "access_token": MAPBOX_TOKEN,
                        "geometries": "geojson",
                        "overview": "full",
                        "steps": "true",
                    },
                    timeout=30.0
                )

                if response.status_code == 200:
                    data = response.json()
                    if data.get("routes") and len(data["routes"]) > 0:
                        return data["routes"][0]
            except Exception as e:
                logger.error(f"Route chunk fetch error: {e}")

        return None

    # Split stops into chunks with overlap (last point of chunk N = first point of chunk N+1)
    chunks = []
    for i in range(0, len(navigation_stops), MAX_WAYPOINTS - 1):
        chunk = navigation_stops[i:i + MAX_WAYPOINTS]
        if len(chunk) >= 2:
            chunks.append(chunk)

    # Fetch all chunks
    all_legs = []
    total_distance = 0
    total_duration = 0
    all_geometry_coords = []

    global_stop_index = 0

    for chunk_idx, chunk in enumerate(chunks):
        route_data = await fetch_route_chunk(chunk)

        if not route_data:
            # If a chunk fails, create placeholder legs
            for i in range(len(chunk) - 1):
                from_stop = chunk[i]
                to_stop = chunk[i + 1]
                all_legs.append({
                    "leg_index": global_stop_index,
                    "from_stop": from_stop,
                    "to_stop": to_stop,
                    "distance": 0,
                    "duration": 0,
                    "summary": "Route unavailable",
                    "steps": []
                })
                global_stop_index += 1
            continue

        # Process route data
        total_distance += route_data.get("distance", 0)
        total_duration += route_data.get("duration", 0)

        # Add geometry coordinates
        if route_data.get("geometry", {}).get("coordinates"):
            # Skip first coordinate if not first chunk (to avoid duplicates)
            coords = route_data["geometry"]["coordinates"]
            if chunk_idx > 0 and all_geometry_coords:
                coords = coords[1:]
            all_geometry_coords.extend(coords)

        # Process legs
        for leg_idx, leg in enumerate(route_data.get("legs", [])):
            from_stop_idx = leg_idx
            to_stop_idx = leg_idx + 1

            from_stop = chunk[from_stop_idx] if from_stop_idx < len(chunk) else None
            to_stop = chunk[to_stop_idx] if to_stop_idx < len(chunk) else None

            steps = []
            for step in leg.get("steps", []):
                maneuver = step.get("maneuver", {})
                voice_instructions = step.get("voiceInstructions", [])
                voice_text = voice_instructions[0].get("announcement", "") if voice_instructions else ""

                steps.append({
                    "distance": step.get("distance", 0),
                    "duration": step.get("duration", 0),
                    "instruction": maneuver.get("instruction", ""),
                    "type": maneuver.get("type", ""),
                    "modifier": maneuver.get("modifier", ""),
                    "location": maneuver.get("location", []),
                    "name": step.get("name", ""),
                    "geometry": step.get("geometry", {}),
                    "voice_instruction": voice_text
                })

            all_legs.append({
                "leg_index": global_stop_index,
                "from_stop": from_stop,
                "to_stop": to_stop,
                "distance": leg.get("distance", 0),
                "duration": leg.get("duration", 0),
                "summary": leg.get("summary", ""),
                "steps": steps
            })
            global_stop_index += 1

    # Build combined geometry
    combined_geometry = {
        "type": "LineString",
        "coordinates": all_geometry_coords
    } if all_geometry_coords else None

    # Calculate completion stats
    completed_count = len(completed_stops)

    return {
        "total_distance": total_distance,
        "total_duration": total_duration,
        "geometry": combined_geometry,
        "legs": all_legs,
        "stops": stops + completed_stops,
        "completed_count": completed_count,
        "total_stops": len(all_user_stops),
        "chunks_used": len(chunks)
    }


@router.get("/mapbox-token")
async def get_mapbox_token(response: Response):
    """Return Mapbox token for frontend use"""
    from server import MAPBOX_TOKEN  # noqa: WPS433
    response.headers["Cache-Control"] = "private, max-age=3600"  # 1h — rarely changes
    return {"token": MAPBOX_TOKEN}
