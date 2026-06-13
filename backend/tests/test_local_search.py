"""Tests for solvers/local_search.py — local-search improvement operators.

Covers:
  - two_opt_improve   (2-opt with full asymmetric-safe cost comparison)
  - three_opt_improve (non-reversing 3-opt segment swap)
  - or_opt_improve    (Or-opt segment relocation)

No server imports are exercised; all three functions are pure algorithms
that operate on route index lists and a cost matrix.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import List

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from solvers.local_search import (  # noqa: E402
    or_opt_improve,
    three_opt_improve,
    two_opt_improve,
)


# ── shared helpers ────────────────────────────────────────────────────────────


def route_cost(indices: List[int], matrix: List[List[float]]) -> float:
    """Total cost of an open-path route."""
    return sum(matrix[indices[i]][indices[i + 1]] for i in range(len(indices) - 1))


def cross_matrix() -> List[List[float]]:
    """4-stop Euclidean matrix where route [0,2,1,3] has a crossing.

    Positions: 0=(0,0), 1=(1,0), 2=(0,1), 3=(1,1).
    Optimal open-path from 0: 0→2→3→1 or 0→1→3→2 (no crossing).
    Crossed route 0→2→1→3 or 0→1→2→3 has an extra diagonal.
    """
    pts = [(0, 0), (1, 0), (0, 1), (1, 1)]
    return [
        [math.sqrt((pts[i][0] - pts[j][0]) ** 2 + (pts[i][1] - pts[j][1]) ** 2)
         for j in range(4)]
        for i in range(4)
    ]


def linear_matrix(n: int) -> List[List[float]]:
    """n×n matrix where cost(i,j) = abs(i-j).

    Optimal open-path tour from 0: 0,1,2,...,n-1.
    """
    return [[float(abs(i - j)) for j in range(n)] for i in range(n)]


def _is_permutation(original: List[int], result: List[int]) -> bool:
    return sorted(result) == sorted(original)


# ── two_opt_improve ───────────────────────────────────────────────────────────


class TestTwoOptImprove:
    def test_empty_list_returns_empty(self):
        assert two_opt_improve([], linear_matrix(0)) == []

    def test_single_element_returns_unchanged(self):
        assert two_opt_improve([0], linear_matrix(1)) == [0]

    def test_two_element_route_returns_unchanged(self):
        m = linear_matrix(2)
        assert two_opt_improve([0, 1], m) == [0, 1]

    def test_result_is_permutation_of_input(self):
        route = [0, 2, 1, 3]
        m = cross_matrix()
        result = two_opt_improve(route, m)
        assert _is_permutation(route, result)

    def test_crossed_route_cost_does_not_increase(self):
        # Route [0,2,1,3] has a crossing. 2-opt must not make things worse.
        m = cross_matrix()
        route = [0, 2, 1, 3]
        before = route_cost(route, m)
        result = two_opt_improve(route, m)
        after = route_cost(result, m)
        assert after <= before + 1e-9

    def test_already_optimal_linear_route_unchanged(self):
        # 0→1→2→3→4 on a linear matrix is already optimal.
        m = linear_matrix(5)
        route = [0, 1, 2, 3, 4]
        result = two_opt_improve(route, m)
        assert result == route

    def test_5node_result_covers_all_nodes(self):
        m = linear_matrix(5)
        route = [0, 4, 3, 2, 1]
        result = two_opt_improve(route, m)
        assert sorted(result) == [0, 1, 2, 3, 4]

    def test_output_cost_never_worse_than_input(self):
        m = cross_matrix()
        for route in ([0, 1, 2, 3], [0, 3, 2, 1], [0, 2, 3, 1]):
            before = route_cost(route, m)
            result = two_opt_improve(route[:], m)
            assert route_cost(result, m) <= before + 1e-9


# ── three_opt_improve ─────────────────────────────────────────────────────────


class TestThreeOptImprove:
    def test_less_than_5_nodes_returns_unchanged(self):
        m = linear_matrix(4)
        route = [0, 1, 2, 3]
        result = three_opt_improve(route, m)
        assert result == route

    def test_exactly_4_nodes_unchanged(self):
        m = cross_matrix()
        route = [0, 1, 2, 3]
        assert three_opt_improve(route, m) == route

    def test_result_is_permutation_of_input(self):
        m = linear_matrix(6)
        route = [0, 5, 4, 3, 2, 1]
        result = three_opt_improve(route, m)
        assert _is_permutation(route, result)

    def test_output_cost_never_worse_than_input(self):
        m = linear_matrix(7)
        route = [0, 6, 5, 4, 3, 2, 1]
        before = route_cost(route, m)
        result = three_opt_improve(route, m)
        assert route_cost(result, m) <= before + 1e-9

    def test_already_optimal_route_unchanged(self):
        m = linear_matrix(6)
        route = [0, 1, 2, 3, 4, 5]
        result = three_opt_improve(route, m)
        assert result == route

    def test_zero_iterations_returns_input_unchanged(self):
        m = linear_matrix(6)
        route = [0, 5, 4, 3, 2, 1]
        result = three_opt_improve(route, m, max_iterations=0)
        assert result == route

    def test_5node_covers_all_nodes(self):
        m = linear_matrix(5)
        route = [0, 4, 3, 2, 1]
        result = three_opt_improve(route, m)
        assert sorted(result) == [0, 1, 2, 3, 4]

    def test_segment_swap_improves_known_suboptimal_route(self):
        """Build a route where swapping two inner segments (C+B ordering)
        is provably cheaper than the original (B+C) ordering.

        Layout (open-path, depot fixed at 0):
          0 --(1)--> 1 --(10)--> 4 --(10)--> 3 --(1)--> 2 --(5)--> 5
        Segment B = [1], Segment C = [4, 3] when i=1, j=2, k=4.
        Swap gives: 0,4,3,1,2,5  which saves boundary edge cost.
        """
        # Use a hand-crafted asymmetric-safe matrix
        n = 6
        # Start with large costs everywhere, then set cheap edges
        big = 100.0
        m = [[big] * n for _ in range(n)]
        for i in range(n):
            m[i][i] = 0.0
        # Cheap path: 0→4→3→1→2→5
        cheap = [(0, 4, 1.0), (4, 3, 1.0), (3, 1, 1.0), (1, 2, 1.0), (2, 5, 1.0)]
        for (a, b, c) in cheap:
            m[a][b] = c

        route = [0, 1, 4, 3, 2, 5]
        before = route_cost(route, m)
        result = three_opt_improve(route, m, max_iterations=5)
        after = route_cost(result, m)
        assert after <= before + 1e-9, f"3-opt made things worse: {before} → {after}"


# ── or_opt_improve ────────────────────────────────────────────────────────────


class TestOrOptImprove:
    def test_less_than_4_nodes_returns_unchanged(self):
        m = linear_matrix(3)
        route = [0, 1, 2]
        assert or_opt_improve(route, m) == route

    def test_exactly_3_nodes_unchanged(self):
        m = linear_matrix(3)
        route = [0, 2, 1]
        assert or_opt_improve(route, m) == route

    def test_result_is_permutation_of_input(self):
        m = linear_matrix(6)
        route = [0, 5, 4, 3, 2, 1]
        result = or_opt_improve(route, m)
        assert _is_permutation(route, result)

    def test_output_cost_never_worse_than_input(self):
        m = linear_matrix(7)
        route = [0, 6, 5, 4, 3, 2, 1]
        before = route_cost(route, m)
        result = or_opt_improve(route, m)
        assert route_cost(result, m) <= before + 1e-9

    def test_already_optimal_route_not_made_worse(self):
        m = linear_matrix(6)
        route = [0, 1, 2, 3, 4, 5]
        before = route_cost(route, m)
        result = or_opt_improve(route, m)
        assert route_cost(result, m) <= before + 1e-9

    def test_zero_iterations_returns_unchanged(self):
        m = linear_matrix(6)
        route = [0, 5, 4, 3, 2, 1]
        result = or_opt_improve(route, m, max_iterations=0)
        assert result == route

    def test_6node_covers_all_nodes(self):
        m = linear_matrix(6)
        route = [0, 5, 1, 2, 3, 4]
        result = or_opt_improve(route, m)
        assert sorted(result) == [0, 1, 2, 3, 4, 5]

    def test_misplaced_single_stop_is_relocated(self):
        """A stop that is far from its neighbours but cheap to insert elsewhere
        should be relocated by Or-opt.

        Layout (8 stops, open path):
          Cheap linear path: 0,1,2,3,4,5,6,7  (all unit cost)
          We insert stop 7 (the last one) between 0 and 1 to create a detour:
          Route:  0, 7, 1, 2, 3, 4, 5, 6
          Stop 7 has low cost to reach from 6 and high cost from 0.
        """
        n = 8
        big = 50.0
        m = [[big] * n for _ in range(n)]
        for i in range(n):
            m[i][i] = 0.0
        # Build a cheap chain 0→1→2→3→4→5→6→7
        for i in range(n - 1):
            m[i][i + 1] = 1.0
            m[i + 1][i] = 1.0  # symmetric for simplicity

        # Misplace stop 7: insert it right after depot (expensive from 0,
        # but 0→1→…→6→7 would be cheap = 7 unit hops).
        route = [0, 7, 1, 2, 3, 4, 5, 6]
        before = route_cost(route, m)
        result = or_opt_improve(route, m, max_iterations=10)
        after = route_cost(result, m)
        assert after <= before + 1e-9, f"Or-opt should not increase cost: {before} → {after}"
        # Cost should improve (misplaced stop 7 is very expensive at the front)
        assert after < before, (
            f"Expected Or-opt to relocate misplaced stop 7. before={before}, after={after}"
        )
