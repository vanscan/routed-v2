"""elkai ATSP wrapper (bundled LKH C backend, no external binary).

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

from solvers.open_path import _open_path_matrix

logger = logging.getLogger("server")

def elkai_tsp_solve(
    duration_matrix: List[List[int]],
    depot: int = 0,
) -> List[int]:
    """Solve ATSP using elkai (bundled LKH C backend - no external binary needed).
    
    elkai is recommended for production due to its native C backend and
    simple installation (pip install elkai).
    
    Args:
        duration_matrix: NxN integer cost matrix (seconds).
        depot: Starting node index (fixed as tour start).
    
    Returns:
        Ordered list of 0-indexed stop indices starting from depot.
    """
    import server as _srv  # noqa: WPS433
    if not _srv.ELKAI_AVAILABLE:
        raise RuntimeError(f"elkai not available: {_srv.ELKAI_IMPORT_ERROR}")
    
    n = len(duration_matrix)
    if n <= 2:
        return list(range(n))
    
    # Sanitise matrix (handle null/NaN/negative values)
    from solvers.pyvrp_tsp_solver import sanitize_osrm_matrix
    clean = sanitize_osrm_matrix(duration_matrix).tolist()
    
    # Open-path TSP via free return edge
    open_path_matrix = _open_path_matrix(clean, depot)
    
    # elkai expects a flat list for the distance matrix
    # It solves symmetric TSP, so we need to handle ATSP by converting
    # or use the asymmetric version if available
    try:
        # elkai.solve_float_matrix expects List[List[float]]
        tour = _srv.elkai.solve_float_matrix(open_path_matrix)
    except AttributeError:
        # Older elkai versions use different API
        # Flatten matrix for elkai.solve_int_matrix
        flat_matrix = [int(cell) for row in open_path_matrix for cell in row]
        tour = _srv.elkai.solve_int_matrix(flat_matrix, n)
    
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
