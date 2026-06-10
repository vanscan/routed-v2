"""Construction heuristics: nearest-neighbour and Clarke-Wright savings.

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
from typing import List, Sequence

from solvers.coord_clustering import cluster_aware_solve

logger = logging.getLogger("server")

def nearest_neighbor_optimize(stops: List[dict], distance_matrix: List[List[float]], start_index: int = 0) -> List[dict]:
    """Basic nearest neighbor optimization - greedy approach"""
    if len(stops) <= 1:
        return stops
    
    n = len(stops)
    visited = [False] * n
    route = [start_index]
    visited[start_index] = True
    
    for _ in range(n - 1):
        current = route[-1]
        nearest = -1
        nearest_dist = float('inf')
        
        for j in range(n):
            if not visited[j] and distance_matrix[current][j] < nearest_dist:
                nearest = j
                nearest_dist = distance_matrix[current][j]
        
        if nearest != -1:
            route.append(nearest)
            visited[nearest] = True
    
    return [stops[i] for i in route]

def calculate_route_distance(route: List[int], matrix: List[List[float]]) -> float:
    """Sum of edge costs along a route (list of indices into the cost matrix)."""
    return sum(matrix[route[i]][route[i + 1]] for i in range(len(route) - 1))


# ─── Greedy fallback (Nearest Neighbor with super-node clustering) ──────

def _nearest_neighbor_indices(
    matrix: Sequence[Sequence[float]],
    depot: int = 0,
    **_kwargs: object,
) -> List[int]:
    """Pure index-space NN. Picks the `min` outgoing edge from the current
    node, ignoring already-visited indices. O(n²) — no warm-starts, no
    randomness, fully deterministic.

    `**_kwargs` swallows extra args so this can be passed straight to
    ``cluster_aware_solve`` (which forwards solver kwargs verbatim)."""
    n = len(matrix)
    if n == 0:
        return []
    if n == 1:
        return [depot]
    visited = [False] * n
    route = [depot]
    visited[depot] = True
    for _ in range(n - 1):
        current = route[-1]
        best_idx = -1
        best_cost = float("inf")
        row = matrix[current]
        for j in range(n):
            if visited[j]:
                continue
            c = row[j]
            if c < best_cost:
                best_cost = c
                best_idx = j
        if best_idx < 0:
            break
        route.append(best_idx)
        visited[best_idx] = True
    return route

def solve_nearest_neighbor(
    distance_matrix: Sequence[Sequence[float]],
    stops: List[dict],
    start_index: int = 0,
) -> List[dict]:
    """Bulletproof greedy fallback for the routing pipeline.

    Pipeline:
      1. Wrap the index-space NN in ``cluster_aware_solve`` so identical-
         coordinate "super nodes" (multi-parcel doorsteps) are collapsed
         before the solver runs and re-expanded sequentially after — same
         protection PyVRP gets internally. Prevents the "Zero-Cost
         Interleaving" bug where the greedy picks A1 → B → A2 because
         the inter-parcel edge cost is 0.
      2. If the matrix degenerates (empty, no stops, identical depot) the
         function falls back to returning the input list unchanged.

    Why a wrapper around the existing ``nearest_neighbor_optimize``:
        ``nearest_neighbor_optimize`` works in stop-dict space and can't
        be passed to ``cluster_aware_solve`` directly. ``_nearest_neighbor_indices``
        is the index-space twin that integrates with the cluster pipeline.
        Returning ``List[dict]`` here matches every other top-level solver
        in this file (PyVRP, ALNS, OR-Tools, etc.) so the call sites are
        drop-in-compatible.

    Args:
        distance_matrix: square matrix in seconds (or any cost). Driver-
            provided OSRM/Mapbox `duration_matrix` is the right input.
        stops: list of stop dicts with `latitude`/`longitude`.
        start_index: depot index (driver location), default 0.
    """
    if not stops or len(stops) == 1:
        return list(stops)
    indices = cluster_aware_solve(
        _nearest_neighbor_indices,
        distance_matrix,
        start_index,
        stops,
    )
    return [stops[i] for i in indices]

def _indices_by_identity(source_list: List[dict], ordered: List[dict]) -> List[int]:
    """Map each dict in `ordered` back to its position in `source_list` using
    Python object identity (`id()`), not equality.

    Why: every pre-existing call site used ``[source_list.index(s) for s in ordered]``,
    which returns the FIRST equal dict. For users with duplicate-address stops
    (same lat/lng, different stop ids) that silently collapses two different
    stops onto the same index → the optimizer output loses a real stop.

    Since every solver in this file returns the same dict *references* that
    were passed in (see e.g. ``nearest_neighbor_optimize``: ``return [stops[i] for i in route]``),
    `id()` identifies each dict uniquely regardless of duplicate values.
    """
    id_map = {id(item): idx for idx, item in enumerate(source_list)}
    return [id_map[id(item)] for item in ordered]

def clarke_wright_savings(stops: List[dict], distance_matrix: List[List[float]], 
                          depot_index: int = 0) -> List[dict]:
    """Clarke-Wright Savings Algorithm - classic VRP algorithm
    Treats first stop as depot and builds routes from there"""
    n = len(stops)
    if n <= 2:
        return stops
    
    # Calculate savings for each pair of customers
    savings = []
    for i in range(n):
        if i == depot_index:
            continue
        for j in range(i + 1, n):
            if j == depot_index:
                continue
            # Saving = distance(depot,i) + distance(depot,j) - distance(i,j)
            s = distance_matrix[depot_index][i] + distance_matrix[depot_index][j] - distance_matrix[i][j]
            savings.append((s, i, j))
    
    # Sort by savings (descending)
    savings.sort(reverse=True)
    
    # Build routes
    routes = [[i] for i in range(n) if i != depot_index]
    customer_route = {i: i - (1 if i > depot_index else 0) for i in range(n) if i != depot_index}
    
    for s, i, j in savings:
        route_i = customer_route.get(i)
        route_j = customer_route.get(j)
        
        if route_i is None or route_j is None or route_i == route_j:
            continue
        
        # Check if i and j are at the ends of their routes
        ri = routes[route_i]
        rj = routes[route_j]
        
        if (ri[0] == i or ri[-1] == i) and (rj[0] == j or rj[-1] == j):
            # Merge routes
            if ri[-1] == i and rj[0] == j:
                new_route = ri + rj
            elif ri[0] == i and rj[-1] == j:
                new_route = rj + ri
            elif ri[-1] == i and rj[-1] == j:
                new_route = ri + rj[::-1]
            else:
                new_route = ri[::-1] + rj
            
            # Update routes
            routes[route_i] = new_route
            routes[route_j] = []
            
            # Update customer_route mapping
            for c in new_route:
                customer_route[c] = route_i
    
    # Combine all non-empty routes
    final_route = [depot_index]
    for route in routes:
        if route:
            final_route.extend(route)
    
    return [stops[i] for i in final_route]
