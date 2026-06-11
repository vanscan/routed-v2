"""Optimization pipeline helpers extracted from server.py.

These are higher-level orchestration functions that sit above the individual
solver algorithms: Mapbox/Generoute API optimizers, traffic-time scaling,
hub-segment assignment, and the per-segment optimization dispatcher.

All solver functions and config vars are imported from `server` at call time
(deferred imports inside function bodies) to avoid circular imports — server.py
imports this module, so we cannot import server at module load time.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import List, Optional

import httpx
from haversine import Unit, haversine

logger = logging.getLogger("server")


async def mapbox_optimize(
    stops: List[dict],
    current_latitude: float = None,
    current_longitude: float = None,
) -> List[dict]:
    """Use Mapbox Optimization API for route optimization.

    Mapbox Optimization API handles up to 12 coordinates per request.
    For larger routes, we batch them.
    """
    from server import MAPBOX_TOKEN  # noqa: WPS433
    if not MAPBOX_TOKEN:
        raise ValueError("Mapbox token not configured")

    if len(stops) < 2:
        return stops

    all_coords = []

    if current_latitude and current_longitude:
        all_coords.append(f"{current_longitude},{current_latitude}")

    for stop in stops:
        all_coords.append(f"{stop['longitude']},{stop['latitude']}")

    if len(all_coords) <= 12:
        coordinates = ";".join(all_coords)

        _ = 0 if current_latitude else "any"

        params = {
            "access_token": MAPBOX_TOKEN,
            "source": "first",
            "destination": "last",
            "roundtrip": "false",
            "geometries": "geojson",
            "overview": "full"
        }

        url = f"https://api.mapbox.com/optimized-trips/v1/mapbox/driving/{coordinates}"

        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, timeout=30)

            if response.status_code == 200:
                data = response.json()

                if data.get("code") == "Ok" and data.get("waypoints"):
                    waypoints = data["waypoints"]
                    ordered_waypoints = sorted(waypoints, key=lambda w: w["waypoint_index"])

                    offset = 1 if current_latitude else 0
                    optimized_stops = []

                    for wp in ordered_waypoints:
                        original_idx = wp["waypoint_index"] - offset
                        if original_idx >= 0 and original_idx < len(stops):
                            optimized_stops.append(stops[original_idx])

                    return optimized_stops

            logger.warning("Mapbox Optimization API error: %s - %s", response.status_code, response.text[:200])

    else:
        chunk_size = 10
        optimized_chunks = []

        for i in range(0, len(stops), chunk_size):
            chunk = stops[i:i + chunk_size]

            if len(chunk) >= 2:
                coords = ";".join([f"{s['longitude']},{s['latitude']}" for s in chunk])

                params = {
                    "access_token": MAPBOX_TOKEN,
                    "roundtrip": "false",
                    "geometries": "geojson"
                }

                url = f"https://api.mapbox.com/optimized-trips/v1/mapbox/driving/{coords}"

                async with httpx.AsyncClient() as client:
                    response = await client.get(url, params=params, timeout=30)

                    if response.status_code == 200:
                        data = response.json()

                        if data.get("code") == "Ok" and data.get("waypoints"):
                            waypoints = data["waypoints"]
                            ordered_waypoints = sorted(waypoints, key=lambda w: w["waypoint_index"])

                            chunk_optimized = []
                            for wp in ordered_waypoints:
                                if wp["waypoint_index"] < len(chunk):
                                    chunk_optimized.append(chunk[wp["waypoint_index"]])

                            optimized_chunks.extend(chunk_optimized)
                        else:
                            optimized_chunks.extend(chunk)
                    else:
                        optimized_chunks.extend(chunk)
            else:
                optimized_chunks.extend(chunk)

        return optimized_chunks

    return stops


async def generoute_optimize(
    stops: List[dict],
    current_latitude: float = None,
    current_longitude: float = None,
) -> List[dict]:
    """Use Generoute API for route optimization."""
    from server import GENEROUTE_API_KEY  # noqa: WPS433
    if not GENEROUTE_API_KEY:
        raise ValueError("Generoute API key not configured")

    if len(stops) < 2:
        return stops

    MAX_LOCATIONS = 99

    try:
        if len(stops) > MAX_LOCATIONS:
            logger.info(f"Chunking {len(stops)} stops for Generoute (max {MAX_LOCATIONS} per request)")

            chunks = []
            for i in range(0, len(stops), MAX_LOCATIONS):
                chunks.append(stops[i:i + MAX_LOCATIONS])

            all_optimized = []
            for chunk_idx, chunk in enumerate(chunks):
                chunk_start_lat = None
                chunk_start_lng = None
                if chunk_idx == 0 and current_latitude and current_longitude:
                    chunk_start_lat = current_latitude
                    chunk_start_lng = current_longitude
                elif all_optimized:
                    last_stop = all_optimized[-1]
                    chunk_start_lat = last_stop['latitude']
                    chunk_start_lng = last_stop['longitude']

                try:
                    optimized_chunk = await generoute_optimize(chunk, chunk_start_lat, chunk_start_lng)
                    all_optimized.extend(optimized_chunk)
                except Exception as e:
                    logger.warning(f"Chunk {chunk_idx} optimization failed: {e}, using original order")
                    all_optimized.extend(chunk)

            return all_optimized

        locations = []

        if current_latitude and current_longitude:
            locations.append({
                "coordinates": [current_longitude, current_latitude],
                "title": "Current Location",
                "data": {"id": "current_location"}
            })

        for stop in stops:
            locations.append({
                "coordinates": [stop['longitude'], stop['latitude']],
                "title": stop.get('address', stop.get('name', '')),
                "data": {"id": stop.get('id', str(uuid.uuid4()))}
            })

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.generoute.io/v1/trip",
                headers={
                    "Authorization": f"Bearer {GENEROUTE_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "region": "AU",
                    "locations": locations
                },
                timeout=30.0
            )

            if response.status_code != 200:
                logger.error(f"Generoute API error: {response.status_code} - {response.text}")
                raise ValueError(f"Generoute API error: {response.status_code}")

            result = response.json()

            trips = result.get('trips', [])
            if not trips or len(trips) == 0:
                logger.warning("Generoute returned no trips, using original order")
                return stops

            optimized_waypoints = trips[0].get('waypoints', [])

            if not optimized_waypoints:
                logger.warning("Generoute returned no optimized waypoints, using original order")
                return stops

            optimized_waypoints.sort(key=lambda w: w.get('waypoint_order', 0))

            id_to_stop = {stop.get('id'): stop for stop in stops}
            optimized_stops = []

            for opt_wp in optimized_waypoints:
                loc_id = opt_wp.get('data', {}).get('id')

                if loc_id == "current_location":
                    continue

                if loc_id and loc_id in id_to_stop:
                    optimized_stops.append(id_to_stop[loc_id])
                else:
                    opt_coords = opt_wp.get('coordinates', opt_wp.get('waypoint_location', []))
                    if len(opt_coords) == 2:
                        for stop in stops:
                            if stop not in optimized_stops:
                                if abs(stop['longitude'] - opt_coords[0]) < 0.0001 and \
                                   abs(stop['latitude'] - opt_coords[1]) < 0.0001:
                                    optimized_stops.append(stop)
                                    break

            for stop in stops:
                if stop not in optimized_stops:
                    optimized_stops.append(stop)

            logger.info(f"Generoute optimization succeeded: {len(optimized_stops)} stops optimized")
            return optimized_stops

    except httpx.TimeoutException:
        logger.error("Generoute API timeout")
        raise ValueError("Generoute API timeout - try again later")
    except Exception as e:
        logger.error(f"Generoute optimization error: {e}")
        raise ValueError(f"Generoute optimization failed: {str(e)}")


def _traffic_multiplier(hour: int) -> float:
    """Return a duration multiplier based on time-of-day traffic patterns."""
    if 7 <= hour < 9:
        return 1.35
    elif 16 <= hour < 18:
        return 1.40
    elif 15 <= hour < 16:
        return 1.20
    elif 9 <= hour < 10:
        return 1.15
    elif 10 <= hour < 15:
        return 1.05
    elif 5 <= hour < 7:
        return 1.10
    elif 18 <= hour < 20:
        return 1.15
    else:
        return 1.00


def apply_traffic_multiplier(matrix: List[List[int]], hour: int) -> List[List[int]]:
    """Apply time-of-day traffic multiplier to a duration matrix."""
    m = _traffic_multiplier(hour)
    if m == 1.0:
        return matrix
    return [
        [max(1, int(round(cell * m))) for cell in row]
        for row in matrix
    ]


def assign_stops_to_hub_segments(
    stops: List[dict],
    hubs: List[dict],
    current_location: dict = None,
) -> List[List[dict]]:
    """Assign each stop to the nearest hub segment."""
    if not hubs:
        return [stops]

    sorted_hubs = sorted(hubs, key=lambda h: h['order'])

    waypoints = []
    if current_location:
        waypoints.append({
            'latitude': current_location['latitude'],
            'longitude': current_location['longitude'],
            'is_hub': False
        })

    for hub in sorted_hubs:
        waypoints.append({
            'latitude': hub['latitude'],
            'longitude': hub['longitude'],
            'is_hub': True,
            'hub_id': hub['id']
        })

    num_segments = len(sorted_hubs) + (1 if current_location else 0)
    segments = [[] for _ in range(num_segments)]

    for stop in stops:
        stop_coord = (stop['latitude'], stop['longitude'])

        best_segment = 0
        best_score = float('inf')

        for seg_idx in range(num_segments):
            if seg_idx < len(waypoints):
                start_wp = waypoints[seg_idx]
                start_coord = (start_wp['latitude'], start_wp['longitude'])
                dist_to_start = haversine(stop_coord, start_coord, unit=Unit.KILOMETERS)

                if seg_idx + 1 < len(waypoints):
                    end_wp = waypoints[seg_idx + 1]
                    end_coord = (end_wp['latitude'], end_wp['longitude'])
                    dist_to_end = haversine(stop_coord, end_coord, unit=Unit.KILOMETERS)
                    score = min(dist_to_start, dist_to_end)
                else:
                    score = dist_to_start
            else:
                score = float('inf')

            if score < best_score:
                best_score = score
                best_segment = seg_idx

        segments[best_segment].append(stop)

    return segments


def optimize_segment(
    stops: List[dict],
    algorithm: str,
    start_point: dict = None,
    end_point: dict = None,
) -> List[dict]:
    """Optimize a single segment of stops using the specified algorithm."""
    from server import (  # noqa: WPS433
        ALNS_AVAILABLE,
        _indices_by_identity,
        alns_hybrid_optimize,
        calculate_distance_matrix,
        genetic_algorithm_optimize,
        nearest_neighbor_optimize,
        ortools_optimize,
        simulated_annealing_optimize,
        two_opt_improve,
    )

    if len(stops) <= 1:
        return stops

    working_stops = []
    start_idx = 0

    if start_point:
        anchor_start = {
            'id': f"anchor_start_{start_point.get('id', 'loc')}",
            'latitude': start_point['latitude'],
            'longitude': start_point['longitude'],
            'is_anchor': True
        }
        working_stops.append(anchor_start)
        start_idx = 0

    working_stops.extend(stops)

    if end_point:
        anchor_end = {
            'id': f"anchor_end_{end_point.get('id', 'loc')}",
            'latitude': end_point['latitude'],
            'longitude': end_point['longitude'],
            'is_anchor': True
        }
        working_stops.append(anchor_end)

    distance_matrix = calculate_distance_matrix(working_stops)

    if algorithm == 'alns':
        try:
            optimized = alns_hybrid_optimize(
                working_stops,
                distance_matrix,
                start_index=start_idx,
                time_limit_seconds=6,
            )
        except Exception as exc:
            logger.warning("ALNS segment optimization failed, using 2-Opt fallback: %s", exc)
            nn_result = nearest_neighbor_optimize(working_stops, distance_matrix, start_idx)
            route_indices = _indices_by_identity(working_stops, nn_result)
            improved_indices = two_opt_improve(route_indices, distance_matrix)
            optimized = [working_stops[i] for i in improved_indices]
    elif algorithm == 'ortools':
        try:
            optimized = ortools_optimize(
                working_stops,
                distance_matrix,
                start_index=start_idx,
                time_limit_seconds=8,
            )
        except Exception as exc:
            logger.warning("OR-Tools segment optimization failed, using 2-Opt fallback: %s", exc)
            nn_result = nearest_neighbor_optimize(working_stops, distance_matrix, start_idx)
            route_indices = _indices_by_identity(working_stops, nn_result)
            improved_indices = two_opt_improve(route_indices, distance_matrix)
            optimized = [working_stops[i] for i in improved_indices]
    elif algorithm in ['two_opt', 'auto'] or len(working_stops) <= 10:
        nn_result = nearest_neighbor_optimize(working_stops, distance_matrix, start_idx)
        route_indices = _indices_by_identity(working_stops, nn_result)
        improved_indices = two_opt_improve(route_indices, distance_matrix)
        optimized = [working_stops[i] for i in improved_indices]
    elif algorithm == 'simulated_annealing':
        optimized = simulated_annealing_optimize(working_stops, distance_matrix, start_idx)
    elif algorithm == 'genetic':
        optimized = genetic_algorithm_optimize(working_stops, distance_matrix, start_idx)
    else:
        optimized = nearest_neighbor_optimize(working_stops, distance_matrix, start_idx)

    result = [s for s in optimized if not s.get('is_anchor')]
    return result
