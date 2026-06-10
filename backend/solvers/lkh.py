"""LKH-3 (Lin-Kernighan-Helsgaun) ATSP wrapper.

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

import errno

from solvers.open_path import _open_path_matrix

logger = logging.getLogger("server")

def lkh_tsp_solve(
    duration_matrix: List[List[int]],
    depot: int = 0,
    runs: int = 5,
    time_limit_seconds: int = 10,
) -> List[int]:
    """Solve ATSP using LKH-3 (Lin-Kernighan-Helsgaun), the gold-standard TSP heuristic.

    Args:
        duration_matrix: NxN integer cost matrix (seconds).
        depot: Starting node index (fixed as tour start).
        runs: Number of LKH trial runs (more = better quality, slower).
        time_limit_seconds: Max wall-clock time for the solver.

    Returns:
        Ordered list of 0-indexed stop indices starting from depot.
    """
    import server as _srv  # noqa: WPS433
    if not _srv.LKH_AVAILABLE:
        raise RuntimeError("LKH-3 binary not available")

    n = len(duration_matrix)
    if n <= 2:
        return list(range(n))

    # ── Matrix sanitisation ──────────────────────────────────────────────
    # OSRM occasionally returns `null`/negative cells for un-snappable coords;
    # passed verbatim to LKH those become "free" or "negative-cost" edges and
    # the solver gladly exploits them, producing visibly absurd tours. Force
    # `null/NaN/<0 → PENALTY_SECONDS` and the diagonal to 0 BEFORE the
    # open-path patch so the depot column zero-out is preserved.
    from solvers.pyvrp_tsp_solver import sanitize_osrm_matrix
    clean = sanitize_osrm_matrix(duration_matrix).tolist()

    # ── Open-path TSP via free return edge ────────────────────────────────
    # LKH solves a closed Hamiltonian cycle. For delivery routes we DO NOT
    # return to the depot — the driver finishes wherever the last stop is.
    # Zeroing the return-to-depot column makes the closed-loop optimum equal
    # to the open-path optimum because the return leg becomes free and drops
    # out of the objective. Without this, LKH produced routes that started
    # `depot → far_cluster → ...` because returning past near_cluster was
    # cheap in the cycle, even though the driver never actually returns.
    open_path_matrix = _open_path_matrix(clean, depot)

    # LKH uses ATSP format with FULL_MATRIX edge weights.
    problem = _srv.lkh.LKHProblem(
        type='ATSP',
        dimension=n,
        edge_weight_type='EXPLICIT',
        edge_weight_format='FULL_MATRIX',
        edge_weights=open_path_matrix,
    )

    # Scale runs and time with problem size
    actual_runs = max(runs, min(10, n // 20))
    actual_time = max(time_limit_seconds, min(30, n // 10))

    try:
        result = _srv.lkh.solve(
            solver=_srv.LKH_SOLVER_PATH,
            problem=problem,
            runs=actual_runs,
            time_limit=actual_time,
        )
    except OSError as exec_err:
        # ── Architecture mismatch self-disable ────────────────────────────
        # `[Errno 8] Exec format error` fires when the cached LKH binary at
        # LKH_SOLVER_PATH was compiled for a CPU arch that doesn't match the
        # current container (e.g. x86_64 binary on aarch64). Without this
        # guard every Optimize call re-tries LKH, re-throws OSError, and
        # spams the production log via the caller's `logger.warning`.
        # Flip `LKH_AVAILABLE=False` so the top-of-function guard short-
        # circuits future calls (and the caller-level `if LKH_AVAILABLE:`
        # blocks skip LKH cleanly). VROOM/3-opt fallback already exists.
        if exec_err.errno in (errno.ENOEXEC, 8):
            if _srv.LKH_AVAILABLE:
                _srv.LKH_AVAILABLE = False
                _srv.LKH_IMPORT_ERROR = (
                    f"LKH binary incompatible with current arch ({exec_err})"
                )
                logger.info(
                    "[lkh] Disabling LKH for this process — binary at %s is "
                    "incompatible with current CPU arch (Errno 8). Falling "
                    "back to VROOM+3-opt.",
                    _srv.LKH_SOLVER_PATH,
                )
        raise RuntimeError(f"LKH-3 binary not runnable: {exec_err}") from exec_err

    if not result or not result[0]:
        raise RuntimeError("LKH returned empty solution")

    # LKH returns 1-indexed tour. Convert to 0-indexed.
    tour_1indexed = result[0]
    tour = [x - 1 for x in tour_1indexed]

    # Rotate tour so depot is first
    if depot in tour:
        depot_pos = tour.index(depot)
        tour = tour[depot_pos:] + tour[:depot_pos]

    # Defensive: add any missing nodes
    visited = set(tour)
    for i in range(n):
        if i not in visited:
            tour.append(i)

    return tour
