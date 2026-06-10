"""Open-path matrix transform shared by the TSP engine wrappers.

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

def _open_path_matrix(matrix: List[List[int]], depot: int) -> List[List[int]]:
    """Convert a closed-loop matrix to an open-path matrix by zeroing return-to-depot.

    Why this exists:
        Delivery routes don't return to depot — the driver finishes at whichever
        stop is last. Closed-loop TSP solvers (LKH, PyVRP via Hybrid Genetic
        Search with `end_depot`) optimise the full Hamiltonian cycle including
        the return leg back to the start. The "optimal" cycle is often
        catastrophically wrong for open-path delivery: the solver routes
        `depot → far_cluster → ... → near_cluster → back_to_depot` because that
        minimises the cycle, but the driver actually drives `depot → far_cluster
        → ... → near_cluster` and stops there — having driven past every
        near_cluster house at the start.

        The standard fix: tell the solver the return edge costs zero. Then the
        closed-loop optimum is identical to the open-path optimum because the
        return is "free" and never affects the objective.

    Args:
        matrix: N×N cost matrix (seconds or meters). Will be deep-copied.
        depot: Index of the start node. The column `[i][depot]` is zeroed for
            all i != depot, leaving the diagonal alone.

    Returns:
        A new N×N matrix with the same shape and same row/col semantics, but
        with `result[i][depot] = 0` for `i != depot`. The original matrix is
        left untouched (callers can still report distances from it).
    """
    n = len(matrix)
    if n == 0:
        return []
    # Use list comprehension over per-row slice to keep the original immutable
    out = [list(row) for row in matrix]
    if 0 <= depot < n:
        for i in range(n):
            if i != depot:
                out[i][depot] = 0
    return out
