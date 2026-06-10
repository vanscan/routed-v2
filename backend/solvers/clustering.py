"""Cluster-first / route-second optimization (geographic DBSCAN +
per-cluster inner solver + global 2-opt stitch).

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

from haversine import Unit, haversine

from solvers.heuristics import (
    _indices_by_identity,
    clarke_wright_savings,
    nearest_neighbor_optimize,
)
from solvers.local_search import three_opt_improve, two_opt_improve
from solvers.metaheuristics import (
    genetic_algorithm_optimize,
    simulated_annealing_optimize,
)
from solvers.ortools import ortools_optimize, ortools_tsp_solve
from solvers.pyvrp_adapter import pyvrp_tsp_solve

logger = logging.getLogger("server")

def _geographic_dbscan(stops: List[dict], eps_km: float = 0.8, min_samples: int = 2) -> List[int]:
    """DBSCAN clustering on geographic coordinates using haversine distance.
    Returns list of cluster labels per stop. -1 = noise (unassigned)."""
    n = len(stops)
    if n == 0:
        return []

    labels = [-1] * n
    cluster_id = 0
    visited = [False] * n

    for i in range(n):
        if visited[i]:
            continue
        visited[i] = True

        # Find all neighbors within eps
        neighbors = []
        for j in range(n):
            if i != j:
                d = haversine(
                    (stops[i]["latitude"], stops[i]["longitude"]),
                    (stops[j]["latitude"], stops[j]["longitude"]),
                    unit=Unit.KILOMETERS,
                )
                if d <= eps_km:
                    neighbors.append(j)

        if len(neighbors) < min_samples - 1:
            continue  # noise, will be assigned in post-processing

        # Start new cluster
        labels[i] = cluster_id
        seed_set = list(neighbors)
        idx = 0

        while idx < len(seed_set):
            j = seed_set[idx]
            idx += 1

            if not visited[j]:
                visited[j] = True
                j_neighbors = []
                for k in range(n):
                    if k != j:
                        d = haversine(
                            (stops[j]["latitude"], stops[j]["longitude"]),
                            (stops[k]["latitude"], stops[k]["longitude"]),
                            unit=Unit.KILOMETERS,
                        )
                        if d <= eps_km:
                            j_neighbors.append(k)

                if len(j_neighbors) >= min_samples - 1:
                    for k in j_neighbors:
                        if labels[k] == -1:
                            seed_set.append(k)

            if labels[j] == -1:
                labels[j] = cluster_id

        cluster_id += 1

    return labels


def _adaptive_eps(stops: List[dict]) -> float:
    """Compute adaptive DBSCAN eps based on stop density.
    Uses k-nearest-neighbor heuristic (k=4) to find natural cluster radius."""
    n = len(stops)
    if n <= 2:
        return 1.0

    # For each stop, find distance to 4th nearest neighbor
    k = min(4, n - 1)
    k_distances = []

    for i in range(n):
        dists = []
        for j in range(n):
            if i != j:
                d = haversine(
                    (stops[i]["latitude"], stops[i]["longitude"]),
                    (stops[j]["latitude"], stops[j]["longitude"]),
                    unit=Unit.KILOMETERS,
                )
                dists.append(d)
        dists.sort()
        k_distances.append(dists[k - 1] if len(dists) >= k else dists[-1])

    # Sort k-distances and find the "elbow" — we use the median as a robust estimate
    k_distances.sort()
    # Use the 60th percentile as eps (captures most natural clusters)
    eps = k_distances[int(n * 0.6)]
    # Clamp to reasonable delivery neighborhood sizes
    return max(0.3, min(2.5, eps))


def _postprocess_clusters(
    labels: List[int],
    stops: List[dict],
    max_cluster_size: int = 23,
    min_cluster_size: int = 2,
) -> List[List[int]]:
    """Post-process DBSCAN clusters:
    - Assign noise points to nearest cluster
    - Split oversized clusters (>max_cluster_size) for Mapbox API compliance
    - Merge tiny clusters into nearest neighbor
    Returns list of lists of global stop indices."""
    from collections import defaultdict

    clusters_map = defaultdict(list)
    noise = []

    for i, label in enumerate(labels):
        if label == -1:
            noise.append(i)
        else:
            clusters_map[label].append(i)

    cluster_list = list(clusters_map.values())

    # If no clusters found, treat everything as one cluster
    if not cluster_list:
        cluster_list = [list(range(len(stops)))]
        noise = []

    # Assign noise points to nearest cluster
    for ni in noise:
        best_ci = 0
        best_dist = float("inf")
        for ci, cluster in enumerate(cluster_list):
            for si in cluster:
                d = haversine(
                    (stops[ni]["latitude"], stops[ni]["longitude"]),
                    (stops[si]["latitude"], stops[si]["longitude"]),
                    unit=Unit.KILOMETERS,
                )
                if d < best_dist:
                    best_dist = d
                    best_ci = ci
        cluster_list[best_ci].append(ni)

    # Split oversized clusters using geographic k-means for spatially compact subclusters
    split_clusters = []
    for cluster in cluster_list:
        if len(cluster) <= max_cluster_size:
            split_clusters.append(cluster)
        else:
            # k-means split: divide into ceil(n/max_cluster_size) spatially compact groups
            import math as _math
            k = _math.ceil(len(cluster) / max_cluster_size)
            coords = [(stops[i]["latitude"], stops[i]["longitude"]) for i in cluster]

            # Initialize centroids using evenly spaced indices from sorted points
            sorted_by_lat = sorted(range(len(cluster)), key=lambda x: coords[x])
            centroids = [coords[sorted_by_lat[int(j * len(cluster) / k)]] for j in range(k)]

            for _ in range(15):  # k-means iterations
                buckets = [[] for _ in range(k)]
                for ci_local, idx in enumerate(cluster):
                    lat, lng = coords[ci_local]
                    best_k = 0
                    best_d = float("inf")
                    for ki in range(k):
                        d = (lat - centroids[ki][0]) ** 2 + (lng - centroids[ki][1]) ** 2
                        if d < best_d:
                            best_d = d
                            best_k = ki
                    buckets[best_k].append(idx)

                # Recompute centroids
                new_centroids = []
                for ki in range(k):
                    if buckets[ki]:
                        avg_lat = sum(stops[i]["latitude"] for i in buckets[ki]) / len(buckets[ki])
                        avg_lng = sum(stops[i]["longitude"] for i in buckets[ki]) / len(buckets[ki])
                        new_centroids.append((avg_lat, avg_lng))
                    else:
                        new_centroids.append(centroids[ki])

                if new_centroids == centroids:
                    break
                centroids = new_centroids

            for bucket in buckets:
                if bucket:
                    split_clusters.append(bucket)

    # Merge tiny clusters into nearest larger cluster (if it won't exceed max)
    final = []
    tiny = []
    for c in split_clusters:
        if len(c) < min_cluster_size:
            tiny.append(c)
        else:
            final.append(c)

    for tc in tiny:
        if not final:
            final.append(tc)
            continue
        tc_lat = sum(stops[i]["latitude"] for i in tc) / len(tc)
        tc_lng = sum(stops[i]["longitude"] for i in tc) / len(tc)

        best_ci = 0
        best_dist = float("inf")
        for ci, c in enumerate(final):
            if len(c) + len(tc) > max_cluster_size:
                continue
            c_lat = sum(stops[i]["latitude"] for i in c) / len(c)
            c_lng = sum(stops[i]["longitude"] for i in c) / len(c)
            d = haversine((tc_lat, tc_lng), (c_lat, c_lng), unit=Unit.KILOMETERS)
            if d < best_dist:
                best_dist = d
                best_ci = ci
        final[best_ci].extend(tc)

    return final if final else [list(range(len(stops)))]


def _order_clusters_tsp(
    clusters: List[List[int]],
    stops: List[dict],
    start_stop_index: int = 0,
) -> List[int]:
    """Order clusters using centroid nearest-neighbor + 2-opt.
    Returns list of cluster indices in visit order."""
    nc = len(clusters)
    if nc <= 1:
        return list(range(nc))

    # Compute centroids
    centroids = []
    for cluster in clusters:
        avg_lat = sum(stops[i]["latitude"] for i in cluster) / len(cluster)
        avg_lng = sum(stops[i]["longitude"] for i in cluster) / len(cluster)
        centroids.append((avg_lat, avg_lng))

    # Find the cluster that contains (or is nearest to) the start stop
    start_ci = 0
    for ci, cluster in enumerate(clusters):
        if start_stop_index in cluster:
            start_ci = ci
            break

    # Nearest-neighbor TSP on centroids
    visited = [False] * nc
    order = [start_ci]
    visited[start_ci] = True

    for _ in range(nc - 1):
        current = order[-1]
        best = -1
        best_dist = float("inf")
        for j in range(nc):
            if not visited[j]:
                d = haversine(centroids[current], centroids[j], unit=Unit.KILOMETERS)
                if d < best_dist:
                    best_dist = d
                    best = j
        if best != -1:
            order.append(best)
            visited[best] = True

    # 2-opt improvement on the cluster order
    improved = True
    while improved:
        improved = False
        for i in range(1, len(order) - 1):
            for j in range(i + 1, len(order)):
                # Calculate distance change if we reverse order[i:j+1]
                pi, pj = order[i - 1], order[i]
                qi, qj = order[j], order[(j + 1) % len(order)] if j + 1 < len(order) else order[0]

                old_d = (
                    haversine(centroids[pi], centroids[pj], unit=Unit.KILOMETERS)
                    + (haversine(centroids[qi], centroids[qj], unit=Unit.KILOMETERS) if j + 1 < len(order) else 0)
                )
                new_d = (
                    haversine(centroids[pi], centroids[qi], unit=Unit.KILOMETERS)
                    + (haversine(centroids[pj], centroids[qj], unit=Unit.KILOMETERS) if j + 1 < len(order) else 0)
                )
                if new_d < old_d - 0.001:
                    order[i : j + 1] = reversed(order[i : j + 1])
                    improved = True

    return order


def _convex_hull(points: List[tuple]) -> List[tuple]:
    """Compute convex hull of 2D points using Andrew's monotone chain.
    Points are (lng, lat) tuples. Returns hull vertices in CCW order."""
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _padded_polygon(hull: List[tuple], pad_deg: float = 0.0002) -> List[List[float]]:
    """Expand a convex hull outward by pad_deg (~20m at equator).
    Returns GeoJSON-compatible closed ring [[lng,lat], ...]."""
    import math

    if len(hull) < 2:
        # Single point → small octagon
        if hull:
            cx, cy = hull[0]
            return [
                [cx + pad_deg * math.cos(a), cy + pad_deg * math.sin(a)]
                for a in [i * math.pi / 4 for i in range(8)]
            ] + [[cx + pad_deg, cy]]
        return []

    if len(hull) == 2:
        # Line segment → diamond
        ax, ay = hull[0]
        bx, by = hull[1]
        dx, dy = bx - ax, by - ay
        length = math.sqrt(dx * dx + dy * dy) or 1e-8
        nx, ny = -dy / length * pad_deg, dx / length * pad_deg
        return [
            [ax - dx * 0.1 + nx, ay - dy * 0.1 + ny],
            [bx + dx * 0.1 + nx, by + dy * 0.1 + ny],
            [bx + dx * 0.1 - nx, by + dy * 0.1 - ny],
            [ax - dx * 0.1 - nx, ay - dy * 0.1 - ny],
            [ax - dx * 0.1 + nx, ay - dy * 0.1 + ny],
        ]

    # Compute centroid
    cx = sum(p[0] for p in hull) / len(hull)
    cy = sum(p[1] for p in hull) / len(hull)

    # Push each vertex outward from centroid
    padded = []
    for px, py in hull:
        dx, dy = px - cx, py - cy
        dist = math.sqrt(dx * dx + dy * dy) or 1e-8
        padded.append([px + dx / dist * pad_deg, py + dy / dist * pad_deg])
    padded.append(padded[0])  # close the ring
    return padded


# 15 distinct cluster colors — semi-transparent fills with solid borders
CLUSTER_COLORS = [
    {"fill": "rgba(59, 130, 246, 0.25)", "border": "rgba(59, 130, 246, 0.8)"},   # blue
    {"fill": "rgba(239, 68, 68, 0.25)", "border": "rgba(239, 68, 68, 0.8)"},     # red
    {"fill": "rgba(16, 185, 129, 0.25)", "border": "rgba(16, 185, 129, 0.8)"},   # emerald
    {"fill": "rgba(245, 158, 11, 0.25)", "border": "rgba(245, 158, 11, 0.8)"},   # amber
    {"fill": "rgba(168, 85, 247, 0.25)", "border": "rgba(168, 85, 247, 0.8)"},   # purple
    {"fill": "rgba(236, 72, 153, 0.25)", "border": "rgba(236, 72, 153, 0.8)"},   # pink
    {"fill": "rgba(20, 184, 166, 0.25)", "border": "rgba(20, 184, 166, 0.8)"},   # teal
    {"fill": "rgba(251, 146, 60, 0.25)", "border": "rgba(251, 146, 60, 0.8)"},   # orange
    {"fill": "rgba(99, 102, 241, 0.25)", "border": "rgba(99, 102, 241, 0.8)"},   # indigo
    {"fill": "rgba(34, 197, 94, 0.25)", "border": "rgba(34, 197, 94, 0.8)"},     # green
    {"fill": "rgba(244, 63, 94, 0.25)", "border": "rgba(244, 63, 94, 0.8)"},     # rose
    {"fill": "rgba(6, 182, 212, 0.25)", "border": "rgba(6, 182, 212, 0.8)"},     # cyan
    {"fill": "rgba(234, 179, 8, 0.25)", "border": "rgba(234, 179, 8, 0.8)"},     # yellow
    {"fill": "rgba(139, 92, 246, 0.25)", "border": "rgba(139, 92, 246, 0.8)"},   # violet
    {"fill": "rgba(14, 165, 233, 0.25)", "border": "rgba(14, 165, 233, 0.8)"},   # sky
]


def _or_opt_1_improve(indices: List[int], matrix: List[List[float]]) -> List[int]:
    """Or-opt-1: Relocate single stops to better positions using the road distance matrix.
    Catches cases where stops on the same street get split by stops on adjacent streets."""
    n = len(indices)
    if n <= 3:
        return indices

    best = indices[:]

    def route_cost(r):
        return sum(matrix[r[i]][r[i + 1]] for i in range(len(r) - 1))

    current_cost = route_cost(best)
    improved = True
    iterations = 0

    while improved and iterations < 5:
        improved = False
        iterations += 1
        for i in range(1, len(best)):  # Skip index 0 (start point)
            stop = best[i]
            # Remove stop from current position
            remaining = best[:i] + best[i + 1:]
            # Cost without this stop
            remove_save = (
                matrix[best[i - 1]][best[i]]
                + (matrix[best[i]][best[i + 1]] if i + 1 < len(best) else 0)
                - (matrix[best[i - 1]][best[i + 1]] if i + 1 < len(best) else 0)
            )

            best_j = -1
            best_insert_cost = float("inf")

            for j in range(len(remaining)):
                # Try inserting after position j in remaining
                if j + 1 < len(remaining):
                    insert_cost = (
                        matrix[remaining[j]][stop]
                        + matrix[stop][remaining[j + 1]]
                        - matrix[remaining[j]][remaining[j + 1]]
                    )
                else:
                    insert_cost = matrix[remaining[j]][stop]

                if insert_cost < best_insert_cost:
                    best_insert_cost = insert_cost
                    best_j = j

            # Check if relocating improves total cost
            if best_j >= 0 and best_insert_cost < remove_save - 0.001:
                new_route = remaining[:best_j + 1] + [stop] + remaining[best_j + 1:]
                new_cost = route_cost(new_route)
                if new_cost < current_cost - 0.001:
                    best = new_route
                    current_cost = new_cost
                    improved = True
                    break  # Restart from beginning after improvement

    return best


def _build_cluster_info(
    ordered_clusters: List[List[int]],
    stops: List[dict],
) -> List[dict]:
    """Build GeoJSON-ready cluster visualization data with convex hull polygons."""
    cluster_info = []
    for visit_order, cluster_indices in enumerate(ordered_clusters):
        points = [(stops[i]["longitude"], stops[i]["latitude"]) for i in cluster_indices]
        hull = _convex_hull(points)
        polygon = _padded_polygon(hull)

        centroid_lat = sum(stops[i]["latitude"] for i in cluster_indices) / len(cluster_indices)
        centroid_lng = sum(stops[i]["longitude"] for i in cluster_indices) / len(cluster_indices)
        color = CLUSTER_COLORS[visit_order % len(CLUSTER_COLORS)]

        cluster_info.append({
            "id": visit_order,
            "visit_order": visit_order,
            "stop_count": len(cluster_indices),
            "centroid": {"latitude": round(centroid_lat, 6), "longitude": round(centroid_lng, 6)},
            "polygon": polygon,
            "fill_color": color["fill"],
            "border_color": color["border"],
            "label": f"Zone {visit_order + 1}",
        })
    return cluster_info


def _run_inner_algorithm(
    stops: List[dict],
    matrix: List[List[float]],
    start_index: int,
    time_limit: int,
    algorithm: str,
) -> List[dict]:
    """Run a specific optimization algorithm on a subset of stops.
    Used within cluster_first to apply the user's preferred algorithm per cluster.
    Applies post-optimization 2-opt + or-opt using the road distance matrix
    to catch local swaps the main solver may have missed (e.g., grouping same-street stops)."""
    import server as _srv  # noqa: WPS433
    result = None
    try:
        if algorithm == "ortools" and _srv.ORTOOLS_AVAILABLE and getattr(_srv, "pywrapcp", None):
            # Use ortools_tsp_solve directly — the matrix passed in is already the
            # correct type (duration seconds when cluster_first uses OR-Tools inner)
            time_limit_ms = max(1000, time_limit * 1000)
            indices = ortools_tsp_solve(matrix, depot=start_index, time_limit_ms=time_limit_ms)
            result = [stops[i] for i in indices]
        elif algorithm == "pyvrp" and _srv.PYVRP_AVAILABLE:
            pyvrp_seconds = max(1.0, min(2.0, len(stops) * 0.05))
            indices = pyvrp_tsp_solve(matrix, depot=start_index, time_limit_seconds=pyvrp_seconds)
            result = [stops[i] for i in indices]
        elif algorithm == "alns":
            try:
                result = alns_hybrid_optimize(stops, matrix, start_index=start_index, time_limit_seconds=time_limit)
            except NameError:
                logger.warning("ALNS not available, falling back to OR-Tools")
                if _srv.ORTOOLS_AVAILABLE and getattr(_srv, "pywrapcp", None):
                    result = ortools_optimize(stops, matrix, start_index, time_limit)
        elif algorithm == "simulated_annealing":
            result = simulated_annealing_optimize(stops, matrix, start_index)
        elif algorithm == "genetic":
            result = genetic_algorithm_optimize(stops, matrix, start_index)
        elif algorithm == "clarke_wright":
            result = clarke_wright_savings(stops, matrix, start_index)
    except Exception as exc:
        logger.warning("Inner algorithm '%s' failed, falling back to NN+2-opt: %s", algorithm, exc)

    if result is None:
        nn = nearest_neighbor_optimize(stops, matrix, start_index)
        ri = _indices_by_identity(stops, nn)
        result = [stops[i] for i in two_opt_improve(ri, matrix)]

    # Post-optimization: apply road-distance or-opt-1 then 2-opt to catch missed local swaps
    # This fixes cases where stops on the same street get split by stops on adjacent streets
    if len(result) > 3:
        indices = _indices_by_identity(stops, result)
        indices = _or_opt_1_improve(indices, matrix)
        indices = two_opt_improve(indices, matrix)
        result = [stops[i] for i in indices]

    return result


def _global_two_opt_pass(optimized: List[dict], max_iterations: int = 3) -> List[dict]:
    """Apply or-opt-1 + 2-opt on the full stitched route using haversine distances.
    Fixes cross-cluster boundary inefficiencies:
    - or-opt-1 relocates single stops to better positions (e.g., moving stop 46 from
      between 45→47 to after 48, avoiding an unnecessary south→north detour)
    - 2-opt reverses segments to uncross route lines
    - 3-opt (large routes only): non-reversing segment swap to escape 2-opt
      local optima on routes ≥150 stops where boundary stitching tends to
      leave a few residual cross-cluster zig-zags that 2-opt can't fix.
    """
    n = len(optimized)
    if n <= 3:
        return optimized

    # Large routes (≥150 stops) get more aggressive polishing: doubling the
    # iteration budget (3 → 6) gives or-opt and 2-opt enough runway to chase
    # cross-cluster relocations to convergence on long routes, where each
    # iteration only nudges a handful of stops at a time.
    if n >= 150:
        max_iterations = max(max_iterations, 6)

    # Build haversine matrix for the stitched route
    matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            d = haversine(
                (optimized[i]["latitude"], optimized[i]["longitude"]),
                (optimized[j]["latitude"], optimized[j]["longitude"]),
                unit=Unit.KILOMETERS,
            )
            matrix[i][j] = d
            matrix[j][i] = d

    indices = list(range(n))
    best_dist = sum(matrix[indices[i]][indices[i + 1]] for i in range(n - 1))

    # Phase 1: Global or-opt-1 — relocate individual stops across cluster boundaries
    for _iter in range(max_iterations):
        improved = False
        for i in range(1, len(indices)):
            stop_idx = indices[i]
            # Cost of edges touching this stop
            prev_idx = indices[i - 1]
            next_idx = indices[i + 1] if i + 1 < len(indices) else None

            edge_before = matrix[prev_idx][stop_idx]
            edge_after = matrix[stop_idx][next_idx] if next_idx is not None else 0
            edge_skip = matrix[prev_idx][next_idx] if next_idx is not None else 0
            remove_save = edge_before + edge_after - edge_skip

            if remove_save < 0.02:  # Not worth relocating if removal doesn't save much
                continue

            best_j = -1
            best_delta = 0

            # Try inserting this stop at every other position (limited window for speed)
            remaining = indices[:i] + indices[i + 1:]
            for j in range(max(0, i - 40), min(len(remaining), i + 40)):
                a = remaining[j]
                b = remaining[j + 1] if j + 1 < len(remaining) else None
                old_edge = matrix[a][b] if b is not None else 0
                new_edge = matrix[a][stop_idx] + (matrix[stop_idx][b] if b is not None else 0)
                insert_cost = new_edge - old_edge
                delta = remove_save - insert_cost
                if delta > best_delta + 0.01:
                    best_delta = delta
                    best_j = j

            if best_j >= 0:
                # Perform the relocation
                indices.pop(i)
                actual_j = best_j if best_j < i else best_j
                indices.insert(actual_j + 1, stop_idx)
                improved = True
                break  # Restart scan after improvement

        if not improved:
            break

    # Phase 2: Global 2-opt — reverse segments to uncross route lines
    for _ in range(max_iterations):
        improved = False
        for i in range(1, n - 1):
            for j in range(i + 1, min(i + 60, n)):
                d_old = matrix[indices[i - 1]][indices[i]] + (matrix[indices[j]][indices[j + 1]] if j + 1 < n else 0)
                d_new = matrix[indices[i - 1]][indices[j]] + (matrix[indices[i]][indices[j + 1]] if j + 1 < n else 0)
                if d_new < d_old - 0.01:
                    indices[i:j + 1] = reversed(indices[i:j + 1])
                    improved = True
        if not improved:
            break

    # Phase 3: 3-opt polish (large routes only). On routes ≥150 stops the
    # 2-opt pass above usually plateaus with a few residual cross-cluster
    # zig-zags that the reversal-only neighbourhood can't escape.
    # `three_opt_improve` swaps non-adjacent segments without reversing
    # them, which is asymmetric-safe and exact on this haversine matrix.
    # We deliberately keep 3-opt off for smaller routes — the 2-opt window
    # above already converges, and 3-opt's O(n³) inner loop would dominate
    # the per-request budget without measurable quality gain.
    if n >= 150:
        polished = three_opt_improve(indices, matrix, max_iterations=3)
        polished_dist = sum(matrix[polished[i]][polished[i + 1]] for i in range(n - 1))
        current_dist = sum(matrix[indices[i]][indices[i + 1]] for i in range(n - 1))
        if polished_dist < current_dist - 0.01:
            indices = polished
            logger.info(
                "Global 3-opt polish improved route: %.2f km → %.2f km",
                current_dist, polished_dist,
            )

    new_dist = sum(matrix[indices[i]][indices[i + 1]] for i in range(n - 1))
    if new_dist < best_dist:
        logger.info("Global or-opt+2-opt improved route: %.2f km → %.2f km (saved %.2f km)", best_dist, new_dist, best_dist - new_dist)
        return [optimized[i] for i in indices]

    return optimized


async def cluster_first_optimize(
    stops: List[dict],
    distance_matrix: List[List[float]],
    start_index: int = 0,
    time_limit_seconds: int = 30,
    inner_algorithm: str = "ortools",
) -> tuple:
    """Cluster-first route-second optimization.

    Guarantees spatially coherent routing by:
    1. DBSCAN geographic clustering into natural neighborhoods
    2. Inter-cluster ordering via centroid TSP with 2-opt
    3. Intra-cluster optimization with Mapbox road distances + user's preferred algorithm
    4. Smart entry/exit stitching between adjacent clusters
    5. Global 2-opt pass to fix cross-boundary inefficiencies

    Args:
        inner_algorithm: Algorithm to use within each cluster (ortools, alns, etc.)

    Returns (optimized_stops, cluster_info) tuple.
    """
    from server import (  # noqa: WPS433
        _osrm_duration_matrix,
        calculate_duration_matrix,
        calculate_road_distance_matrix,
    )
    n = len(stops)
    if n <= 25:
        # Small enough for a single pass — no cluster visualization
        result = _run_inner_algorithm(stops, distance_matrix, start_index, time_limit_seconds, inner_algorithm)
        return result, []

    # Step 1: Geographic clustering
    eps = _adaptive_eps(stops)
    labels = _geographic_dbscan(stops, eps_km=eps, min_samples=2)
    clusters = _postprocess_clusters(labels, stops, max_cluster_size=23, min_cluster_size=2)
    logger.info(
        "Cluster-first (%s): %d clusters from %d stops (eps=%.2f km, sizes=%s)",
        inner_algorithm, len(clusters), n, eps,
        [len(c) for c in clusters],
    )

    # Step 2: Order clusters using centroid TSP
    cluster_order = _order_clusters_tsp(clusters, stops, start_stop_index=start_index)
    ordered_clusters = [clusters[i] for i in cluster_order]

    # Build cluster visualization data
    cluster_info = _build_cluster_info(ordered_clusters, stops)

    # Step 3 & 4: Optimize within each cluster and stitch
    all_optimized: List[dict] = []
    previous_exit_global = start_index
    # per_cluster_time kept as reference for future time-budgeted cluster solves.
    _ = max(5, time_limit_seconds // max(1, len(ordered_clusters)))

    for ci, cluster_indices in enumerate(ordered_clusters):
        cluster_stops = [stops[gi] for gi in cluster_indices]

        if len(cluster_stops) == 1:
            all_optimized.extend(cluster_stops)
            previous_exit_global = cluster_indices[0]
            continue

        # Scale per-cluster OR-Tools time based on cluster size
        # Small clusters are trivially solved — 1 second is plenty
        # OR-Tools GUIDED_LOCAL_SEARCH uses the FULL time limit regardless of problem size
        if len(cluster_stops) <= 5:
            cluster_time = 1
        elif len(cluster_stops) <= 12:
            cluster_time = 2
        elif len(cluster_stops) <= 18:
            cluster_time = 3
        else:
            cluster_time = 5

        # Get cluster cost matrix.
        # OR-Tools inner algorithm uses DURATION (seconds) for time-optimal routing.
        # Other algorithms use road DISTANCE (km).
        if inner_algorithm == "ortools":
            # Try OSRM duration matrix first — same primary source as the main
            # `/optimize` pipeline (server.py line 5120). `calculate_duration_matrix`
            # below is the Mapbox/haversine FALLBACK path; calling it directly
            # silently degraded routing quality whenever a cluster had >25 stops
            # (Mapbox Matrix API limit → haversine straight-line distances), or
            # whenever Mapbox was rate-limited. Now cluster_first gets the same
            # road-aware OSRM data as VROOM/OR-Tools/LKH on the top-level path.
            cluster_matrix = await _osrm_duration_matrix(cluster_stops)
            if not cluster_matrix:
                cluster_matrix = await calculate_duration_matrix(cluster_stops)
        else:
            # `calculate_road_distance_matrix` already tries OSRM first internally
            # (see server.py line 2812), so non-ortools inner algorithms have
            # always had the OSRM-first path. No change needed here.
            cluster_matrix = await calculate_road_distance_matrix(cluster_stops)

        # Determine entry point: closest stop to previous cluster's exit
        entry_local = 0
        if ci == 0:
            # First cluster: find the start stop
            for li, gi in enumerate(cluster_indices):
                if gi == start_index:
                    entry_local = li
                    break
        else:
            prev_stop = stops[previous_exit_global]
            min_d = float("inf")
            for li, gi in enumerate(cluster_indices):
                d = haversine(
                    (prev_stop["latitude"], prev_stop["longitude"]),
                    (stops[gi]["latitude"], stops[gi]["longitude"]),
                    unit=Unit.KILOMETERS,
                )
                if d < min_d:
                    min_d = d
                    entry_local = li

        # Optimize within this cluster using the user's preferred algorithm
        optimized = _run_inner_algorithm(
            cluster_stops, cluster_matrix,
            start_index=entry_local,
            time_limit=cluster_time,
            algorithm=inner_algorithm,
        )

        all_optimized.extend(optimized)

        # Track exit point (last stop in this cluster) for stitching to next cluster
        last_stop = optimized[-1]
        for gi in range(n):
            if stops[gi] is last_stop:
                previous_exit_global = gi
                break

    # Step 5: Global 2-opt pass to fix cross-cluster boundary inefficiencies
    all_optimized = _global_two_opt_pass(all_optimized, max_iterations=3)

    return all_optimized, cluster_info
