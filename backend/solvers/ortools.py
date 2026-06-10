"""OR-Tools single-vehicle TSP wrapper with Guided Local Search,
plus the smart-insertion fallback for late freight.

Split out of server.py for maintainability. Availability flags, solver
library modules and sibling helpers still live in (or are re-exported
from) `server`, so functions here resolve them with call-time
`from server import ...` / `import server` — late binding keeps the lazy
solver loaders and `monkeypatch.setattr(server, ...)` in tests working.
Never import `server` at module level here: this module is imported while
server.py is still executing.
"""
from __future__ import annotations

import logging
from typing import List

logger = logging.getLogger("server")

def build_time_matrix_from_distance(distance_matrix: List[List[float]], avg_speed_kmh: float = 38.0) -> List[List[int]]:
    """Approximate travel-time matrix (seconds) from distance matrix (km)."""
    if avg_speed_kmh <= 0:
        avg_speed_kmh = 38.0

    n = len(distance_matrix)
    time_matrix = [[0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            # seconds = (km / kmh) * 3600
            time_matrix[i][j] = max(1, int((distance_matrix[i][j] / avg_speed_kmh) * 3600))
    return time_matrix


def ortools_optimize(
    stops: List[dict],
    distance_matrix: List[List[float]],
    start_index: int = 0,
    time_limit_seconds: int = 10,
) -> List[dict]:
    """Legacy wrapper — calls ortools_tsp_solve and maps indices back to stops."""
    if len(stops) <= 1:
        return stops
    indices = ortools_tsp_solve(distance_matrix, depot=start_index, time_limit_ms=time_limit_seconds * 1000)
    return [stops[i] for i in indices]


def ortools_tsp_solve(
    matrix: List[List[float]],
    depot: int = 0,
    time_limit_ms: int = 2000,
    initial_indices: List[int] = None,
    locked_order: List[int] = None,
) -> List[int]:
    """
    Solve the Travelling Salesman Problem using Google OR-Tools.

    This is the single, industry-standard solver for route optimization.
    It accepts a Distance/Duration Matrix and returns the optimal visit order.

    ── How it works ──
    1. An (N+1)-node model is created: N real stops + 1 dummy "end" node.
       The dummy end node has zero cost from every real node, giving OR-Tools
       freedom to terminate the route at whichever real stop is cheapest.
       This produces an OPEN-PATH route (start at depot, end anywhere).

    2. First solution: PATH_CHEAPEST_ARC greedily extends the cheapest arc.
    3. Metaheuristic: GUIDED_LOCAL_SEARCH escapes local minima by penalising
       frequently-used arcs, untangling crossed paths and producing routes
       similar to commercial apps like Circuit/Routific.
    4. The solver runs for `time_limit_ms` milliseconds, returning the best
       solution found within that budget.

    ── Mapping matrix indices to front-end stops ──
    1. Build your stops array:
         stops = [current_location] + delivery_stops
       Index 0 = current location (depot), 1..N = delivery stops.
    2. Query Mapbox Matrix API with the coordinates of all stops.
       The returned matrix[i][j] = driving time/distance from stop i to stop j.
       Use duration (seconds) for time-optimal routing.
    3. Call: ordered = ortools_tsp_solve(matrix, depot=0)
    4. Map back: route = [stops[i] for i in ordered]

    Args:
        matrix:        NxN matrix of costs (driving seconds or meters).
                       matrix[i][j] = cost to travel from node i to node j.
                       Populated by the Mapbox Matrix API.
        depot:         Index of the starting node (typically 0 = current location).
        time_limit_ms: Solver time budget in milliseconds (default 2000).
                       2000ms is enough for ≤50 stops. Scale up for larger routes.

    Returns:
        Ordered list of node indices (0..N-1) representing the visit sequence.
        The depot is always first. The route ends at whichever stop minimises
        total cost (open-path TSP).

    Raises:
        RuntimeError: If OR-Tools is not installed.
        ValueError:   If no solution is found.
    """
    import server as _srv  # noqa: WPS433
    if not _srv.ORTOOLS_AVAILABLE or getattr(_srv, 'pywrapcp', None) is None or getattr(_srv, 'routing_enums_pb2', None) is None:
        raise RuntimeError(f"OR-Tools not available: {_srv.ORTOOLS_IMPORT_ERROR or 'import failed'}")

    n = len(matrix)
    if n <= 1:
        return list(range(n))
    if n == 2:
        return [depot, 1 - depot]

    safe_depot = depot if 0 <= depot < n else 0

    # ── Build (N+1)-node model with dummy end node for open-path TSP ──
    #
    # Node indices 0..n-1 are real stops.
    # Node n is a dummy "end" node: cost FROM any real node TO dummy = 0,
    # cost FROM dummy TO any real node = very large (never used as source).
    # The vehicle starts at `safe_depot` and ends at node `n` (the dummy).
    # Since travelling to the dummy is free, OR-Tools ends at whichever
    # real stop produces the shortest total route.
    N = n + 1  # total nodes including dummy
    DUMMY = n
    LARGE = 10**9  # prohibitive cost — dummy is never a real origin

    # Scale matrix values to integers (OR-Tools requires int callbacks).
    # If the matrix contains floats (km), multiply by 1000 to preserve
    # three decimal places. If already in seconds (int), use as-is.
    scale = 1000 if any(isinstance(matrix[i][j], float) for i in range(min(2, n)) for j in range(min(2, n))) else 1

    # Build the raw n×n integer matrix first (vectorised via NumPy).
    import numpy as _np
    int_nxn = _np.asarray(matrix, dtype=_np.float64) * scale
    _np.clip(int_nxn, 0, None, out=int_nxn)
    int_nxn = int_nxn.astype(_np.int64, copy=False)

    # ── Matrix sparsification (single-driver, large-N only) ──
    # For large routes, clamp geographically absurd arcs to a large penalty so
    # OR-Tools never routes through them. Keeps all nodes reachable via the
    # depot (sparsify_matrix preserves depot row + col), preserves optimality
    # on real-world delivery data, and shrinks the effective search space.
    # Skipped when `locked_order` is set: forcing the locked sequence may
    # legitimately require an arc that sparsification would have pruned, so we
    # keep the full matrix to guarantee precedence feasibility.
    if n >= 20 and not locked_order:
        try:
            from vrp_solver import sparsify_matrix
            nonzero = int_nxn[int_nxn > 0]
            if nonzero.size > 0:
                threshold = int(3 * _np.median(nonzero))
                int_nxn, _n_pruned = sparsify_matrix(
                    int_nxn, prune_threshold_s=threshold, keep_depot=safe_depot
                )
        except Exception as _e:
            logger.warning(f"Matrix sparsification skipped (non-fatal): {_e}")

    # ── Expand to (N+1)×(N+1) with dummy-end-node scaffolding ──
    int_matrix = [[0] * N for _ in range(N)]
    for i in range(n):
        row = int_nxn[i]
        for j in range(n):
            int_matrix[i][j] = int(row[j])
        int_matrix[i][DUMMY] = 0      # free to end the route here
    for j in range(N):
        int_matrix[DUMMY][j] = LARGE   # dummy is never a real origin
    int_matrix[DUMMY][DUMMY] = 0

    # ── OR-Tools model ──
    manager = _srv.pywrapcp.RoutingIndexManager(N, 1, [safe_depot], [DUMMY])
    routing = _srv.pywrapcp.RoutingModel(manager)

    def cost_callback(from_index: int, to_index: int) -> int:
        return int_matrix[manager.IndexToNode(from_index)][manager.IndexToNode(to_index)]

    transit_idx = routing.RegisterTransitCallback(cost_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_idx)

    # Add cumulative dimension to track total cost (for diagnostics / constraints)
    routing.AddDimension(transit_idx, 0, LARGE, True, "Cost")

    # ── Late Freight precedence constraints ──
    # `locked_order` is a list of REAL node indices that MUST be visited in
    # this exact relative order (their immutable Sharpie `original_sequence`).
    # We add a unary "Position" dimension (cost 1 per arc) so each node's
    # CumulVar equals its 0-based visit position, then constrain
    # position(locked[k]) <= position(locked[k+1]) for every consecutive
    # locked pair. Unlocked "late freight" nodes carry no such constraint, so
    # PATH_CHEAPEST_ARC + GUIDED_LOCAL_SEARCH slot them into the cheapest gaps
    # freely. This never mutates any stop's `original_sequence` value.
    if locked_order and len(locked_order) >= 2:
        def _unit_callback(from_index: int, to_index: int) -> int:
            return 1
        unit_idx = routing.RegisterTransitCallback(_unit_callback)
        routing.AddDimension(unit_idx, 0, N, True, "Position")
        position_dim = routing.GetDimensionOrDie("Position")
        solver = routing.solver()
        for a, b in zip(locked_order, locked_order[1:]):
            if 0 <= a < n and 0 <= b < n:
                solver.Add(
                    position_dim.CumulVar(manager.NodeToIndex(a))
                    <= position_dim.CumulVar(manager.NodeToIndex(b))
                )

    # ── Search strategy ──
    search_params = _srv.pywrapcp.DefaultRoutingSearchParameters()
    search_params.local_search_metaheuristic = (
        _srv.routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    search_params.time_limit.FromMilliseconds(max(500, int(time_limit_ms)))

    # ── Warm-start: inject VROOM initial solution if provided ──
    solution = None
    if initial_indices and len(initial_indices) >= 2:
        try:
            # Strip depot from head — OR-Tools expects only the intermediate nodes
            warm_route = [i for i in initial_indices if i != safe_depot]
            initial_assignment = routing.ReadAssignmentFromRoutes([warm_route], True)
            if initial_assignment:
                # With a warm-start, skip greedy construction — jump straight to GLS
                search_params.first_solution_strategy = (
                    _srv.routing_enums_pb2.FirstSolutionStrategy.FIRST_UNBOUND_MIN_VALUE
                )
                solution = routing.SolveFromAssignmentWithParameters(
                    initial_assignment, search_params
                )
        except Exception:
            pass  # Fall through to cold-start below

    if not solution:
        # Cold-start: PATH_CHEAPEST_ARC greedy seed, then GLS improvement
        search_params.first_solution_strategy = (
            _srv.routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
        )
        solution = routing.SolveWithParameters(search_params)
    if not solution:
        raise ValueError("OR-Tools could not find a route solution")

    # ── Extract ordered real-node indices (exclude dummy end) ──
    ordered: List[int] = []
    index = routing.Start(0)
    while not routing.IsEnd(index):
        node = manager.IndexToNode(index)
        if node != DUMMY:
            ordered.append(node)
        index = solution.Value(routing.NextVar(index))

    # Safety: ensure every real node appears exactly once
    seen = set(ordered)
    for i in range(n):
        if i not in seen:
            ordered.append(i)

    return ordered

def _smart_insertion_fallback(
    stops: List[dict],
    matrix: List[List[float]],
    start_index: int,
    locked_order: List[int],
) -> List[dict]:
    """Deterministic late-freight insertion when the OR-Tools solver fails.

    Builds the base route from the depot followed by the locked stops in
    their immutable `original_sequence` order, then cheapest-inserts each
    unlocked "late freight" stop into the gap that adds the least travel
    cost (open-path, so appending at the end costs only the inbound leg).
    Never mutates `original_sequence` values.
    """
    n = len(stops)
    locked_set = set(locked_order)
    base: List[int] = []
    if start_index not in locked_set:
        base.append(start_index)
    base.extend(locked_order)
    late = [i for i in range(n) if i != start_index and i not in locked_set]
    for node in late:
        best_pos, best_delta = len(base), float("inf")
        for pos in range(1, len(base) + 1):
            prev = base[pos - 1]
            if pos < len(base):
                nxt = base[pos]
                delta = matrix[prev][node] + matrix[node][nxt] - matrix[prev][nxt]
            else:
                delta = matrix[prev][node]  # append at end (open path)
            if delta < best_delta:
                best_delta, best_pos = delta, pos
        base.insert(best_pos, node)
    return [stops[i] for i in base]
