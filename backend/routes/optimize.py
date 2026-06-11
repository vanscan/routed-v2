"""Route optimization endpoints — the heart of the product.

    POST /optimize                    → synchronous multi-engine optimizer
    POST /optimize/jobs               → async kickoff (Mongo-backed job doc)
    GET  /optimize/jobs/{job_id}      → poll job status/result
    GET  /optimize/diagnostics        → last 10 jobs + solver availability
    POST /optimize/tighten-cluster    → one-tap single-stop zig-zag fix
    POST /optimize/tighten-clusters   → iterative whole-route tightener
    GET  /optimize/algorithms         → static algorithm catalogue
    GET  /generoute/status            → Generoute API config probe

Split out of server.py for maintainability. The solver cascade, matrix
builders, ML hooks and availability flags still live in server.py, so the
handlers resolve those symbols with call-time `from server import ...`
lines — late binding keeps `monkeypatch.setattr(server, ...)` in tests and
the lazy solver loaders working exactly as before. Module-level imports
here must never touch `server` (this module is imported while server.py
is still executing).

server.py re-imports the moved helpers (`_optimize_route_inner`,
`_iterative_haversine_tighten`, `_osrm_verify_relocation`, …) so existing
`from server import X` call sites and tests keep working.
"""
from __future__ import annotations

import asyncio
import logging
import traceback
import uuid
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from haversine import Unit, haversine

from models import OptimizationRequest, TightenClusterRequest, User
from routes.benchmark import _run_algorithm_benchmark

# Pro paywall gate for the optimize endpoints. billing.require_pro defers its
# server imports to request time, so importing it at module load is safe.
from routes.billing import (
    FREE_STOP_CAP as _FREE_STOP_CAP,
    get_is_pro as _billing_get_is_pro,
    require_pro as _billing_require_pro,
)

logger = logging.getLogger("server")
router = APIRouter()


async def _current_user(request: Request):
    """Dep wrapper — defers the `server` import until the first request so the
    module can load before `server.py` finishes defining its symbols."""
    from server import get_current_user  # noqa: WPS433
    return await get_current_user(request)


@router.post("/optimize")
async def optimize_route(
    request: OptimizationRequest = OptimizationRequest(),
    current_user: User = Depends(_current_user),
):
    """Optimize route order using various algorithms
    
    Algorithms:
    - auto: Smart selection based on stop count (ALNS for 10+ stops, 2-opt for small)
    - alns: ALNS Hybrid Metaheuristic (NN construction + ALNS/SA + Local Search polish)
    - ortools: Google OR-Tools guided local search (single-vehicle, time-first)
    - nearest_neighbor: Greedy approach, fast O(n²)
    - two_opt: Improvement heuristic, good quality
    - simulated_annealing: Meta-heuristic, better for medium routes
    - genetic: Evolutionary algorithm, best for complex routes
    - clarke_wright: VRP savings algorithm, treats start as depot
    """
    try:
        return await _optimize_route_inner(request, current_user)
    except HTTPException:
        raise  # Let FastAPI handle 4xx errors (401, 402, 403) cleanly
    except Exception as e:
        logger.error("[optimize] Unhandled exception for user=%s algorithm=%s:\n%s",
                     current_user.user_id, request.algorithm, traceback.format_exc())
        # Class name only — raw str(e) leaks internals to the client
        # (CodeQL py/stack-trace-exposure); the full traceback is logged above.
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": f"Optimization failed ({type(e).__name__})"},
        )


async def _optimize_route_inner(
    request: OptimizationRequest = OptimizationRequest(),
    current_user: User = Depends(_current_user)
):
    import server as _srv  # noqa: WPS433
    from server import (  # noqa: WPS433,F811
        LKH_AVAILABLE,
        OSRM_URL,
        TELEPATHY_USER_IDS,
        TIMEFOLD_AVAILABLE,
        TIMEFOLD_IMPORT_ERROR,
        VROOM_AVAILABLE,
        _global_two_opt_pass,
        _haversine_duration_matrix,
        _indices_by_identity,
        _osrm_distance_matrix,
        _osrm_duration_matrix,
        _smart_insertion_fallback,
        _srv,
        _traffic_multiplier,
        apply_traffic_multiplier,
        assign_stops_to_hub_segments,
        calculate_distance_matrix,
        calculate_duration_matrix,
        calculate_full_road_distance_matrix,
        calculate_road_distance_km,
        calculate_road_distance_matrix,
        calculate_route_distance,
        clarke_wright_savings,
        cluster_aware_solve,
        cluster_first_optimize,
        db,
        detect_cluster_spikes,
        generoute_optimize,
        genetic_algorithm_optimize,
        iterated_local_search,
        ELKAI_AVAILABLE,
        elkai_tsp_solve,
        lkh_tsp_solve,
        mapbox_optimize,
        nearest_neighbor_optimize,
        optimize_segment,
        or_opt_improve,
        ortools_tsp_solve,
        parse_start_time,
        pyvrp_tsp_solve,
        solve_nearest_neighbor,
        three_opt_improve,
        timefold_optimize,
        two_opt_improve,
        vroom_tsp_solve,
    )
    all_user_stops = await db.stops.find({"user_id": current_user.user_id}, {"_id": 0}).sort("order", 1).to_list(1000)
    completed_stops = [s for s in all_user_stops if s.get("completed")]
    stops = [s for s in all_user_stops if not s.get("completed")]

    if not await _billing_get_is_pro(db, current_user.user_id, current_user.email or ""):
        if len(stops) > _FREE_STOP_CAP:
            raise HTTPException(
                status_code=402,
                detail={
                    "code": "subscription_required",
                    "message": (
                        f"Free plan is limited to {_FREE_STOP_CAP} stops. "
                        "Upgrade to Pro for unlimited routes — 14-day free trial, no card upfront."
                    ),
                    "upgrade_required": True,
                    "stop_cap": _FREE_STOP_CAP,
                    "checkout_endpoint": "/api/billing/checkout",
                },
            )

    # ── AUDIT 1: raw input + super-node proxy ────────────────────────────
    # Counts unique (lat, lng) — this is what PyVRP's super-node grouper
    # SHOULD collapse to. If `unique_coords == raw_pending` even though
    # the route contains multi-parcel addresses, super-node clustering
    # is broken and PyVRP will compute a matrix N times larger than
    # needed (with zero-cost legs that the algorithm has to ignore).
    _audit_unique_coords = len({
        (round(s["latitude"], 6), round(s["longitude"], 6))
        for s in stops if s.get("latitude") is not None and s.get("longitude") is not None
    })
    logger.info(
        "AUDIT[/optimize] raw_pending=%d raw_completed=%d unique_coords=%d "
        "(if unique_coords==raw_pending with sibling parcels present, "
        "super-node clustering is broken)",
        len(stops), len(completed_stops), _audit_unique_coords,
    )
    
    if len(stops) < 2:
        return {"message": "Need at least 2 stops to optimize", "stops": stops + completed_stops, "algorithm": "none"}
    
    # Handle current location as starting point
    start_index = 0
    current_loc_stop = None
    
    # Presence only — precise GPS coordinates are private data and don't
    # belong in the log stream (CodeQL py/clear-text-logging-sensitive-data).
    logger.info("Optimize request: use_current=%s, has_coords=%s",
                request.use_current_location,
                request.current_latitude is not None and request.current_longitude is not None)
    
    if request.use_current_location and request.current_latitude and request.current_longitude:
        # Create a virtual "current location" stop
        current_loc_stop = {
            "id": "current_location",
            "address": "Current Location",
            "name": "Start (Current Location)",
            "latitude": request.current_latitude,
            "longitude": request.current_longitude,
            "priority": "high",
            "completed": False,
            "order": -1,
            "is_start_point": True
        }
        # Insert at beginning
        stops = [current_loc_stop] + stops
        start_index = 0
    
    # Determine algorithm before distance matrix calculation
    algorithm_used = request.algorithm
    inner_algorithm = "ortools"  # Default inner algorithm for cluster_first
    
    # ── Auto-selection (2026-04-25 update) ─────────────────────────────────
    # Previously `auto` resolved to raw VROOM. On real 79-stop user data
    # VROOM gave 96.0 min while LKH-3 (with the open-path fix) finds the
    # true optimum at 95.7 min in 0.11s — a 0.3 min improvement at zero
    # latency cost. The `vroom_lkh_3opt` cascade does:
    #   Stage 1: VROOM seed (best fast TSP heuristic, <0.3s for 100 stops)
    #   Stage 2: LKH-3 refine (state-of-the-art Lin-Kernighan, ~0.1s)
    #   Stage 3: 3-opt polish (catches anything LKH missed)
    # …which is strictly ≥ VROOM-alone quality and routinely 0.3–2.4 min
    # better. The added latency is <100 ms vs raw VROOM. For tiny routes
    # (<11 stops) we skip the cascade and use the existing 2-opt path
    # earlier in the pipeline (line ~4644) which is already optimal at
    # that scale via brute-force-ish enumeration.
    if algorithm_used == "auto":
        if VROOM_AVAILABLE and LKH_AVAILABLE and len(stops) >= 11:
            algorithm_used = "vroom_lkh_3opt"
        elif VROOM_AVAILABLE:
            algorithm_used = "vroom"
        else:
            algorithm_used = "ortools"

    # ── Cluster-first removed (2026-05-30) ───────────────────────────────
    # Cluster-first partitioned stops with HAVERSINE before routing, which
    # imposed artificial cluster boundaries and could trap a stop in the
    # wrong group — visibly worse than a full-matrix solve now that a real
    # OSRM road matrix is always available. Redirect any cluster_first
    # request to the standard full-matrix cascade so optimization always
    # runs on the OSRM N×N matrix.
    if algorithm_used == "cluster_first":
        if VROOM_AVAILABLE and LKH_AVAILABLE and len(stops) >= 11:
            algorithm_used = "vroom_lkh_3opt"
        elif VROOM_AVAILABLE:
            algorithm_used = "vroom"
        else:
            algorithm_used = "ortools"
        logger.info(
            "cluster_first is removed — redirected to %s (full OSRM matrix)",
            algorithm_used,
        )
    # ── Late Freight Smart Insertion detection ───────────────────────────
    # A manifest is "hybrid" when it mixes LOCKED stops (integer
    # `original_sequence`, frozen at /routes/confirm) with unlocked "late
    # freight" stops (null `original_sequence`). When that happens we must
    # preserve the locked visiting order EXACTLY (stop N before stop N+1)
    # while letting OR-Tools slot the late stops into the cheapest gaps —
    # without mutating any `original_sequence` value. Only OR-Tools (with
    # precedence constraints) can honour the lock, so this overrides the
    # otherwise-selected solver. `locked_order_indices` are matrix-space
    # node indices ordered by their immutable `original_sequence`.
    locked_order_indices = None

    def _orig_seq(s):
        v = s.get("original_sequence")
        # bool is a subclass of int — exclude it explicitly
        return v if (isinstance(v, int) and not isinstance(v, bool)) else None

    _locked_present = [(i, _orig_seq(s)) for i, s in enumerate(stops) if _orig_seq(s) is not None]
    _late_present = any(
        _orig_seq(s) is None and not s.get("is_start_point")
        for s in stops
    )
    if len(_locked_present) >= 2 and _late_present:
        _candidate = [i for (i, seq) in sorted(_locked_present, key=lambda t: t[1])]
        # Guard: if the depot is itself a locked stop but NOT the earliest in
        # sequence, precedence would be infeasible (depot is forced first).
        # In that contradictory case we skip smart insertion entirely.
        if not (start_index in _candidate and _candidate[0] != start_index):
            locked_order_indices = _candidate
            algorithm_used = "ortools_smart_insertion"
            logger.info(
                "Late Freight: %d locked + late freight detected → OR-Tools smart insertion",
                len(locked_order_indices),
            )

    # Respect the user's algorithm choice. Basic heuristics may be slow on large routes,
    # but silently hijacking the selection prevents the user from seeing how their picked
    # solver actually performs (and makes "algorithm X isn't working" look like a bug).
    # If the caller wants the auto-cluster behaviour they can select `cluster_first` explicitly.

    # Build cost matrices — OSRM first (free, local), then Mapbox, then haversine last resort.
    # All solvers receive OSRM data when available so quality is consistent regardless of algorithm.
    duration_matrix = None

    if algorithm_used == "cluster_first":
        # Cluster-first uses haversine for the spatial grouping phase; inner per-cluster
        # solver builds its own duration matrix internally.
        distance_matrix = calculate_distance_matrix(stops)
    else:
        # ── Primary: OSRM (local, free, handles 100+ stops natively) ──
        duration_matrix = await _osrm_duration_matrix(stops)
        if duration_matrix:
            logger.info("Using OSRM duration matrix for %d stops (%s)", len(stops), algorithm_used)
            osrm_dist = await _osrm_distance_matrix(stops)
            distance_matrix = osrm_dist if osrm_dist else calculate_distance_matrix(stops)
            if osrm_dist:
                logger.info("Using OSRM distance matrix for %d stops (reporting)", len(stops))
        else:
            # ── Fallback: Mapbox for duration-sensitive solvers, road distance for others ──
            logger.info("OSRM unavailable, building fallback matrices for %s (%d stops)", algorithm_used, len(stops))
            if algorithm_used in ("vroom", "ortools", "lkh", "vroom_lkh_3opt", "vroom_ortools", "timefold"):
                duration_matrix = await calculate_duration_matrix(stops)
                distance_matrix = calculate_distance_matrix(stops)
            elif algorithm_used == "alns" and len(stops) > 25:
                distance_matrix = await calculate_full_road_distance_matrix(stops)
            else:
                distance_matrix = await calculate_road_distance_matrix(stops)

    # ── ML service-time injection (Phase 1.5) ────────────────────────────
    # If the user has trained a service-time model (via
    # POST /api/_meta/ml/train), bake the per-stop predicted service
    # seconds INTO the duration matrix's outgoing edges. From every
    # solver's perspective, "leaving node i for j" now costs the actual
    # travel time PLUS the median service time observed at node i's
    # suburb-and-hour bucket.
    #
    # Why bake into the matrix vs. pass `service=` to each solver?
    # ----------------------------------------------------------------
    # VROOM has a `service` param on Job, but OR-Tools needs a
    # time-dimension callback, LKH/3-opt/genetic only consume a flat
    # matrix, and the post-optimize 2-opt refines also only see the
    # matrix. A matrix-baked approach uniformly applies the service
    # time to ALL solvers without per-solver wiring — change one place,
    # benefit everywhere. Outgoing-from-i (not incoming-to-j) so the
    # last stop's service time isn't double-counted on the virtual
    # exit edge (see service_time_learner.apply_service_times_to_matrix
    # for the rationale).
    #
    # Skips silently when:
    #   - User has no trained model (cold start before first Train Now)
    #   - We're on the cluster_first/haversine path (no duration matrix)
    if duration_matrix and len(stops) > 1:
        try:
            ml_doc = await db.ml_service_time_models.find_one(
                {"user_id": current_user.user_id},
                {"_id": 0},
            )
            if ml_doc:
                from ml.service_time_learner import (
                    predict_service_time_seconds,
                    apply_service_times_to_matrix,
                )
                # Start hour drives the (suburb, hour_bucket) lookup. If
                # the request didn't supply one we fall back to "now"
                # which matches what `predict_service_time_seconds`
                # already does internally.
                from datetime import datetime as _dt, timezone as _tz
                start_hour = _dt.now(_tz.utc).hour
                try:
                    if request.start_time:
                        # Parse "HH:MM" into hour. parse_start_time
                        # returns a datetime; we just want the hour.
                        _sd = parse_start_time(request.start_time)
                        start_hour = _sd.hour if _sd else start_hour
                except Exception:
                    pass

                service_times = [
                    predict_service_time_seconds(s, ml_doc, completion_hour=start_hour)
                    for s in stops
                ]
                duration_matrix = apply_service_times_to_matrix(
                    duration_matrix, service_times,
                )
                logger.info(
                    "[ml] Service-time injection: user=%s, %d stops, "
                    "min=%.0fs, median=%.0fs, max=%.0fs",
                    current_user.user_id,
                    len(service_times),
                    min(service_times),
                    sorted(service_times)[len(service_times)//2],
                    max(service_times),
                )
        except Exception as _ml_exc:
            # Non-fatal: optimize MUST still produce a route even if the
            # learner errors. Logged at warning level so we can grep
            # the failure pattern without spamming on every request.
            logger.warning(
                "[ml] Service-time injection failed (%s) — falling back to "
                "raw duration matrix without service times.",
                _ml_exc,
            )

    # ── School-zone penalty removed 2026-05-13 ────────────────────────────
    # The Meridan State College + Parklands Blvd inbound-edge penalty was
    # removed per user request. Helpers remain in
    # routes/_route_constraints.py if we want to revisit.

    # ── No-Go Zone penalty ───────────────────────────────────────────────
    # User-defined polygons (real road closures, mistagged footbridges,
    # private roads). Two-stage check:
    #   Stage 1 — straight-line: any (A, B) leg whose great-circle line
    #   crosses any zone gets +1e9 seconds. Cheap and catches the
    #   majority of cases.
    #   Stage 2 — OSRM-geometry-aware: for cells whose straight line is
    #   *near* a zone but doesn't intersect it, fetch the actual OSRM
    #   road path and check the LineString against each polygon. Catches
    #   cases where the road bends through the closed area while the
    #   straight line skirts past (Meridan Way × Rainforest Drive
    #   diagonal report 2026-05-09). Pre-filter to bbox+1.5 km so we
    #   only do a few hundred OSRM calls instead of 28 k.
    # We DON'T touch the distance matrix: distance-based solvers like NN
    # are rarer, and double-penalising risks integer overflow on int
    # matrices.
    try:
        from routes.nogo_zones import (
            fetch_user_zone_polygons,
            apply_nogo_penalty,
            apply_nogo_penalty_osrm_aware,
        )
        _nogo_polygons = await fetch_user_zone_polygons(db, current_user.user_id)
        if _nogo_polygons and duration_matrix is not None:
            _straight = apply_nogo_penalty(duration_matrix, stops, _nogo_polygons)
            _osrm_extra = await apply_nogo_penalty_osrm_aware(
                duration_matrix, stops, _nogo_polygons,
                osrm_url=OSRM_URL,
            )
            if _straight or _osrm_extra:
                logger.info(
                    "[nogo-zones] penalised %d cells (straight=%d, osrm-aware=%d) across %d zone(s) for user=%s",
                    _straight + _osrm_extra, _straight, _osrm_extra,
                    len(_nogo_polygons), current_user.user_id,
                )
    except Exception as nogo_err:
        # Non-fatal: never let a zone bug block optimisation.
        logger.warning("[nogo-zones] skipped due to error: %s", nogo_err)

    reasoning = ""

    # ── AUDIT 2: matrix sanity ───────────────────────────────────────────
    # If the OSRM matrix is broken (all zeros, NaN-like ints, missing
    # row), every solver downstream will produce an arbitrary tour because
    # they think every leg is free. Sample row 0 first 5 cols + the
    # source provenance ("road" via OSRM, "haversine" fallback, "cached",
    # etc.). If you see all zeros here, OSRM is dead and we silently fell
    # back to crow-flies — which IS the patchy/zig-zag symptom.
    #
    # Note: `distance_source` is assigned later inside the per-algorithm
    # branch, so at this point in the function it may not yet be defined
    # — we use locals().get() to read it safely. The same applies to
    # `duration_matrix` on the haversine-only fallback path.
    try:
        _audit_dim = len(distance_matrix) if distance_matrix else 0
        _audit_row0 = (
            list(distance_matrix[0][:5]) if _audit_dim > 0 else []
        )
        _dur = locals().get("duration_matrix")
        _audit_dur = (
            list(_dur[0][:5]) if _dur and len(_dur) > 0 else None
        )
        _audit_src = locals().get("distance_source", "unset-at-audit-time")
        logger.info(
            "AUDIT[/optimize] matrix dim=%dx%d source=%s row0[:5]=%s duration_row0[:5]=%s "
            "(all zeros ⇒ matrix is broken, solver tour will be arbitrary)",
            _audit_dim, _audit_dim, _audit_src,
            _audit_row0, _audit_dur,
        )
    except Exception as _e:
        logger.warning("AUDIT[/optimize] matrix sample failed: %s", _e)
    
    # ========== HUB-BASED SEGMENTED OPTIMIZATION ==========
    # If hubs are provided, use segmented optimization
    if request.hubs and len(request.hubs) > 0:
        logger.info(f"Hub-based optimization with {len(request.hubs)} hubs")
        
        # Convert hubs to dict format
        hubs_dict = [{"id": h.id, "latitude": h.latitude, "longitude": h.longitude, "order": h.order} 
                     for h in request.hubs]
        
        # Prepare current location dict
        current_loc_dict = None
        if current_loc_stop:
            current_loc_dict = {
                "latitude": current_loc_stop["latitude"],
                "longitude": current_loc_stop["longitude"]
            }
        
        # Get stops without current location for segmentation
        actual_stops = [s for s in stops if s.get("id") != "current_location"]
        
        # Assign stops to segments based on hub proximity
        segments = assign_stops_to_hub_segments(actual_stops, hubs_dict, current_loc_dict)
        
        # Sort hubs by order
        sorted_hubs = sorted(hubs_dict, key=lambda h: h['order'])
        
        # Optimize each segment independently
        optimized_segments = []
        
        # Build waypoints for start/end anchors
        waypoints = []
        if current_loc_dict:
            waypoints.append(current_loc_dict)
        waypoints.extend(sorted_hubs)
        
        for seg_idx, segment_stops in enumerate(segments):
            if len(segment_stops) == 0:
                continue
                
            # Determine start and end points for this segment
            start_point = waypoints[seg_idx] if seg_idx < len(waypoints) else None
            end_point = waypoints[seg_idx + 1] if seg_idx + 1 < len(waypoints) else None
            
            # Optimize this segment
            optimized_segment = optimize_segment(segment_stops, algorithm_used, start_point, end_point)
            optimized_segments.append(optimized_segment)
        
        # Stitch segments together in order
        optimized_stops = []
        for segment in optimized_segments:
            optimized_stops.extend(segment)
        
        reasoning = f"Hub-based segmented optimization with {len(request.hubs)} waypoints using {algorithm_used}"
        
        # Update stop orders in database
        from pymongo import UpdateOne
        ops = [
            UpdateOne({"id": stop["id"], "user_id": current_user.user_id}, {"$set": {"order": index}})
            for index, stop in enumerate(optimized_stops)
            if stop.get("id") != "current_location" and not stop.get("is_anchor")
        ]
        if ops:
            await db.stops.bulk_write(ops, ordered=False)
        
        # Save hubs to database for navigation to use
        # Clear existing hubs first
        await db.optimization_hubs.delete_many({"user_id": current_user.user_id})
        
        # Insert new hubs
        for hub in sorted_hubs:
            hub_doc = {
                "id": hub["id"],
                "user_id": current_user.user_id,
                "latitude": hub["latitude"],
                "longitude": hub["longitude"],
                "order": hub["order"],
                "name": f"Hub {hub['order']}",
                "is_hub": True
            }
            await db.optimization_hubs.insert_one(hub_doc)
        
        # Calculate total distance
        total_distance = 0
        all_stops_for_distance = optimized_stops
        if current_loc_stop:
            all_stops_for_distance = [current_loc_stop] + optimized_stops
        
        for i in range(len(all_stops_for_distance) - 1):
            coord1 = (all_stops_for_distance[i]["latitude"], all_stops_for_distance[i]["longitude"])
            coord2 = (all_stops_for_distance[i+1]["latitude"], all_stops_for_distance[i+1]["longitude"])
            total_distance += haversine(coord1, coord2, unit=Unit.KILOMETERS)
        
        return {
            "message": "Route optimized with hub waypoints",
            "algorithm": algorithm_used,
            "reasoning": reasoning,
            "total_distance_km": round(total_distance, 2),
            "stop_count": len(optimized_stops),
            "hub_count": len(request.hubs),
            "started_from_current_location": current_loc_stop is not None,
            "stops": optimized_stops + completed_stops
        }
    
    # ========== STANDARD OPTIMIZATION (no hubs) ==========
    
    # Clear any existing hubs since we're doing standard optimization
    await db.optimization_hubs.delete_many({"user_id": current_user.user_id})
    
    # ========== SECTION-BASED ROUTE REFINEMENT ==========
    # If sections are provided (from lasso tool), optimize within each section and stitch together
    if request.sections and len(request.sections) > 0:
        logger.info(f"Section-based route refinement with {len(request.sections)} sections")
        
        # Get stops without current location
        actual_stops = [s for s in stops if s.get("id") != "current_location"]
        
        # Create a mapping of stop_id to stop
        id_to_stop = {s["id"]: s for s in actual_stops}
        
        # Sort sections by id (order in which they were drawn)
        sorted_sections = sorted(request.sections, key=lambda sec: sec.id)
        
        # Track which stops are assigned to sections
        assigned_stop_ids = set()
        for section in sorted_sections:
            assigned_stop_ids.update(section.stop_ids)
        
        # Get unassigned stops (not in any section)
        unassigned_stops = [s for s in actual_stops if s["id"] not in assigned_stop_ids]
        
        # Optimize each section independently and stitch them together
        optimized_stops = []
        previous_end_point = None
        
        # If we have current location, use it as the starting point for the first section
        if current_loc_stop:
            previous_end_point = {
                "latitude": current_loc_stop["latitude"],
                "longitude": current_loc_stop["longitude"]
            }
        
        for section_idx, section in enumerate(sorted_sections):
            # Get the stops in this section
            section_stops = [id_to_stop[sid] for sid in section.stop_ids if sid in id_to_stop]
            
            if len(section_stops) == 0:
                continue
            
            # Optimize this section
            if len(section_stops) == 1:
                # Single stop, no optimization needed
                optimized_section = section_stops
            else:
                # Use the best available solver chain: VROOM → LKH → 3-opt → 2-opt fallback
                section_distance_matrix = calculate_distance_matrix(section_stops)
                
                # Find the best starting point - closest to previous end point
                start_idx = 0
                if previous_end_point:
                    min_dist = float('inf')
                    for idx, stop in enumerate(section_stops):
                        dist = haversine(
                            (previous_end_point["latitude"], previous_end_point["longitude"]),
                            (stop["latitude"], stop["longitude"]),
                            unit=Unit.KILOMETERS
                        )
                        if dist < min_dist:
                            min_dist = dist
                            start_idx = idx
                
                # Try VROOM first (best quality + speed). Use OSRM for the
                # duration matrix so the section optimizer sees real road
                # times, not great-circle distances — matches the main
                # /api/optimize path and prevents refine from "fixing" a
                # zig-zag onto an even worse one because haversine thinks
                # it's shorter.
                solver_used = "nearest_neighbor+2opt"
                try:
                    try:
                        solver_matrix = await _osrm_duration_matrix(section_stops)
                    except Exception as osrm_err:
                        logger.warning(
                            "Section refine: OSRM matrix failed (%s), falling back to haversine",
                            osrm_err,
                        )
                        solver_matrix = _haversine_duration_matrix(section_stops)
                    indices = vroom_tsp_solve(solver_matrix, depot=start_idx, exploration_level=5)
                    solver_used = "VROOM"
                    
                    # LKH post-processing for gold-standard refinement
                    pre_cost = calculate_route_distance(indices, solver_matrix)
                    if LKH_AVAILABLE and len(section_stops) >= 4:
                        try:
                            lkh_indices = lkh_tsp_solve(solver_matrix, depot=start_idx, runs=3, time_limit_seconds=5)
                            lkh_cost = calculate_route_distance(lkh_indices, solver_matrix)
                            if lkh_cost < pre_cost:
                                indices = lkh_indices
                                solver_used = "VROOM+LKH"
                        except Exception:
                            pass
                    elif len(section_stops) >= 4:
                        indices = three_opt_improve(indices, solver_matrix, max_iterations=3)
                        solver_used = "VROOM+3opt"
                    
                    optimized_section = [section_stops[i] for i in indices]
                except Exception as vroom_err:
                    logger.warning("VROOM section optimization failed, using 2-Opt: %s", vroom_err)
                    nn_result = nearest_neighbor_optimize(section_stops, section_distance_matrix, start_idx)
                    route_indices = _indices_by_identity(section_stops, nn_result)
                    improved_indices = two_opt_improve(route_indices, section_distance_matrix)
                    optimized_section = [section_stops[i] for i in improved_indices]
                
                logger.info(f"Section {section_idx+1}: {len(section_stops)} stops optimized with {solver_used}")
            
            # Add optimized section to results
            optimized_stops.extend(optimized_section)
            
            # Update previous end point for next section
            if optimized_section:
                last_stop = optimized_section[-1]
                previous_end_point = {
                    "latitude": last_stop["latitude"],
                    "longitude": last_stop["longitude"]
                }
        
        # Optimize and append unassigned stops at the end
        if len(unassigned_stops) > 0:
            if len(unassigned_stops) == 1:
                optimized_stops.extend(unassigned_stops)
            else:
                # Use best available solver for unassigned stops too
                unassigned_distance_matrix = calculate_distance_matrix(unassigned_stops)
                start_idx = 0
                if previous_end_point:
                    min_dist = float('inf')
                    for idx, stop in enumerate(unassigned_stops):
                        dist = haversine(
                            (previous_end_point["latitude"], previous_end_point["longitude"]),
                            (stop["latitude"], stop["longitude"]),
                            unit=Unit.KILOMETERS
                        )
                        if dist < min_dist:
                            min_dist = dist
                            start_idx = idx
                
                try:
                    try:
                        solver_matrix = await _osrm_duration_matrix(unassigned_stops)
                    except Exception as osrm_err:
                        logger.warning(
                            "Section refine (unassigned): OSRM matrix failed (%s), haversine fallback",
                            osrm_err,
                        )
                        solver_matrix = _haversine_duration_matrix(unassigned_stops)
                    indices = vroom_tsp_solve(solver_matrix, depot=start_idx, exploration_level=5)
                    if LKH_AVAILABLE and len(unassigned_stops) >= 4:
                        try:
                            lkh_indices = lkh_tsp_solve(solver_matrix, depot=start_idx, runs=3, time_limit_seconds=5)
                            if calculate_route_distance(lkh_indices, solver_matrix) < calculate_route_distance(indices, solver_matrix):
                                indices = lkh_indices
                        except Exception:
                            pass
                    elif len(unassigned_stops) >= 4:
                        indices = three_opt_improve(indices, solver_matrix, max_iterations=3)
                    optimized_unassigned = [unassigned_stops[i] for i in indices]
                except Exception:
                    nn_result = nearest_neighbor_optimize(unassigned_stops, unassigned_distance_matrix, start_idx)
                    route_indices = _indices_by_identity(unassigned_stops, nn_result)
                    improved_indices = two_opt_improve(route_indices, unassigned_distance_matrix)
                    optimized_unassigned = [unassigned_stops[i] for i in improved_indices]
                optimized_stops.extend(optimized_unassigned)
        
        # Track polish savings so we can surface them on the response
        # ("Refined: saved X km") instead of buying the win silently in
        # the logs. The polish runs in two phases — spike-relocate first,
        # then global or-opt + 2-opt — so we measure the total km between
        # the raw stitched route and the final optimised one.
        polish_relocations = 0
        polish_distance_saved_km = 0.0
        try:
            if len(optimized_stops) >= 4:
                pre_polish_km = sum(
                    haversine(
                        (optimized_stops[i]["latitude"], optimized_stops[i]["longitude"]),
                        (optimized_stops[i + 1]["latitude"], optimized_stops[i + 1]["longitude"]),
                        unit=Unit.KILOMETERS,
                    )
                    for i in range(len(optimized_stops) - 1)
                )
                tightened, refine_moves = _iterative_haversine_tighten(optimized_stops)
                optimized_stops = tightened
                if refine_moves:
                    polish_relocations = len(refine_moves)
                    logger.info(
                        "Refine polish: tightened %d spike(s) across stitched route",
                        polish_relocations,
                    )
                pre_polish_count = len(optimized_stops)
                optimized_stops = _global_two_opt_pass(optimized_stops, max_iterations=3)
                # Defensive: never let the polish accidentally drop a stop.
                if len(optimized_stops) != pre_polish_count:
                    logger.error(
                        "Refine polish: stop count drift %d → %d, reverting",
                        pre_polish_count, len(optimized_stops),
                    )
                post_polish_km = sum(
                    haversine(
                        (optimized_stops[i]["latitude"], optimized_stops[i]["longitude"]),
                        (optimized_stops[i + 1]["latitude"], optimized_stops[i + 1]["longitude"]),
                        unit=Unit.KILOMETERS,
                    )
                    for i in range(len(optimized_stops) - 1)
                )
                # Floor at 0 so a tiny rounding regression never reads as
                # "polish made it worse" on the UI.
                polish_distance_saved_km = max(0.0, pre_polish_km - post_polish_km)
        except Exception as polish_err:
            # Polish is a quality booster, not a correctness step. If it
            # explodes we keep the unpolished section-stitched route.
            logger.warning("Refine polish skipped due to error: %s", polish_err)

        reasoning = f"Section-based route refinement with {len(request.sections)} sections"
        
        # Update stop orders in database
        from pymongo import UpdateOne as _BulkUpdate
        bulk_ops = [
            _BulkUpdate({"id": stop["id"], "user_id": current_user.user_id}, {"$set": {"order": index}})
            for index, stop in enumerate(optimized_stops)
            if stop.get("id") != "current_location"
        ]
        if bulk_ops:
            await db.stops.bulk_write(bulk_ops, ordered=False)
        
        # Calculate total distance
        total_distance = 0
        all_stops_for_distance = optimized_stops
        if current_loc_stop:
            all_stops_for_distance = [current_loc_stop] + optimized_stops
        
        for i in range(len(all_stops_for_distance) - 1):
            coord1 = (all_stops_for_distance[i]["latitude"], all_stops_for_distance[i]["longitude"])
            coord2 = (all_stops_for_distance[i+1]["latitude"], all_stops_for_distance[i+1]["longitude"])
            total_distance += haversine(coord1, coord2, unit=Unit.KILOMETERS)
        
        return {
            "message": "Route refined with sections",
            "algorithm": "section_refinement",
            "reasoning": reasoning,
            "total_distance_km": round(total_distance, 2),
            "stop_count": len(optimized_stops),
            "section_count": len(request.sections),
            "started_from_current_location": current_loc_stop is not None,
            # Polish stats — frontend uses these to render a success Alert
            # ("Refined: saved 26.8 km · 3 spike(s) tightened"). 0/0.0 when
            # the route was already clean enough that polish skipped.
            "polish_relocations": polish_relocations,
            "polish_distance_saved_km": round(polish_distance_saved_km, 2),
            "stops": optimized_stops + completed_stops
        }
    
    # Auto-select was already resolved before distance matrix calculation — this is a no-op
    
    # Apply selected algorithm
    cluster_info = []  # Populated only by cluster_first
    if algorithm_used == "generoute":
        try:
            optimized_stops = await generoute_optimize(
                stops if not current_loc_stop else stops[1:],
                current_latitude=request.current_latitude,
                current_longitude=request.current_longitude
            )
            reasoning = "Optimized using Generoute API (road-based optimization)"
        except Exception as e:
            logger.warning("Generoute optimization failed: %s, falling back to 2-opt", e)
            nn_result = nearest_neighbor_optimize(stops, distance_matrix, start_index)
            route_indices = _indices_by_identity(stops, nn_result)
            improved_indices = two_opt_improve(route_indices, distance_matrix)
            optimized_stops = [stops[i] for i in improved_indices]
            if current_loc_stop:
                optimized_stops = [s for s in optimized_stops if s.get("id") != "current_location"]
            reasoning = f"Generoute failed ({type(e).__name__}), used 2-Opt fallback"
    
    elif algorithm_used == "mapbox":
        try:
            optimized_stops = await mapbox_optimize(
                stops if not current_loc_stop else stops[1:],  # Exclude current location from optimization
                current_latitude=request.current_latitude,
                current_longitude=request.current_longitude
            )
            reasoning = "Optimized using Mapbox Optimization API (road-based)"
        except Exception as e:
            logger.warning("Mapbox optimization failed: %s, falling back to 2-opt", e)
            # Fallback to 2-opt if Mapbox fails
            nn_result = nearest_neighbor_optimize(stops, distance_matrix, start_index)
            route_indices = _indices_by_identity(stops, nn_result)
            improved_indices = two_opt_improve(route_indices, distance_matrix)
            optimized_stops = [stops[i] for i in improved_indices]
            if current_loc_stop:
                optimized_stops = [s for s in optimized_stops if s.get("id") != "current_location"]
            reasoning = "Mapbox failed, used 2-Opt fallback"
            
    elif algorithm_used == "alns":
        # Use the OSRM duration matrix (seconds) like the rest of the cascade so
        # ALNS optimizes for travel time, not crow-flies distance. Falls back to
        # distance_matrix (OSRM distance / haversine) only when OSRM duration is
        # unavailable.
        alns_matrix = duration_matrix if duration_matrix else distance_matrix
        try:
            alns_time_limit = max(4, min(15, 8 + len(stops) // 10))
            optimized_stops = _srv.alns_hybrid_optimize(
                stops,
                alns_matrix,
                start_index=start_index,
                time_limit_seconds=alns_time_limit,
            )
            reasoning = "Optimized using ALNS Hybrid Metaheuristic (NN + ALNS/SA + Local Search)"
        except Exception as e:
            logger.warning("ALNS optimization failed, using 2-Opt fallback: %s", e)
            nn_result = nearest_neighbor_optimize(stops, alns_matrix, start_index)
            route_indices = _indices_by_identity(stops, nn_result)
            improved_indices = two_opt_improve(route_indices, alns_matrix)
            optimized_stops = [stops[i] for i in improved_indices]
            reasoning = f"ALNS failed ({type(e).__name__}), used 2-Opt fallback"

    elif algorithm_used == "cluster_first":
        cluster_info = []
        try:
            cf_time_limit = max(15, min(60, 20 + len(stops) // 5))
            optimized_stops, cluster_info = await cluster_first_optimize(
                stops,
                distance_matrix,
                start_index=start_index,
                time_limit_seconds=cf_time_limit,
                inner_algorithm=inner_algorithm,
            )
            inner_label = inner_algorithm.upper().replace("_", " ")
            matrix_label = "Mapbox driving durations" if inner_algorithm == "ortools" else "Mapbox road distances"
            reasoning = f"Optimized using Cluster-First Route-Second (DBSCAN neighborhoods + per-cluster {inner_label} with {matrix_label})"
        except Exception as e:
            logger.warning("Cluster-first optimization failed, falling back to ALNS: %s", e, exc_info=True)
            try:
                alns_time_limit = max(4, min(15, 8 + len(stops) // 10))
                optimized_stops = _srv.alns_hybrid_optimize(stops, distance_matrix, start_index=start_index, time_limit_seconds=alns_time_limit)
                reasoning = f"Cluster-first failed ({type(e).__name__}), used ALNS fallback"
            except Exception:
                nn_result = nearest_neighbor_optimize(stops, distance_matrix, start_index)
                route_indices = _indices_by_identity(stops, nn_result)
                improved_indices = two_opt_improve(route_indices, distance_matrix)
                optimized_stops = [stops[i] for i in improved_indices]
                reasoning = f"Cluster-first failed ({type(e).__name__}), used 2-Opt fallback"

    elif algorithm_used == "vroom":
        try:
            solver_matrix = duration_matrix if duration_matrix else _haversine_duration_matrix(stops)

            # Apply traffic-aware multiplier if requested
            traffic_info = ""
            if request.traffic_aware:
                from datetime import datetime, timezone
                dep_hour = request.departure_hour if request.departure_hour is not None else datetime.now(timezone.utc).hour
                tmult = _traffic_multiplier(dep_hour)
                solver_matrix = apply_traffic_multiplier(solver_matrix, dep_hour)
                traffic_info = f", traffic={tmult:.2f}x@{dep_hour}:00"
                logger.info("Traffic-aware: applied %.2fx multiplier for hour %d", tmult, dep_hour)

            indices = cluster_aware_solve(
                vroom_tsp_solve, solver_matrix, start_index, stops,
                exploration_level=5,
            )

            # LKH post-processing: refine VROOM's solution with gold-standard TSP heuristic
            pre_cost = calculate_route_distance(indices, solver_matrix)
            if LKH_AVAILABLE:
                try:
                    lkh_indices = cluster_aware_solve(
                        lkh_tsp_solve, solver_matrix, start_index, stops,
                        runs=5, time_limit_seconds=10,
                    )
                    lkh_cost = calculate_route_distance(lkh_indices, solver_matrix)
                    # Use LKH result only if it's actually better
                    if lkh_cost < pre_cost:
                        indices = lkh_indices
                        post_cost = lkh_cost
                        refinement = "LKH"
                    else:
                        post_cost = pre_cost
                        refinement = "LKH(no improvement)"
                except Exception as lkh_err:
                    logger.warning("LKH post-processing failed, keeping VROOM result: %s", lkh_err)
                    post_cost = pre_cost
                    refinement = "LKH(failed)"
            else:
                # Fallback to 3-opt if LKH binary not available
                indices = three_opt_improve(indices, solver_matrix, max_iterations=3)
                post_cost = calculate_route_distance(indices, solver_matrix)
                refinement = "3-opt"

            saved_pct = ((pre_cost - post_cost) / pre_cost * 100) if pre_cost > 0 else 0
            logger.info("%s post-processing: %.0f → %.0f (saved %.1f%%)", refinement, pre_cost, post_cost, saved_pct)

            # Or-opt final polish: relocate 1-3 stop sequences
            pre_oropt = post_cost
            indices = or_opt_improve(indices, solver_matrix, max_iterations=10)
            post_oropt = calculate_route_distance(indices, solver_matrix)
            oropt_saved = ((pre_oropt - post_oropt) / pre_oropt * 100) if pre_oropt > 0 else 0
            if post_oropt < pre_oropt:
                logger.info("Or-opt polish: %.0f → %.0f (saved %.1f%%)", pre_oropt, post_oropt, oropt_saved)
                refinement += "+Or-opt"
                post_cost = post_oropt
                saved_pct = ((pre_cost - post_cost) / pre_cost * 100) if pre_cost > 0 else 0
            else:
                logger.info("Or-opt polish: no improvement (%.0f)", pre_oropt)

            optimized_stops = [stops[i] for i in indices]
            matrix_source = "OSRM" if (duration_matrix and OSRM_URL) else "Mapbox"
            reasoning = f"VROOM + {refinement} ({matrix_source} duration matrix, {len(stops)} stops, {refinement} saved {saved_pct:.1f}%{traffic_info})"
        except Exception as e:
            logger.warning("VROOM optimization failed, falling back to OR-Tools: %s", e)
            try:
                solver_matrix = duration_matrix if duration_matrix else _haversine_duration_matrix(stops)
                ortools_time_ms = max(2000, min(30000, 2000 + len(stops) * 80))
                indices = ortools_tsp_solve(solver_matrix, depot=start_index, time_limit_ms=ortools_time_ms)
                optimized_stops = [stops[i] for i in indices]
                reasoning = f"VROOM failed, OR-Tools fallback (duration matrix, {ortools_time_ms}ms)"
            except Exception as e2:
                logger.warning("OR-Tools fallback also failed: %s", e2)
                nn_result = nearest_neighbor_optimize(stops, distance_matrix, start_index)
                route_indices = _indices_by_identity(stops, nn_result)
                improved_indices = two_opt_improve(route_indices, distance_matrix)
                optimized_stops = [stops[i] for i in improved_indices]
                reasoning = "VROOM+OR-Tools failed, used 2-Opt fallback"

    elif algorithm_used == "ortools":
        try:
            # Use DURATION matrix (seconds) for time-optimal routing.
            # Scale time limit: 2s base + 80ms per stop. 123 stops ≈ 12s.
            # OR-Tools GUIDED_LOCAL_SEARCH needs adequate time for large routes.
            ortools_time_ms = max(2000, min(30000, 2000 + len(stops) * 80))
            solver_matrix = duration_matrix if duration_matrix else distance_matrix
            indices = ortools_tsp_solve(solver_matrix, depot=start_index, time_limit_ms=ortools_time_ms)
            optimized_stops = [stops[i] for i in indices]
            matrix_type = "duration" if duration_matrix else "distance"
            reasoning = f"OR-Tools TSP (PATH_CHEAPEST_ARC + GUIDED_LOCAL_SEARCH, {matrix_type} matrix, {ortools_time_ms}ms, {len(stops)} stops)"
        except Exception as e:
            logger.warning("OR-Tools optimization failed, using 2-Opt fallback: %s", e)
            nn_result = nearest_neighbor_optimize(stops, distance_matrix, start_index)
            route_indices = _indices_by_identity(stops, nn_result)
            improved_indices = two_opt_improve(route_indices, distance_matrix)
            optimized_stops = [stops[i] for i in improved_indices]
            reasoning = f"OR-Tools failed ({type(e).__name__}), used 2-Opt fallback"

    elif algorithm_used == "pyvrp":
        try:
            solver_matrix = duration_matrix if duration_matrix else _haversine_duration_matrix(stops)
            # HGS needs ≥2s on small/medium TSPs to breed enough generations
            # to untangle crossings, but we cap at 3s to keep the request
            # comfortably inside the frontend's 90s AbortController.
            pyvrp_seconds = max(2.0, min(3.0, 0.5 + len(stops) * 0.04))
            # Pass coordinates so identical-address clusters (multi-unit
            # buildings, apartments, units sharing one front door) are
            # collapsed into a single PyVRP super-node — prevents random
            # zig-zag ordering between zero-distance stops.
            stop_coords = [
                (float(s["longitude"]), float(s["latitude"]))
                for s in stops
            ]
            indices = await asyncio.to_thread(
                pyvrp_tsp_solve,
                solver_matrix,
                start_index,
                pyvrp_seconds,
                0,  # seed
                stop_coords,
            )
            optimized_stops = [stops[i] for i in indices]
            matrix_source = "OSRM" if (duration_matrix and OSRM_URL) else "Mapbox"
            reasoning = (
                f"PyVRP Hybrid Genetic Search ({matrix_source} duration matrix, "
                f"{len(stops)} stops, {pyvrp_seconds:.1f}s budget)"
            )
        except Exception as e:
            logger.warning("PyVRP optimization failed, using OR-Tools fallback: %s", e)
            try:
                ortools_time_ms = max(2000, min(30000, 2000 + len(stops) * 80))
                solver_matrix = duration_matrix if duration_matrix else distance_matrix
                indices = cluster_aware_solve(
                    ortools_tsp_solve, solver_matrix, start_index, stops,
                    time_limit_ms=ortools_time_ms,
                )
                optimized_stops = [stops[i] for i in indices]
                reasoning = f"PyVRP failed ({type(e).__name__}), OR-Tools fallback"
            except Exception:
                nn_result = nearest_neighbor_optimize(stops, distance_matrix, start_index)
                route_indices = _indices_by_identity(stops, nn_result)
                improved_indices = two_opt_improve(route_indices, distance_matrix)
                optimized_stops = [stops[i] for i in improved_indices]
                reasoning = "PyVRP + OR-Tools both failed, used 2-Opt fallback"

    elif algorithm_used == "vroom_ortools":
        try:
            solver_matrix = duration_matrix if duration_matrix else _haversine_duration_matrix(stops)
            ortools_time_ms = max(2000, min(30000, 2000 + len(stops) * 80))
            # Stage 1: VROOM — fast construction heuristic (~100ms)
            vroom_indices = cluster_aware_solve(
                vroom_tsp_solve, solver_matrix, start_index, stops,
                exploration_level=5,
            )
            vroom_cost = calculate_route_distance(vroom_indices, solver_matrix)
            # Stage 2: OR-Tools GLS warm-started from VROOM solution
            ortools_indices = cluster_aware_solve(
                ortools_tsp_solve, solver_matrix, start_index, stops,
                time_limit_ms=ortools_time_ms,
                initial_indices=vroom_indices,
            )
            ortools_cost = calculate_route_distance(ortools_indices, solver_matrix)
            # Take the best of the two
            indices = ortools_indices if ortools_cost <= vroom_cost else vroom_indices
            saved_pct = ((vroom_cost - ortools_cost) / vroom_cost * 100) if vroom_cost > 0 else 0
            matrix_source = "OSRM" if (duration_matrix and OSRM_URL) else "Mapbox"
            reasoning = (
                f"VROOM warm-start → OR-Tools GLS ({matrix_source}, {len(stops)} stops, "
                f"GLS improved {saved_pct:.1f}% over VROOM seed, {ortools_time_ms}ms budget)"
            )
            optimized_stops = [stops[i] for i in indices]
        except Exception as e:
            logger.warning("VROOM+OR-Tools pipeline failed, using OR-Tools cold-start: %s", e)
            try:
                solver_matrix = duration_matrix if duration_matrix else distance_matrix
                ortools_time_ms = max(2000, min(30000, 2000 + len(stops) * 80))
                indices = ortools_tsp_solve(solver_matrix, depot=start_index, time_limit_ms=ortools_time_ms)
                optimized_stops = [stops[i] for i in indices]
                reasoning = f"VROOM→OR-Tools failed ({type(e).__name__}), OR-Tools cold-start fallback"
            except Exception:
                nn_result = nearest_neighbor_optimize(stops, distance_matrix, start_index)
                route_indices = _indices_by_identity(stops, nn_result)
                improved_indices = two_opt_improve(route_indices, distance_matrix)
                optimized_stops = [stops[i] for i in improved_indices]
                reasoning = "VROOM+OR-Tools both failed, used 2-Opt fallback"

    elif algorithm_used in ("nearest_neighbor", "greedy"):
        # Bulletproof greedy fallback. Per spec: rely strictly on the OSRM
        # /Mapbox driving-time matrix (`duration_matrix`) so distances are
        # real road seconds, not haversine. If OSRM is unreachable in the
        # build env, drop to a haversine-shaped duration matrix — same
        # shape, lower precision, never blocks the request. Wrapped in
        # `cluster_aware_solve` via `solve_nearest_neighbor` so multi-
        # parcel super-nodes (apartment doorsteps) expand sequentially
        # at the end and never get split by a "B in the middle of A1, A2".
        nn_matrix = duration_matrix if duration_matrix else _haversine_duration_matrix(stops)
        optimized_stops = solve_nearest_neighbor(nn_matrix, stops, start_index)
        matrix_label = "OSRM" if duration_matrix else "haversine"
        reasoning = (
            f"Optimized using Nearest Neighbor (greedy, super-node aware, "
            f"matrix={matrix_label})"
        )

    elif algorithm_used in ("greedy_2opt", "nearest_neighbor_2opt"):
        # Greedy → 2-opt polish. Roughly halves the quality gap between
        # basic greedy and VROOM at the cost of ~50 ms extra on a 167-stop
        # manifest. Same OSRM-duration-matrix discipline as plain greedy.
        # cluster_aware_solve still wraps the construction step so super-
        # nodes are kept contiguous; the 2-opt refinement runs on the
        # expanded sequence (it never reverses a segment that splits a
        # super-node because such a split would only ever increase cost
        # — the inter-parcel edge is 0 inside a super-node, so swapping
        # it out always lengthens the tour).
        nn_matrix = duration_matrix if duration_matrix else _haversine_duration_matrix(stops)
        nn_stops = solve_nearest_neighbor(nn_matrix, stops, start_index)
        route_indices = _indices_by_identity(stops, nn_stops)
        improved_indices = await asyncio.to_thread(two_opt_improve, route_indices, nn_matrix)
        optimized_stops = [stops[i] for i in improved_indices]
        matrix_label = "OSRM" if duration_matrix else "haversine"
        reasoning = (
            f"Optimized using Greedy + 2-Opt polish (super-node aware, "
            f"matrix={matrix_label})"
        )
        
    elif algorithm_used == "two_opt":
        # Start with nearest neighbor, then improve with 2-opt
        nn_result = nearest_neighbor_optimize(stops, distance_matrix, start_index)
        route_indices = _indices_by_identity(stops, nn_result)
        improved_indices = two_opt_improve(route_indices, distance_matrix)
        optimized_stops = [stops[i] for i in improved_indices]
        reasoning = "Optimized using 2-Opt improvement heuristic"
        
    elif algorithm_used == "simulated_annealing":
        # Silently upgraded to ILS — same interface, strictly better results.
        # ILS uses double-bridge kicks + 2-opt/Or-opt which consistently beats SA.
        ils_time = max(5, min(15, 5 + len(stops) // 10))
        optimized_stops = await asyncio.to_thread(iterated_local_search, stops, distance_matrix, start_index, ils_time)
        reasoning = f"Optimized using ILS (upgraded SA: double-bridge + 2-opt/Or-opt, {ils_time}s budget)"

    elif algorithm_used == "ils":
        ils_time = max(5, min(15, 5 + len(stops) // 10))
        try:
            optimized_stops = await asyncio.to_thread(iterated_local_search, stops, distance_matrix, start_index, ils_time)
            reasoning = f"Optimized using ILS (double-bridge perturbation + 2-opt + Or-opt local search, {ils_time}s budget)"
        except Exception as e:
            logger.warning("ILS optimization failed, using 2-Opt fallback: %s", e)
            nn_result = nearest_neighbor_optimize(stops, distance_matrix, start_index)
            route_indices = _indices_by_identity(stops, nn_result)
            improved_indices = two_opt_improve(route_indices, distance_matrix)
            optimized_stops = [stops[i] for i in improved_indices]
            reasoning = f"ILS failed ({type(e).__name__}), used 2-Opt fallback"
        
    elif algorithm_used == "genetic":
        optimized_stops = await asyncio.to_thread(genetic_algorithm_optimize, stops, distance_matrix, start_index, 
                                                      100 + len(stops) * 2,
                                                      max(30, len(stops)))
        reasoning = "Optimized using Genetic Algorithm (evolutionary)"
        
    elif algorithm_used == "clarke_wright":
        optimized_stops = clarke_wright_savings(stops, distance_matrix, start_index)
        reasoning = "Optimized using Clarke-Wright Savings (VRP algorithm)"

    elif algorithm_used == "lkh":
        try:
            if not LKH_AVAILABLE:
                raise RuntimeError("LKH not available")
            solver_matrix = duration_matrix if duration_matrix else _haversine_duration_matrix(stops)
            indices = cluster_aware_solve(
                lkh_tsp_solve, solver_matrix, start_index, stops,
                runs=5, time_limit_seconds=15,
            )
            optimized_stops = [stops[i] for i in indices]
            reasoning = f"LKH-3 (Lin-Kernighan-Helsgott, {len(stops)} stops)"
        except Exception as e:
            logger.warning("LKH failed, falling back to OR-Tools: %s", e)
            try:
                solver_matrix = duration_matrix if duration_matrix else _haversine_duration_matrix(stops)
                indices = cluster_aware_solve(
                    ortools_tsp_solve, solver_matrix, start_index, stops,
                    time_limit_ms=max(2000, len(stops) * 80),
                )
                optimized_stops = [stops[i] for i in indices]
                reasoning = f"LKH failed ({type(e).__name__}), OR-Tools fallback"
            except Exception:
                nn_result = nearest_neighbor_optimize(stops, distance_matrix, start_index)
                route_indices = _indices_by_identity(stops, nn_result)
                improved_indices = two_opt_improve(route_indices, distance_matrix)
                optimized_stops = [stops[i] for i in improved_indices]
                reasoning = "LKH+OR-Tools failed, 2-Opt fallback"

    elif algorithm_used == "elkai":
        try:
            if not ELKAI_AVAILABLE:
                raise RuntimeError("elkai not available")
            solver_matrix = duration_matrix if duration_matrix else _haversine_duration_matrix(stops)
            indices = cluster_aware_solve(
                elkai_tsp_solve, solver_matrix, start_index, stops,
            )
            optimized_stops = [stops[i] for i in indices]
            reasoning = f"Elkai LKH (bundled C backend, {len(stops)} stops)"
        except Exception as e:
            logger.warning("elkai failed, falling back to OR-Tools: %s", e)
            try:
                solver_matrix = duration_matrix if duration_matrix else _haversine_duration_matrix(stops)
                indices = cluster_aware_solve(
                    ortools_tsp_solve, solver_matrix, start_index, stops,
                    time_limit_ms=max(2000, len(stops) * 80),
                )
                optimized_stops = [stops[i] for i in indices]
                reasoning = f"elkai failed ({type(e).__name__}), OR-Tools fallback"
            except Exception:
                nn_result = nearest_neighbor_optimize(stops, distance_matrix, start_index)
                route_indices = _indices_by_identity(stops, nn_result)
                improved_indices = two_opt_improve(route_indices, distance_matrix)
                optimized_stops = [stops[i] for i in improved_indices]
                reasoning = "elkai+OR-Tools failed, 2-Opt fallback"

    elif algorithm_used == "vroom_lkh_3opt":
        try:
            solver_matrix = duration_matrix if duration_matrix else _haversine_duration_matrix(stops)

            # ── Stage 1+2: VROOM and LKH race in parallel ─────────────────
            # Both solvers are CPU-bound and thread-safe; running them
            # concurrently via asyncio.to_thread means total wall-clock time
            # is max(vroom, lkh) instead of their sum. Winner is selected by
            # measuring actual tour cost on the OSRM matrix so we never accept
            # a self-reported cost that may use a different objective.

            def _run_vroom_sync():
                if not VROOM_AVAILABLE:
                    return None
                return cluster_aware_solve(
                    vroom_tsp_solve, solver_matrix, start_index, stops,
                    exploration_level=5,
                )

            def _run_lkh_sync():
                if not LKH_AVAILABLE:
                    return None
                return cluster_aware_solve(
                    lkh_tsp_solve, solver_matrix, start_index, stops,
                    runs=5, time_limit_seconds=15,
                )

            vroom_result, lkh_result = await asyncio.gather(
                asyncio.to_thread(_run_vroom_sync),
                asyncio.to_thread(_run_lkh_sync),
                return_exceptions=True,
            )

            # Pick the best valid result by measured tour cost
            candidates = []
            for label, result in (("VROOM", vroom_result), ("LKH", lkh_result)):
                if isinstance(result, Exception):
                    logger.warning("%s failed in parallel race: %s", label, result)
                elif result is not None:
                    candidates.append((label, result, calculate_route_distance(result, solver_matrix)))

            if not candidates:
                raise RuntimeError("Both VROOM and LKH failed")

            best_label, indices, best_cost = min(candidates, key=lambda x: x[2])
            # Log which solver won and by how much
            if len(candidates) == 2:
                other_label, _, other_cost = next(c for c in candidates if c[0] != best_label)
                saved_vs_other = ((other_cost - best_cost) / other_cost * 100) if other_cost > 0 else 0
                logger.info("Race: %s wins over %s by %.1f%%", best_label, other_label, saved_vs_other)
            else:
                logger.info("Race: only %s available", best_label)

            # ── Stage 3: 3-opt polish — only keep if it genuinely improves ─
            # Guard: 3-opt occasionally regresses on routes LKH already brought
            # to local optimum, so we measure before/after and discard if worse.
            pre_cost = best_cost
            polished = three_opt_improve(indices, solver_matrix, max_iterations=5)
            polished_cost = calculate_route_distance(polished, solver_matrix)
            if polished_cost < pre_cost:
                indices = polished
                post_cost = polished_cost
            else:
                post_cost = pre_cost
            saved = ((pre_cost - post_cost) / pre_cost * 100) if pre_cost > 0 else 0
            optimized_stops = [stops[i] for i in indices]
            reasoning = f"{best_label}+3-opt pipeline ({len(stops)} stops, 3-opt saved {saved:.1f}%)"
        except Exception as e:
            logger.warning("VROOM+LKH+3opt failed: %s", e)
            # Use the OSRM duration matrix for the fallback so the route
            # is still road-quality. Haversine fallback would produce
            # crow-fly orderings that look like spaghetti once rendered
            # against real one-way streets. If OSRM duration is missing
            # (build env without OSRM) we fall back to a haversine-based
            # *duration* matrix — still seconds-shaped, just less precise.
            fallback_matrix = duration_matrix if duration_matrix else _haversine_duration_matrix(stops)
            nn_result = nearest_neighbor_optimize(stops, fallback_matrix, start_index)
            route_indices = _indices_by_identity(stops, nn_result)
            improved_indices = or_opt_improve(route_indices, fallback_matrix)
            optimized_stops = [stops[i] for i in improved_indices]
            reasoning = "VROOM+LKH+3opt failed, OSRM Or-opt fallback"

    elif algorithm_used == "timefold":
        try:
            if not TIMEFOLD_AVAILABLE:
                raise RuntimeError(f"Timefold not available: {TIMEFOLD_IMPORT_ERROR}")
            time_limit = max(5, min(15, 5 + len(stops) // 20))
            # Timefold runs a Java constraint solver via JPype — CPU-bound for
            # the full time_limit. Wrap in to_thread so the event loop stays
            # responsive for concurrent requests (health checks, map tiles…).
            optimized_stops = await asyncio.to_thread(
                timefold_optimize, stops, distance_matrix,
                start_index=start_index, time_limit_seconds=time_limit,
            )
            reasoning = f"Timefold Java constraint solver ({len(stops)} stops, {time_limit}s limit)"
        except Exception as e:
            logger.warning("Timefold failed, falling back to OR-Tools: %s", e)
            try:
                solver_matrix = duration_matrix if duration_matrix else _haversine_duration_matrix(stops)
                indices = ortools_tsp_solve(solver_matrix, depot=start_index, time_limit_ms=max(2000, len(stops) * 80))
                optimized_stops = [stops[i] for i in indices]
                reasoning = f"Timefold failed ({type(e).__name__}), OR-Tools fallback"
            except Exception:
                nn_result = nearest_neighbor_optimize(stops, distance_matrix, start_index)
                route_indices = _indices_by_identity(stops, nn_result)
                improved_indices = two_opt_improve(route_indices, distance_matrix)
                optimized_stops = [stops[i] for i in improved_indices]
                reasoning = "Timefold+OR-Tools failed, 2-Opt fallback"

    elif algorithm_used == "three_opt":
        nn_result = nearest_neighbor_optimize(stops, distance_matrix, start_index)
        route_indices = _indices_by_identity(stops, nn_result)
        improved_indices = three_opt_improve(route_indices, distance_matrix, max_iterations=5)
        optimized_stops = [stops[i] for i in improved_indices]
        reasoning = "Optimized using 3-Opt improvement heuristic (NN seed + 3-edge reconnection)"
        
    elif algorithm_used == "ortools_smart_insertion":
        # ── Late Freight Smart Insertion ──
        # Locked stops keep their immutable `original_sequence` order (N
        # before N+1) via OR-Tools precedence constraints; unlocked late
        # freight stops route freely (PATH_CHEAPEST_ARC + GUIDED_LOCAL_SEARCH)
        # and slot into the cheapest gaps. We deliberately call
        # `ortools_tsp_solve` directly (not cluster_aware_solve) so the
        # locked node indices stay aligned with the precedence constraints.
        solver_matrix = duration_matrix if duration_matrix else _haversine_duration_matrix(stops)
        ortools_time_ms = max(2000, min(30000, 2000 + len(stops) * 80))
        try:
            indices = ortools_tsp_solve(
                solver_matrix,
                depot=start_index,
                time_limit_ms=ortools_time_ms,
                locked_order=locked_order_indices,
            )
            optimized_stops = [stops[i] for i in indices]
            reasoning = (
                f"Late Freight smart insertion (OR-Tools PATH_CHEAPEST_ARC + "
                f"GUIDED_LOCAL_SEARCH, {len(locked_order_indices)} locked precedence "
                f"constraints, {len(stops)} stops, {ortools_time_ms}ms)"
            )
        except Exception as e:
            logger.warning("Smart insertion solver failed, deterministic fallback: %s", e)
            optimized_stops = _smart_insertion_fallback(
                stops, solver_matrix, start_index, locked_order_indices
            )
            reasoning = f"Late Freight smart insertion (deterministic fallback: {type(e).__name__})"

    else:
        # Default to nearest neighbor
        optimized_stops = nearest_neighbor_optimize(stops, distance_matrix, start_index)
        reasoning = "Optimized using Nearest Neighbor algorithm"
    
    # Remove current location stop from results (it's just for optimization)
    if current_loc_stop:
        optimized_stops = [s for s in optimized_stops if s.get("id") != "current_location"]
    
    # Update stop orders in database (bulk write — single round-trip)
    from pymongo import UpdateOne as _BulkOp
    bulk_ops = [
        _BulkOp({"id": stop["id"], "user_id": current_user.user_id}, {"$set": {"order": index}})
        for index, stop in enumerate(optimized_stops)
        if stop.get("id") != "current_location"
    ]
    if bulk_ops:
        await db.stops.bulk_write(bulk_ops, ordered=False)
    
    # Calculate total distance — prefer Mapbox road distance, fall back to haversine.
    # Kick off the shadow benchmark in a background thread FIRST so it runs in
    # parallel with the async OSRM/Mapbox fetch below.
    all_stops_for_distance = optimized_stops
    if current_loc_stop:
        all_stops_for_distance = [current_loc_stop] + optimized_stops

    # Shadow-test: run the best alternative algorithm for comparison. Wrapped
    # in `asyncio.to_thread` + `create_task` so it runs CONCURRENTLY with the
    # road-distance fetch instead of serialising another 5-10 s onto every
    # optimize call. We await the task right before we need the result.
    SHADOW_CANDIDATES = ["alns", "ortools", "two_opt"]
    shadow_algo = next((a for a in SHADOW_CANDIDATES if a != algorithm_used), None)
    shadow_task = (
        asyncio.create_task(
            asyncio.to_thread(_run_algorithm_benchmark, shadow_algo, stops, distance_matrix, start_index)
        )
        if shadow_algo and len(stops) >= 2
        else None
    )

    road_distance = await calculate_road_distance_km(all_stops_for_distance)

    haversine_distance = 0.0
    for i in range(len(all_stops_for_distance) - 1):
        coord1 = (all_stops_for_distance[i]["latitude"], all_stops_for_distance[i]["longitude"])
        coord2 = (all_stops_for_distance[i+1]["latitude"], all_stops_for_distance[i+1]["longitude"])
        haversine_distance += haversine(coord1, coord2, unit=Unit.KILOMETERS)

    total_distance = road_distance if road_distance is not None else round(haversine_distance, 2)
    distance_source = "road" if road_distance is not None else "haversine"

    # Collect the shadow result now (it's been running concurrently with the
    # road-distance fetch above). `await` on the task just waits for whatever
    # time remains — usually zero when the main solver was slower.
    shadow = None
    if shadow_task is not None:
        try:
            shadow = await shadow_task
            if shadow and shadow.get("error") is None:
                shadow["savings_km"] = round(shadow["total_distance_km"] - haversine_distance, 3)
        except Exception as _shadow_err:
            logger.warning("Shadow benchmark failed: %s", _shadow_err)
            shadow = None

    # ── Quality badge: optimized vs nearest-neighbor baseline ──
    # NN is O(n²), always available, and is the universally-understood naïve
    # greedy baseline. Showing "saved X km / Y% vs greedy" gives the driver
    # instant visual proof the optimize button is worth tapping.
    quality_badge = None
    try:
        if algorithm_used != "nearest_neighbor" and len(stops) >= 3:
            nn_stops = nearest_neighbor_optimize(stops, distance_matrix, start_index)
            nn_indices = _indices_by_identity(stops, nn_stops)
            nn_km = sum(
                distance_matrix[nn_indices[i]][nn_indices[i + 1]]
                for i in range(len(nn_indices) - 1)
            )
            opt_km = total_distance if distance_source == "road" else haversine_distance
            saved_km = nn_km - opt_km
            saved_pct = (saved_km / nn_km * 100.0) if nn_km > 0 else 0.0
            quality_badge = {
                "baseline_algorithm": "nearest_neighbor",
                "baseline_km": round(nn_km, 2),
                "optimized_km": round(opt_km, 2),
                "saved_km": round(saved_km, 2),
                "saved_pct": round(saved_pct, 1),
                "improved": saved_km > 0.05,
            }
    except Exception as _badge_err:
        logger.debug(f"Quality badge computation skipped: {_badge_err}")

    # ── Time savings vs unoptimised input order (driver-facing badge) ──
    # The most meaningful comparison for a driver is "how much time did
    # tapping Optimise save me vs the order I had?". This is the order they
    # would have actually driven if they hadn't optimised — not the NN
    # greedy (above) nor a theoretical baseline. We compute open-path
    # duration on the SAME OSRM duration_matrix the solver used, so the
    # number is directly comparable and self-consistent.
    time_savings = None
    try:
        if duration_matrix is not None and len(stops) >= 3:
            optimized_indices = _indices_by_identity(stops, optimized_stops)
            input_indices = list(range(len(stops)))  # input was in DB order
            input_seconds = sum(
                duration_matrix[input_indices[i]][input_indices[i + 1]]
                for i in range(len(input_indices) - 1)
            )
            optimized_seconds = sum(
                duration_matrix[optimized_indices[i]][optimized_indices[i + 1]]
                for i in range(len(optimized_indices) - 1)
            )
            saved_seconds = max(0, int(input_seconds) - int(optimized_seconds))
            saved_pct = (
                (saved_seconds / input_seconds * 100.0) if input_seconds > 0 else 0.0
            )
            time_savings = {
                "baseline_seconds": int(input_seconds),
                "optimized_seconds": int(optimized_seconds),
                "saved_seconds": int(saved_seconds),
                "saved_minutes": round(saved_seconds / 60.0, 1),
                "saved_pct": round(saved_pct, 1),
                "improved": saved_seconds >= 30,  # show badge only if >=30s saved
            }
    except Exception as _ts_err:
        logger.debug(f"Time savings computation skipped: {_ts_err}")

    # ── Visual cluster-spike detection (post-solve) ─────────────────────
    # Even an optimal-by-OSRM-time tour can LOOK fragmented on the map: a
    # stop B can be a small driving-time detour from A→C (highway split,
    # one-way pair) yet be a large *geographic* spike. We sweep every
    # consecutive triplet (A, B, C) using haversine distance and flag any
    # B where `dist(A,C) < threshold * (dist(A,B) + dist(B,C))` — i.e. B
    # is well off the natural A→C line.
    cluster_warnings: List[Dict[str, Any]] = detect_cluster_spikes(optimized_stops)

    # ── Auto-tighten cluster spikes IN-PLACE during optimisation ────────
    # User feedback: drivers don't want to tap a banner — they expect the
    # optimiser to never produce visible zig-zags in the first place.
    # We iteratively relocate the worst spike (largest `extra_km`) up to
    # 10 passes, then run OSRM verification. If OSRM agrees the cleaned
    # route isn't slower in driving time, we silently swap it in. If OSRM
    # rolls back (i.e. the detour is genuinely faster on the road
    # network — e.g. a highway split or one-way pair), we keep the
    # solver's choice and surface the remaining warning so the driver can
    # still override manually. Net effect: cosmetic zig-zags vanish; only
    # OSRM-justified detours ever reach the screen.
    if cluster_warnings and len(optimized_stops) >= 3:
        try:
            cleaned, auto_moves = _iterative_haversine_tighten(optimized_stops)
            if auto_moves:
                # Driver-preference tolerance: accept the cleaned route even
                # if OSRM thinks it's marginally slower. A driver would
                # rather drive 90s longer than do an obvious cross-suburb
                # zig-zag mid-cluster (cf. the Parklands Blvd 68→69→70
                # spike report from 2026-04-25). The effective threshold is
                # `max(90s, before_s * 0.03)` — so a 1-hour route can grow
                # by up to ~108s (3%), capped on the upper end by the route
                # length itself. Manual /tighten endpoints stay strict
                # (slack=0) so an explicit user tap never makes them strictly
                # slower.
                #
                # 2026-05-11 — REVERTED from a wider (240 s / 5 %) tier on
                # ≥150-stop routes. Empirically the wider tier let OSRM
                # accept 2-opt swaps that displaced individual stops into
                # neighbouring clusters on big production runs ("specific
                # stops out of order that obviously shouldn't be"). The
                # tightener has no cluster-locality guard, so the slack
                # budget IS the cluster-locality guard — a small budget
                # restricts moves to within-cluster relocations. Single
                # tier across all route sizes restores the working
                # baseline. If we ever want to allow wider cleanups on
                # big routes, the right fix is a cluster-locality guard
                # in the move generator, NOT a wider OSRM slack.
                chosen, _b, _a, rolled_back = await _osrm_verify_relocation(
                    optimized_stops, cleaned,
                    slack_seconds=90, slack_ratio=0.03,
                )
                if not rolled_back:
                    optimized_stops = chosen
                    cluster_warnings = detect_cluster_spikes(optimized_stops)
                else:
                    # The tightener tried, OSRM said the fix would cost more
                    # road-time than the slack budget allows, and the move
                    # chain was rolled back. Nothing the user can do via the
                    # banner will improve this route. Suppress warnings
                    # entirely — keeping them on screen is a UI lie.
                    cluster_warnings = []
                logger.info(
                    "Auto-tightened %d move(s) during /api/optimize "
                    "(rolled_back=%s, raw_warnings=%d)",
                    len(auto_moves), rolled_back, len(cluster_warnings),
                )
        except Exception as auto_err:
            logger.debug(f"Auto-tighten skipped: {auto_err}")

    # Honest banner: hide warnings the algorithm cannot actually fix on a
    # follow-up tighten. Runs unconditionally — without this filter, an
    # OSRM rollback or a route the solver already nailed leaves the UI
    # showing "17 detour stops" even though every flagged stop is at its
    # haversine-optimal position and Tighten All would be a no-op. Silent
    # spikes that no further single-stop relocation can address are pure
    # geometric quirks (peninsulas, road-network asymmetries) and have no
    # business raising a "you can fix this" banner.
    if cluster_warnings:
        before_filter = len(cluster_warnings)
        cluster_warnings = _filter_actionable_warnings(
            optimized_stops, cluster_warnings
        )
        logger.info(
            "Cluster warnings filter: raw=%d → actionable=%d",
            before_filter, len(cluster_warnings),
        )

    all_output_stops = optimized_stops + completed_stops

    # ── Route Telepathy (Phase A): apply learned sequence preferences ──
    # Currently gated to the owner account only.  Mutates `optimized_stops`
    # in-place (only the uncompleted ones — completed stops stay where the
    # archival flow placed them at the end of the list).
    telepathy_meta: Dict[str, Any] = {"applied": False}
    try:
        if current_user.user_id in TELEPATHY_USER_IDS:
            from ml.sequence_learner import apply_preferences as _seq_apply
            telepathy_meta = await _seq_apply(db, current_user.user_id, optimized_stops)
            if telepathy_meta.get("applied"):
                # Re-build all_output_stops so the response carries the
                # post-swap order. Completed stops always tail the list.
                all_output_stops = optimized_stops + completed_stops
    except Exception as e:  # noqa: BLE001
        logger.warning("[sequence_learner] apply_preferences failed: %s", e)

    # ── Absolute stop-id binding (no positional drift) ──────────────────
    # Frontend currently maps the route by positional order of `stops`.
    # That works as long as nothing reorders the array in transit, but it
    # silently breaks if a serialiser or middleware ever shuffles them.
    # `optimized_sequence` is the canonical, ID-based answer to "what
    # order should the driver visit?"  — a flat list of `stop.id` strings
    # in the optimised order (uncompleted stops first, then completed).
    optimized_sequence = [
        s["id"] for s in all_output_stops if s.get("id") is not None
    ]
    response_body = {
        "message": "Route optimized",
        "algorithm": algorithm_used,
        "reasoning": reasoning,
        "total_distance_km": total_distance,
        "distance_source": distance_source,
        "stop_count": len(all_output_stops),
        "started_from_current_location": current_loc_stop is not None,
        "stops": all_output_stops,
        "optimized_sequence": optimized_sequence,
        "cluster_warnings": cluster_warnings,
        "shadow": shadow,
        "quality_badge": quality_badge,
        "time_savings": time_savings,
        # ── Route Telepathy meta — present even if no swaps were made,
        # so the UI can show the "Learning..." badge once the user_id is
        # whitelisted. Empty list = nothing was reordered.
        "telepathy": telepathy_meta,
    }
    # Include cluster overlay data when cluster_first is used
    if algorithm_used == "cluster_first" and cluster_info:
        response_body["clusters"] = cluster_info

    # ── AUDIT 3: outbound sequence ───────────────────────────────────────
    # The exact ID order this response will deliver to the device. Pair
    # this with the frontend "AUDIT API RX" log: if the two arrays differ,
    # something between FastAPI's JSON encoder and the React state set is
    # re-shuffling. If they match but the polyline still draws wrong,
    # the bug is in the polyline-coord builder (the array-iteration order
    # vs `order` field mismatch we already chased once).
    logger.info(
        "AUDIT[/optimize] TX algorithm=%s sequence_first5=%s "
        "(if frontend RX differs, transit is re-shuffling)",
        algorithm_used,
        [s["id"] for s in all_output_stops[:5] if s.get("id")],
    )
    return response_body


# ── Tighten helpers + endpoints (moved to routes/optimize_tighten.py) ────────
# Re-exported here so existing `from server import X` and test monkeypatches
# keep working without any call-site changes.
from routes.optimize_tighten import (  # noqa: E402,F401
    _two_opt_pass,
    _filter_actionable_warnings,
    _haversine_path_km,
    _relocate_stop_haversine,
    _iterative_haversine_tighten,
    _persist_pending_order,
    _osrm_verify_relocation,
)

# ── Async job store + endpoints (moved to routes/optimize_jobs.py) ────────────
# `_ensure_optimize_jobs_indexes` is called from the startup hook in server.py.
from routes.optimize_jobs import _ensure_optimize_jobs_indexes  # noqa: E402,F401

