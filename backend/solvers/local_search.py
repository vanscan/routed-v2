"""Local-search improvement operators: 2-opt, 3-opt and Or-opt.

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

def two_opt_improve(route_indices: List[int], distance_matrix: List[List[float]]) -> List[int]:
    """2-Opt improvement — asymmetric-matrix-safe.

    Standard 2-opt reverses the segment between two cut edges. On a symmetric
    matrix every internal edge has the same cost in both directions so the
    boundary-only delta formula (d1+d2 vs d3+d4) is correct. On an asymmetric
    matrix (OSRM one-way streets, turn restrictions) reversing a segment flips
    every internal edge's direction, changing its cost — the boundary-only
    formula accepts moves that only *look* cheaper and can produce longer routes.

    Fix: measure the full cost of the affected path slice before and after
    reversal (O(segment) per evaluation). Only accept if the total genuinely
    decreases. Identical to the fix already applied to three_opt_improve.
    """
    improved = True
    best = route_indices[:]
    n = len(best)

    while improved:
        improved = False
        for i in range(1, n - 1):
            for j in range(i + 1, n):
                # ── Cost of current path slice: best[i-1] → … → best[j] ──
                cur = distance_matrix[best[i - 1]][best[i]]
                for k in range(i, j - 1):
                    cur += distance_matrix[best[k]][best[k + 1]]
                if j < n:
                    cur += distance_matrix[best[j - 1]][best[j]]

                # ── Cost after reversing best[i:j] ──
                # New path: best[i-1] → best[j-1] → best[j-2] → … → best[i] → best[j]
                rev = distance_matrix[best[i - 1]][best[j - 1]]
                for k in range(j - 1, i, -1):
                    rev += distance_matrix[best[k]][best[k - 1]]
                if j < n:
                    rev += distance_matrix[best[i]][best[j]]

                if cur > rev:
                    best[i:j] = reversed(best[i:j])
                    improved = True

    return best

def three_opt_improve(route_indices: List[int], cost_matrix: List[List[float]], max_iterations: int = 5) -> List[int]:
    """3-Opt improvement — non-reversal segment swap (asymmetric-safe).

    On an open-path tour we want to escape 2-opt local optima without breaking
    on directed-graph cost matrices. The classic textbook 3-opt enumerates 7
    reconnections, six of which REVERSE one or both inner segments
    (`A + B[::-1] + C + D`, `A + C[::-1] + B + D`, etc.). When the cost
    matrix is asymmetric (OSRM's one-way streets, turn restrictions) reversing
    a segment changes every internal edge cost — but the textbook delta-cost
    formula only re-prices the 3 boundary edges and assumes internal costs
    are unchanged. The result: 3-opt accepts moves that LOOK cheaper than
    they actually are, occasionally producing worse tours than its input
    (the symptom: zig-zags and "doubling back" past a stop the route already
    passed). We saw this in production with stops 11→12→13→14 doubling back.

    Fix: keep only the ONE 3-opt candidate that doesn't reverse any segment:
    `A + C + B + D` (swap segments B and C, preserving their internal
    direction). Its boundary-delta cost is correct on any matrix, symmetric
    or not. We lose some search power (no reversal escapes) but every move
    we DO accept is guaranteed to be a real improvement.

    The first node (depot) is held fixed.
    """
    best = route_indices[:]
    n = len(best)
    if n < 5:
        return best

    for _ in range(max_iterations):
        improved = False
        for i in range(1, n - 3):
            for j in range(i + 1, n - 2):
                for k in range(j + 1, n - 1):
                    # Segments: A = best[:i], B = best[i:j], C = best[j:k], D = best[k:]
                    A_last = best[i - 1]
                    B_first, B_last = best[i], best[j - 1]
                    C_first, C_last = best[j], best[k - 1]
                    D_first = best[k]

                    # Old boundary edges removed by the move.
                    d0 = (cost_matrix[A_last][B_first]
                          + cost_matrix[B_last][C_first]
                          + cost_matrix[C_last][D_first])

                    # Non-reversing swap: tour becomes A + C + B + D, with
                    # internal edges of B and C unchanged. Delta is exact
                    # on any (a)symmetric matrix because no edge inside B
                    # or C is altered — only the 3 join edges change.
                    d_new = (cost_matrix[A_last][C_first]
                             + cost_matrix[C_last][B_first]
                             + cost_matrix[B_last][D_first])

                    if d_new < d0:
                        best = best[:i] + best[j:k] + best[i:j] + best[k:]
                        improved = True

        if not improved:
            break

    return best

def or_opt_improve(route_indices: List[int], cost_matrix: List[List[float]], max_iterations: int = 10) -> List[int]:
    """Or-opt improvement — relocate sequences of 1, 2, or 3 consecutive stops.

    For each segment size (3, 2, 1), tries removing the segment from its
    current position and re-inserting it at every other position in the route.
    Accepts the move if total cost decreases. Repeats until no improvement
    found or max_iterations reached.

    Catches "misplaced cluster" improvements that 3-opt and LKH may miss.
    Runs in O(n^2) per pass per segment size. Keeps first node (depot) fixed.
    """
    best = route_indices[:]
    n = len(best)
    if n < 4:
        return best

    def _total_cost(route):
        return sum(cost_matrix[route[k]][route[k + 1]] for k in range(len(route) - 1))

    for _ in range(max_iterations):
        improved = False
        # Try segment sizes 3, 2, 1 (larger segments first for bigger wins)
        for seg_len in (3, 2, 1):
            if n < seg_len + 2:
                continue
            for i in range(1, n - seg_len):  # skip depot at index 0
                # Extract the segment
                segment = best[i:i + seg_len]
                # Build route without the segment
                rest = best[:i] + best[i + seg_len:]

                # Cost of current route around the removal point
                # Edges removed: (i-1 -> i), (i+seg_len-1 -> i+seg_len)
                # Edge added:    (i-1 -> i+seg_len)
                old_removal_cost = (
                    cost_matrix[best[i - 1]][best[i]] +
                    cost_matrix[best[i + seg_len - 1]][best[i + seg_len]] if (i + seg_len) < n else
                    cost_matrix[best[i - 1]][best[i]]
                )
                new_removal_cost = (
                    cost_matrix[best[i - 1]][best[i + seg_len]] if (i + seg_len) < n else 0
                )
                removal_delta = new_removal_cost - old_removal_cost

                # Try inserting the segment at every valid position in `rest`
                best_delta = 0
                best_insert_pos = -1
                for j in range(1, len(rest)):  # skip inserting before depot
                    # Edge being broken: rest[j-1] -> rest[j]
                    # Edges being added: rest[j-1] -> segment[0], segment[-1] -> rest[j]
                    old_insert_cost = cost_matrix[rest[j - 1]][rest[j]]
                    new_insert_cost = (
                        cost_matrix[rest[j - 1]][segment[0]] +
                        cost_matrix[segment[-1]][rest[j]]
                    )
                    # Internal segment cost stays the same, so only edge changes matter
                    insert_delta = new_insert_cost - old_insert_cost
                    total_delta = removal_delta + insert_delta

                    if total_delta < best_delta - 1e-9:
                        best_delta = total_delta
                        best_insert_pos = j

                if best_insert_pos >= 0:
                    # Apply the best move
                    best = rest[:best_insert_pos] + segment + rest[best_insert_pos:]
                    improved = True
                    break  # restart from scratch after each improvement
            if improved:
                break  # restart outer loop

        if not improved:
            break

    return best
