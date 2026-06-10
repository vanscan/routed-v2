"""Benchmark & shadow-testing endpoint.

    POST /benchmark → run all (or selected) solvers on the current route and
                      return comparison metrics (distance, time, quality)

Split out of server.py for maintainability. The solver functions and
availability flags (`vroom_tsp_solve`, `LKH_AVAILABLE`, …) are imported
from `server` at call time so monkeypatching `server.<solver>` in tests
keeps working and the module loads before server.py finishes
initialising.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request

from models import BenchmarkRequest
from routes.billing import require_pro as _billing_require_pro

logger = logging.getLogger("server")
router = APIRouter()


async def _current_user(request: Request):
    """Dep wrapper — defers the `server` import until the first request so the
    module can load before `server.py` finishes defining its symbols."""
    from server import get_current_user  # noqa: WPS433
    return await get_current_user(request)


def compute_route_quality_metrics(stops: List[dict], distance_matrix: List[List[float]], route_indices: List[int]) -> Dict[str, Any]:
    """Compute route quality metrics beyond raw distance."""
    n = len(route_indices)
    if n < 2:
        return {"backtrack_count": 0, "backtrack_ratio": 0.0, "longest_leg_km": 0.0, "shortest_leg_km": 0.0, "leg_variance": 0.0, "cluster_score": 1.0}

    legs = []
    for i in range(n - 1):
        legs.append(distance_matrix[route_indices[i]][route_indices[i + 1]])

    # Backtracking: a leg that increases bearing by >120° relative to previous
    backtrack_count = 0
    for i in range(1, len(legs)):
        if i + 1 < n:
            a = route_indices[i - 1]
            b = route_indices[i]
            c = route_indices[i + 1]
            # Simple heuristic: if going to c is further from a than b is, we're backtracking
            dist_a_c = distance_matrix[a][c]
            dist_a_b = distance_matrix[a][b]
            dist_b_c = distance_matrix[b][c]
            if dist_a_c < dist_a_b and dist_b_c > 0:
                backtrack_count += 1

    longest_leg = max(legs) if legs else 0.0
    shortest_leg = min(legs) if legs else 0.0
    mean_leg = sum(legs) / len(legs) if legs else 0.0
    variance = sum((leg - mean_leg) ** 2 for leg in legs) / len(legs) if legs else 0.0

    # Cluster coherence: ratio of sequential neighbor distances vs random shuffle average
    total_dist = sum(legs)
    all_distances = [distance_matrix[i][j] for i in range(len(distance_matrix)) for j in range(len(distance_matrix)) if i != j]
    avg_random_dist = sum(all_distances) / len(all_distances) if all_distances else 1.0
    expected_random_total = avg_random_dist * (n - 1)
    cluster_score = round(1.0 - (total_dist / expected_random_total) if expected_random_total > 0 else 0.0, 4)

    return {
        "backtrack_count": backtrack_count,
        "backtrack_ratio": round(backtrack_count / max(n - 2, 1), 4),
        "longest_leg_km": round(longest_leg, 3),
        "shortest_leg_km": round(shortest_leg, 3),
        "leg_variance": round(variance, 4),
        "cluster_score": max(0.0, cluster_score),
    }


def _run_algorithm_benchmark(algo_id: str, stops: List[dict], distance_matrix: List[List[float]], start_index: int) -> Dict[str, Any]:
    """Run a single algorithm and collect metrics. Returns dict with results or error."""
    # Call-time import: solver flags/functions live in server.py (and may be
    # monkeypatched there by tests), so resolve them per call, not at load.
    # `alns_hybrid_optimize` / `timefold_optimize` are resolved via attribute
    # access inside the try below — their guarded imports can leave them
    # undefined/None in server, which must surface as a per-algorithm error,
    # not break this whole import.
    import server as _srv  # noqa: WPS433
    from server import (  # noqa: WPS433
        LKH_AVAILABLE,
        LKH_IMPORT_ERROR,
        PYVRP_AVAILABLE,
        PYVRP_IMPORT_ERROR,
        TIMEFOLD_AVAILABLE,
        TIMEFOLD_IMPORT_ERROR,
        VROOM_AVAILABLE,
        VROOM_IMPORT_ERROR,
        _indices_by_identity,
        clarke_wright_savings,
        genetic_algorithm_optimize,
        iterated_local_search,
        lkh_tsp_solve,
        nearest_neighbor_optimize,
        ortools_optimize,
        pyvrp_tsp_solve,
        simulated_annealing_optimize,
        three_opt_improve,
        two_opt_improve,
        vroom_tsp_solve,
    )
    import time as _bench_time
    t0 = _bench_time.perf_counter()
    error = None
    optimized = []

    try:
        if algo_id == "vroom":
            if not VROOM_AVAILABLE:
                error = f"pyvroom not available: {VROOM_IMPORT_ERROR}"
            else:
                indices = vroom_tsp_solve(distance_matrix, depot=start_index)
                optimized = [stops[i] for i in indices]
        elif algo_id == "lkh":
            if not LKH_AVAILABLE:
                error = f"LKH not available: {LKH_IMPORT_ERROR}"
            else:
                indices = lkh_tsp_solve(distance_matrix, depot=start_index)
                optimized = [stops[i] for i in indices]
        elif algo_id == "pyvrp":
            # PyVRP HGS — same engine the production /api/optimize uses by
            # default. Was missing from the benchmark dispatcher, which made
            # it show up in the demo report as "Unknown algorithm: pyvrp".
            if not PYVRP_AVAILABLE:
                error = f"pyvrp not available: {PYVRP_IMPORT_ERROR}"
            else:
                time_limit = max(2.0, min(8.0, 2.0 + len(stops) / 40))
                coords = [(float(s.get("lng", 0.0)), float(s.get("lat", 0.0))) for s in stops]
                indices = pyvrp_tsp_solve(
                    distance_matrix,
                    depot=start_index,
                    time_limit_seconds=time_limit,
                    seed=42,
                    coordinates=coords,
                )
                optimized = [stops[i] for i in indices]
        elif algo_id == "vroom_lkh_3opt":
            # Full pipeline: VROOM -> LKH-3 -> 3-opt (the production chain)
            if not VROOM_AVAILABLE:
                error = f"pyvroom not available: {VROOM_IMPORT_ERROR}"
            else:
                indices = vroom_tsp_solve(distance_matrix, depot=start_index)
                if LKH_AVAILABLE:
                    try:
                        indices = lkh_tsp_solve(distance_matrix, depot=start_index)
                    except Exception:
                        pass
                indices = three_opt_improve(indices, distance_matrix, max_iterations=3)
                optimized = [stops[i] for i in indices]
        elif algo_id == "timefold":
            if not TIMEFOLD_AVAILABLE:
                error = f"Timefold not available: {TIMEFOLD_IMPORT_ERROR}"
            else:
                time_limit = max(5, min(15, 5 + len(stops) // 20))
                optimized = _srv.timefold_optimize(stops, distance_matrix, start_index=start_index, time_limit_seconds=time_limit)
        elif algo_id == "alns":
            time_limit = max(3, min(10, 5 + len(stops) // 15))
            optimized = _srv.alns_hybrid_optimize(stops, distance_matrix, start_index=start_index, time_limit_seconds=time_limit)
        elif algo_id == "ortools":
            time_limit = max(3, min(12, 5 + len(stops) // 10))
            optimized = ortools_optimize(stops, distance_matrix, start_index=start_index, time_limit_seconds=time_limit)
        elif algo_id == "nearest_neighbor":
            optimized = nearest_neighbor_optimize(stops, distance_matrix, start_index)
        elif algo_id == "two_opt":
            nn = nearest_neighbor_optimize(stops, distance_matrix, start_index)
            ri = _indices_by_identity(stops, nn)
            improved = two_opt_improve(ri, distance_matrix)
            optimized = [stops[i] for i in improved]
        elif algo_id == "three_opt":
            nn = nearest_neighbor_optimize(stops, distance_matrix, start_index)
            ri = _indices_by_identity(stops, nn)
            improved = three_opt_improve(ri, distance_matrix, max_iterations=3)
            optimized = [stops[i] for i in improved]
        elif algo_id == "simulated_annealing":
            iters = min(8000, 3000 + len(stops) * 60)
            optimized = simulated_annealing_optimize(stops, distance_matrix, start_index, iterations=iters)
        elif algo_id == "genetic":
            gens = min(150, 60 + len(stops))
            pop = max(25, min(40, len(stops)))
            optimized = genetic_algorithm_optimize(stops, distance_matrix, start_index, generations=gens, population_size=pop)
        elif algo_id == "clarke_wright":
            optimized = clarke_wright_savings(stops, distance_matrix, start_index)
        elif algo_id == "ils":
            time_limit = max(3, min(12, 5 + len(stops) // 15))
            optimized = iterated_local_search(stops, distance_matrix, start_index=start_index, time_limit_seconds=time_limit)
        elif algo_id == "vroom_ortools":
            # VROOM warm-start + OR-Tools GLS refinement
            if not VROOM_AVAILABLE:
                error = f"pyvroom not available: {VROOM_IMPORT_ERROR}"
            else:
                indices = vroom_tsp_solve(distance_matrix, depot=start_index)
                vroom_seed = [stops[i] for i in indices]
                try:
                    time_limit = max(3, min(10, 3 + len(stops) // 15))
                    optimized = ortools_optimize(vroom_seed, distance_matrix, start_index=0, time_limit_seconds=time_limit)
                except Exception:
                    optimized = vroom_seed
        else:
            error = f"Unknown algorithm: {algo_id}"
    except Exception as exc:
        error = str(exc)[:120]

    elapsed_ms = round((_bench_time.perf_counter() - t0) * 1000, 1)

    if error or not optimized:
        return {"algorithm": algo_id, "error": error or "No result", "time_ms": elapsed_ms}

    # Build route indices for metrics
    id_to_idx = {id(s): i for i, s in enumerate(stops)}
    route_indices = [id_to_idx.get(id(s), 0) for s in optimized]

    total_dist = 0.0
    for i in range(len(route_indices) - 1):
        total_dist += distance_matrix[route_indices[i]][route_indices[i + 1]]

    quality = compute_route_quality_metrics(stops, distance_matrix, route_indices)

    return {
        "algorithm": algo_id,
        "total_distance_km": round(total_dist, 3),
        "time_ms": elapsed_ms,
        "quality": quality,
        "error": None,
    }


@router.post("/benchmark")
async def benchmark_algorithms(
    request: BenchmarkRequest = BenchmarkRequest(),
    current_user=Depends(_current_user),
    _pro=Depends(_billing_require_pro),
):
    """Run all (or selected) algorithms on the current route and return comparison metrics."""
    from server import (  # noqa: WPS433
        LKH_AVAILABLE,
        PYVRP_AVAILABLE,
        TIMEFOLD_AVAILABLE,
        calculate_distance_matrix,
        db,
    )
    all_user_stops = await db.stops.find({"user_id": current_user.user_id}, {"_id": 0}).sort("order", 1).to_list(1000)
    stops = [s for s in all_user_stops if not s.get("completed")]

    if len(stops) < 2:
        raise HTTPException(status_code=400, detail="Need at least 2 incomplete stops to benchmark")

    start_index = 0
    if request.use_current_location and request.current_latitude and request.current_longitude:
        current_loc = {
            "id": "current_location",
            "address": "Current Location",
            "latitude": request.current_latitude,
            "longitude": request.current_longitude,
            "completed": False,
        }
        stops = [current_loc] + stops
        start_index = 0

    distance_matrix = calculate_distance_matrix(stops)

    LOCAL_ALGORITHMS = [
        "vroom_lkh_3opt", "vroom_ortools", "vroom", "lkh", "timefold",
        "alns", "ortools", "pyvrp", "ils",
        "nearest_neighbor", "two_opt", "three_opt",
        "simulated_annealing", "genetic", "clarke_wright",
    ]
    # Filter out solvers whose native dependencies aren't present in this environment
    # (LKH binary on bare-metal, Java JVM for Timefold). In the production Docker
    # image these aren't shipped, so listing them only produces "Failed" noise.
    if not LKH_AVAILABLE:
        LOCAL_ALGORITHMS = [a for a in LOCAL_ALGORITHMS if a not in ("lkh", "vroom_lkh_3opt")]
    if not TIMEFOLD_AVAILABLE:
        LOCAL_ALGORITHMS = [a for a in LOCAL_ALGORITHMS if a != "timefold"]
    if not PYVRP_AVAILABLE:
        LOCAL_ALGORITHMS = [a for a in LOCAL_ALGORITHMS if a != "pyvrp"]
    algos_to_run = request.algorithms if request.algorithms else LOCAL_ALGORITHMS

    # Run each algorithm (sequentially to avoid CPU contention skewing times).
    # Wrapped in asyncio.to_thread so the event loop stays responsive — otherwise
    # 129+ stops × 13 solvers blocks the entire FastAPI process for ~2 minutes,
    # which both times out the K8s ingress and freezes all other requests.
    # A 45s wall-time budget caps runaway benchmarks (esp. LKH / genetic on big
    # routes); algorithms that don't fit in the budget come back as errors so
    # the UI still shows partial results.
    import time as _budget_time

    def _run_all_algorithms() -> List[Dict[str, Any]]:
        results_local: List[Dict[str, Any]] = []
        budget_seconds = 45.0
        started = _budget_time.perf_counter()
        for algo_id in algos_to_run:
            if algo_id not in LOCAL_ALGORITHMS:
                continue
            if _budget_time.perf_counter() - started > budget_seconds:
                results_local.append({
                    "algorithm": algo_id,
                    "error": "Skipped (45s benchmark budget exceeded)",
                    "time_ms": 0,
                })
                continue
            results_local.append(
                _run_algorithm_benchmark(algo_id, stops, distance_matrix, start_index)
            )
        return results_local

    results = await asyncio.to_thread(_run_all_algorithms)

    # Sort by distance (best first), errors last
    successful = [r for r in results if r.get("error") is None]
    failed = [r for r in results if r.get("error") is not None]
    successful.sort(key=lambda r: r["total_distance_km"])

    winner = successful[0]["algorithm"] if successful else None

    return {
        "stop_count": len(stops),
        "results": successful + failed,
        "winner": winner,
        "started_from_current_location": start_index == 0 and request.use_current_location,
    }
