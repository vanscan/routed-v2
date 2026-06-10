"""VROOM (pyvroom) open-path TSP wrapper.

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

def vroom_tsp_solve(
    duration_matrix: List[List[int]],
    depot: int = 0,
    exploration_level: int = 5,
) -> List[int]:
    """Solve open-path TSP using VROOM (pyvroom).

    Args:
        duration_matrix: NxN integer seconds matrix.
        depot: Starting node index.
        exploration_level: VROOM search depth (1-5, higher = better but slower).

    Returns:
        Ordered list of stop indices (excluding depot if it appears at start).
    """
    import server as _srv  # noqa: WPS433
    if not _srv.VROOM_AVAILABLE:
        raise RuntimeError(f"pyvroom not available: {_srv.VROOM_IMPORT_ERROR}")

    n = len(duration_matrix)
    if n <= 1:
        return list(range(n))

    problem = _srv.vroom.Input()

    # Set the pre-computed duration matrix (accepts list-of-lists directly)
    problem.set_durations_matrix(profile="car", matrix_input=duration_matrix)

    # Single vehicle starting at depot (open-path: no explicit end)
    problem.add_vehicle(_srv.vroom.Vehicle(id=0, start=depot, profile="car"))

    # All non-depot stops as jobs
    jobs = []
    for i in range(n):
        if i != depot:
            jobs.append(_srv.vroom.Job(id=i, location=i))
    problem.add_job(jobs)

    # Solve
    solution = problem.solve(exploration_level=exploration_level, nb_threads=4)

    # Extract route order from solution.
    # pyvroom returns solution.routes as a pandas DataFrame with columns:
    # vehicle_id, type, arrival, duration, setup, service, waiting_time, location_index, id, description
    route_indices = [depot]
    routes_df = solution.routes
    if routes_df is not None and len(routes_df) > 0:
        job_rows = routes_df[routes_df["type"] == "job"]
        for _, row in job_rows.iterrows():
            job_id = int(row["id"])
            if job_id != depot:
                route_indices.append(job_id)

    # Add any stops missed by VROOM (shouldn't happen, but defensive)
    visited = set(route_indices)
    for i in range(n):
        if i not in visited:
            route_indices.append(i)

    return route_indices
