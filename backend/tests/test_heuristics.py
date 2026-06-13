"""Tests for solvers/heuristics.py — construction heuristics.

Covers:
  - nearest_neighbor_optimize  (stop-dict space greedy NN)
  - _nearest_neighbor_indices  (pure index-space NN)
  - calculate_route_distance   (edge-cost accumulator)
  - clarke_wright_savings       (CW VRP algorithm)
  - solve_nearest_neighbor      (cluster-aware pipeline wrapper)

No server imports are exercised; all functions tested here are pure
algorithms that take plain Python lists and dicts.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from solvers.heuristics import (  # noqa: E402
    _nearest_neighbor_indices,
    calculate_route_distance,
    clarke_wright_savings,
    nearest_neighbor_optimize,
    solve_nearest_neighbor,
)


# ── helpers ──────────────────────────────────────────────────────────────────


def linear_matrix(n: int):
    """n×n matrix where cost(i,j) = abs(i-j).

    Optimal NN tour from 0 is always 0,1,2,...,n-1 because each
    successive node is exactly 1 unit away from the previous one.
    """
    return [[abs(i - j) for j in range(n)] for i in range(n)]


def _stops(n: int, base_lat: float = 0.0, base_lng: float = 0.0):
    """Create n stop dicts with distinct coordinates."""
    return [
        {"id": f"s{i}", "latitude": base_lat + i * 0.001, "longitude": base_lng}
        for i in range(n)
    ]


def stop(sid: str, lat: float = 0.0, lng: float = 0.0) -> dict:
    return {"id": sid, "latitude": lat, "longitude": lng}


# ── calculate_route_distance ─────────────────────────────────────────────────


class TestCalculateRouteDistance:
    def test_empty_route_returns_zero(self):
        m = linear_matrix(4)
        assert calculate_route_distance([], m) == 0.0

    def test_single_node_returns_zero(self):
        m = linear_matrix(4)
        assert calculate_route_distance([2], m) == 0.0

    def test_two_node_route_returns_single_edge(self):
        m = linear_matrix(4)
        # cost(0,3) = abs(0-3) = 3
        assert calculate_route_distance([0, 3], m) == 3.0

    def test_known_linear_tour_distance(self):
        # 0→1→2→3 with abs(i-j) matrix: 1+1+1 = 3
        m = linear_matrix(4)
        assert calculate_route_distance([0, 1, 2, 3], m) == 3.0

    def test_non_sequential_tour_has_higher_distance(self):
        m = linear_matrix(4)
        # 0→2→1→3: 2+1+2 = 5 > 3
        assert calculate_route_distance([0, 2, 1, 3], m) == 5.0


# ── _nearest_neighbor_indices ─────────────────────────────────────────────────


class TestNearestNeighborIndices:
    def test_empty_matrix_returns_empty(self):
        assert _nearest_neighbor_indices([]) == []

    def test_single_element_returns_depot(self):
        assert _nearest_neighbor_indices([[0]], depot=0) == [0]

    def test_linear_4node_from_depot_0(self):
        # Each hop picks the closest unvisited; linear matrix → 0,1,2,3
        result = _nearest_neighbor_indices(linear_matrix(4), depot=0)
        assert result == [0, 1, 2, 3]

    def test_all_nodes_visited_exactly_once(self):
        m = linear_matrix(6)
        result = _nearest_neighbor_indices(m, depot=0)
        assert sorted(result) == list(range(6))

    def test_depot_is_first_node(self):
        m = linear_matrix(5)
        for start in range(5):
            result = _nearest_neighbor_indices(m, depot=start)
            assert result[0] == start

    def test_returns_correct_length(self):
        n = 8
        result = _nearest_neighbor_indices(linear_matrix(n), depot=0)
        assert len(result) == n


# ── nearest_neighbor_optimize ─────────────────────────────────────────────────


class TestNearestNeighborOptimize:
    def test_empty_stops_returns_empty(self):
        assert nearest_neighbor_optimize([], linear_matrix(0)) == []

    def test_single_stop_returns_unchanged(self):
        s = [stop("a")]
        result = nearest_neighbor_optimize(s, [[0]])
        assert result == s

    def test_linear_matrix_visits_in_order_from_0(self):
        stops = _stops(4)
        result = nearest_neighbor_optimize(stops, linear_matrix(4), start_index=0)
        assert result == stops  # 0,1,2,3 is the optimal NN order

    def test_result_is_permutation_of_input(self):
        stops = _stops(5)
        result = nearest_neighbor_optimize(stops, linear_matrix(5), start_index=0)
        assert len(result) == len(stops)
        assert set(id(s) for s in result) == set(id(s) for s in stops)

    def test_no_stops_duplicated_or_dropped(self):
        stops = _stops(6)
        result = nearest_neighbor_optimize(stops, linear_matrix(6), start_index=0)
        assert sorted(s["id"] for s in result) == sorted(s["id"] for s in stops)

    def test_different_start_index_changes_first_stop(self):
        stops = _stops(4)
        result_from_2 = nearest_neighbor_optimize(stops, linear_matrix(4), start_index=2)
        assert result_from_2[0] is stops[2]

    def test_start_index_stop_appears_first(self):
        stops = _stops(5)
        for start in range(5):
            result = nearest_neighbor_optimize(stops, linear_matrix(5), start_index=start)
            assert result[0] is stops[start]


# ── clarke_wright_savings ─────────────────────────────────────────────────────


class TestClarkeWrightSavings:
    def test_two_stops_returns_unchanged(self):
        stops = _stops(2)
        m = linear_matrix(2)
        result = clarke_wright_savings(stops, m)
        assert result == stops

    def test_single_stop_returns_unchanged(self):
        stops = _stops(1)
        result = clarke_wright_savings(stops, [[0]])
        assert result == stops

    def test_result_is_permutation_no_stops_added_or_dropped(self):
        stops = _stops(5)
        m = linear_matrix(5)
        result = clarke_wright_savings(stops, m)
        assert len(result) == len(stops)
        assert sorted(s["id"] for s in result) == sorted(s["id"] for s in stops)

    def test_symmetric_4stop_visits_each_stop_once(self):
        stops = _stops(4)
        m = linear_matrix(4)
        result = clarke_wright_savings(stops, m, depot_index=0)
        assert len(result) == 4
        ids = [s["id"] for s in result]
        assert len(ids) == len(set(ids)), "Duplicate stops in result"

    def test_first_stop_in_result_is_depot(self):
        # CW builds routes starting from the depot
        stops = _stops(4)
        m = linear_matrix(4)
        result = clarke_wright_savings(stops, m, depot_index=0)
        assert result[0] is stops[0]

    def test_returns_list_type(self):
        stops = _stops(3)
        m = linear_matrix(3)
        result = clarke_wright_savings(stops, m)
        assert isinstance(result, list)


# ── solve_nearest_neighbor ────────────────────────────────────────────────────


class TestSolveNearestNeighbor:
    def test_empty_stops_returns_empty(self):
        assert solve_nearest_neighbor([], []) == []

    def test_single_stop_returns_unchanged(self):
        s = [stop("a")]
        result = solve_nearest_neighbor([[0]], s, start_index=0)
        assert result == s

    def test_result_is_permutation_of_input(self):
        stops = _stops(5)
        m = linear_matrix(5)
        result = solve_nearest_neighbor(m, stops, start_index=0)
        assert len(result) == len(stops)
        assert sorted(s["id"] for s in result) == sorted(s["id"] for s in stops)

    def test_no_stops_lost_no_duplicates(self):
        stops = _stops(6)
        m = linear_matrix(6)
        result = solve_nearest_neighbor(m, stops, start_index=0)
        result_ids = [s["id"] for s in result]
        assert len(result_ids) == len(set(result_ids))
        assert set(result_ids) == {s["id"] for s in stops}

    def test_identical_coordinates_both_appear_in_output(self):
        # Two stops at the exact same lat/lng — cluster-aware wrapper must
        # not silently drop one of them.
        stops = [
            stop("a", lat=1.0, lng=1.0),
            stop("b", lat=2.0, lng=2.0),
            stop("c", lat=1.0, lng=1.0),  # same coords as "a"
        ]
        m = [
            [0.0, 1.0, 0.0],
            [1.0, 0.0, 1.0],
            [0.0, 1.0, 0.0],
        ]
        result = solve_nearest_neighbor(m, stops, start_index=0)
        result_ids = sorted(s["id"] for s in result)
        assert result_ids == ["a", "b", "c"]

    def test_identical_coord_stops_appear_consecutively(self):
        # Stops at same doorstep must not be split by other stops (the
        # zero-cost interleaving protection from coord_clustering).
        stops = [
            stop("depot", lat=0.0, lng=0.0),
            stop("a1", lat=5.0, lng=0.0),
            stop("a2", lat=5.0, lng=0.0),  # same doorstep as a1
            stop("b", lat=10.0, lng=0.0),
        ]
        m = [
            [0,  5,  5, 10],
            [5,  0,  0,  5],
            [5,  0,  0,  5],
            [10, 5,  5,  0],
        ]
        result = solve_nearest_neighbor(m, stops, start_index=0)
        assert sorted(s["id"] for s in result) == ["a1", "a2", "b", "depot"]
        ids = [s["id"] for s in result]
        idx_a1 = ids.index("a1")
        idx_a2 = ids.index("a2")
        assert abs(idx_a1 - idx_a2) == 1, (
            f"Same-doorstep stops a1 and a2 must be adjacent, got order: {ids}"
        )
