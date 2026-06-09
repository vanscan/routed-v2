"""Optimize-adjacent tools — algorithm recommendation.

    GET /optimize/recommend  → analyse the caller's route and suggest an algorithm

This module holds optimize-*adjacent* endpoints: they live in the /optimize/*
namespace and read a core helper, but contain no solver-cascade logic. The
solver cascade and its matrices stay in server.py; this endpoint lazy-imports
the read-only `calculate_distance_matrix` helper from `server` at request time.

The pure analysis helpers `_analyze_route_characteristics` and
`_recommend_algorithm` are private to this endpoint and move here verbatim.

NOTE on the auth dependency: the endpoint depends on `server.get_current_user`
directly (consistent with routes/export.py and routes/meta.py) so any TestClient
override via `app.dependency_overrides[server.get_current_user]` takes effect.
`get_current_user` is defined early in server.py, before this module loads at
the include-router block, so the module-level import is safe.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends

from server import get_current_user  # noqa: E402 — defined before this module loads

logger = logging.getLogger("server")
router = APIRouter()


def _analyze_route_characteristics(stops: List[dict], distance_matrix: List[List[float]]) -> Dict[str, Any]:
    """Analyze geographic characteristics of the route to inform algorithm selection."""
    n = len(stops)
    if n < 2:
        return {"stop_count": n}

    # Collect all pairwise distances
    all_dists = []
    for i in range(n):
        for j in range(i + 1, n):
            all_dists.append(distance_matrix[i][j])

    avg_dist = sum(all_dists) / len(all_dists) if all_dists else 0
    max_dist = max(all_dists) if all_dists else 0

    # Geographic spread: max distance between any two stops
    spread_km = max_dist

    # Nearest-neighbor distances (how clustered stops are)
    nn_dists = []
    for i in range(n):
        nearest = min(distance_matrix[i][j] for j in range(n) if j != i)
        nn_dists.append(nearest)
    avg_nn = sum(nn_dists) / len(nn_dists) if nn_dists else 0

    # Cluster density: ratio of avg nearest-neighbor dist to avg pairwise dist
    # Low ratio = tightly clustered, high ratio = evenly spread
    cluster_ratio = avg_nn / avg_dist if avg_dist > 0 else 1.0

    # Count how many "clusters" exist using a simple threshold
    # Stops within avg_nn * 2 of each other are in the same cluster
    threshold = avg_nn * 3
    visited = [False] * n
    cluster_count = 0
    for i in range(n):
        if visited[i]:
            continue
        cluster_count += 1
        stack = [i]
        while stack:
            cur = stack.pop()
            if visited[cur]:
                continue
            visited[cur] = True
            for j in range(n):
                if not visited[j] and distance_matrix[cur][j] < threshold:
                    stack.append(j)

    return {
        "stop_count": n,
        "spread_km": round(spread_km, 2),
        "avg_distance_km": round(avg_dist, 3),
        "avg_nn_distance_km": round(avg_nn, 3),
        "cluster_ratio": round(cluster_ratio, 4),
        "cluster_count": cluster_count,
        "complexity": "low" if n < 15 else "medium" if n < 60 else "high",
    }


def _recommend_algorithm(chars: Dict[str, Any]) -> Dict[str, Any]:
    """Recommend the best algorithm based on route characteristics."""
    n = chars["stop_count"]
    cluster_ratio = chars.get("cluster_ratio", 0.5)
    cluster_count = chars.get("cluster_count", 1)
    # complexity = chars.get("complexity", "medium")  # reserved for future tuning

    if n < 2:
        return {"algorithm": "none", "confidence": 1.0, "reasoning": "Need at least 2 stops"}

    # Decision tree based on empirical algorithm strengths
    if n <= 8:
        return {
            "algorithm": "two_opt",
            "confidence": 0.95,
            "reasoning": f"With only {n} stops, 2-Opt finds near-optimal solutions instantly.",
            "alternatives": ["nearest_neighbor"],
        }

    if n <= 20:
        if cluster_count <= 2:
            return {
                "algorithm": "ortools",
                "confidence": 0.9,
                "reasoning": f"{n} stops in {cluster_count} cluster(s) — OR-Tools handles this scale perfectly with exact-like solutions.",
                "alternatives": ["two_opt", "simulated_annealing"],
            }
        else:
            return {
                "algorithm": "ortools",
                "confidence": 0.85,
                "reasoning": f"{n} stops across {cluster_count} clusters — OR-Tools balances quality and speed well here.",
                "alternatives": ["alns", "simulated_annealing"],
            }

    if n <= 60:
        if cluster_count >= 4:
            return {
                "algorithm": "alns",
                "confidence": 0.88,
                "reasoning": f"{n} stops across {cluster_count} clusters — ALNS excels at multi-cluster routes with its destroy-repair operators.",
                "alternatives": ["ortools", "simulated_annealing"],
            }
        else:
            return {
                "algorithm": "ortools",
                "confidence": 0.85,
                "reasoning": f"{n} stops in {cluster_count} cluster(s) — OR-Tools provides strong results at this scale.",
                "alternatives": ["alns", "two_opt"],
            }

    # 60+ stops — large scale
    if cluster_ratio < 0.15:
        # Very tightly clustered
        return {
            "algorithm": "alns",
            "confidence": 0.92,
            "reasoning": f"{n} tightly clustered stops (ratio: {cluster_ratio:.2f}) — ALNS's adaptive operators handle dense routes best.",
            "alternatives": ["simulated_annealing", "ortools"],
        }
    elif cluster_count >= 5:
        return {
            "algorithm": "alns",
            "confidence": 0.9,
            "reasoning": f"{n} stops across {cluster_count} distinct clusters — ALNS's segment-based destroy operators are ideal for multi-cluster optimization.",
            "alternatives": ["ortools", "simulated_annealing"],
        }
    else:
        return {
            "algorithm": "alns",
            "confidence": 0.85,
            "reasoning": f"Large route with {n} stops — ALNS provides the best quality for complex large-scale optimization.",
            "alternatives": ["ortools", "simulated_annealing"],
        }


@router.get("/optimize/recommend")
async def recommend_algorithm(current_user=Depends(get_current_user)):
    """Analyze current route and recommend the best optimization algorithm."""
    from server import db, calculate_distance_matrix  # noqa: WPS433

    all_stops = await db.stops.find({"user_id": current_user.user_id}, {"_id": 0}).sort("order", 1).to_list(1000)
    stops = [s for s in all_stops if not s.get("completed")]

    if len(stops) < 2:
        return {
            "recommendation": {"algorithm": "none", "confidence": 1.0, "reasoning": "Need at least 2 incomplete stops"},
            "characteristics": {"stop_count": len(stops)},
        }

    distance_matrix = calculate_distance_matrix(stops)
    chars = _analyze_route_characteristics(stops, distance_matrix)
    rec = _recommend_algorithm(chars)

    return {
        "recommendation": rec,
        "characteristics": chars,
    }
