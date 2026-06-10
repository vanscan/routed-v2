"""PyVRP HGS open-path TSP adapter (thin shim over PyVRPTspSolver).

Lives in its own always-importable module (solvers/pyvrp_tsp_solver.py
imports `pyvrp` at the top, so it can't host this wrapper — the wrapper
must stay defined even when the pyvrp library is absent).

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
from typing import List, Optional, Tuple

from solvers.open_path import _open_path_matrix

logger = logging.getLogger("server")

def pyvrp_tsp_solve(
    duration_matrix: List[List[int]],
    depot: int = 0,
    time_limit_seconds: float = 2.0,
    seed: int = 0,
    coordinates: Optional[List[Tuple[float, float]]] = None,
) -> List[int]:
    """Solve open-path TSP using PyVRP's Hybrid Genetic Search.

    Thin adapter over `PyVRPTspSolver` that matches the shape of the other
    native-solver wrappers (`vroom_tsp_solve`, `lkh_tsp_solve`, …): take a
    duration matrix whose row/col 0 is the depot, return an index list that
    starts at `depot` and visits every other node exactly once.

    Args:
        duration_matrix: N×N integer seconds matrix.
        depot: Index of the starting node inside `duration_matrix`.
        time_limit_seconds: HGS search budget (1-2s is plenty for pure TSP).
        seed: Deterministic seed for reproducible test runs.
        coordinates: Optional list of `(longitude, latitude)` per matrix row
            (length must equal `len(duration_matrix)`). When supplied,
            stops sharing identical `(lon, lat)` are collapsed into a single
            PyVRP super-node and re-expanded in input order — this stops the
            HGS solver from randomly shuffling stops at the same address
            (apartments/units in one building) which would otherwise produce
            visible zig-zags on the map.

    Returns:
        Ordered list of node indices beginning with `depot`.
    """
    import server as _srv  # noqa: WPS433
    if not _srv.PYVRP_AVAILABLE:
        raise RuntimeError(f"pyvrp not available: {_srv.PYVRP_IMPORT_ERROR}")

    n = len(duration_matrix)
    if n <= 1:
        return list(range(n))

    if coordinates is not None and len(coordinates) != n:
        raise ValueError(
            f"coordinates length {len(coordinates)} does not match matrix "
            f"size {n}"
        )

    # ── Open-path TSP via free return edge ────────────────────────────────
    # PyVRP's HGS is a closed-loop solver (vehicle.end_depot = depot is a
    # required field). For delivery routes the driver does NOT return to
    # depot, so we patch the return-to-depot column to 0 BEFORE handing the
    # matrix to PyVRP. The closed-loop optimum on the patched matrix equals
    # the open-path optimum on the original. Without this, PyVRP routinely
    # picked routes like `[0, 37, 38, ..., 1, 2, 3]` — efficient if you'd
    # return to the start, but pessimal for one-way delivery (driver passed
    # stop 1 at the start and had to come back at the end).
    duration_matrix = _open_path_matrix(duration_matrix, depot)

    # PyVRP expects numpy + integer seconds. Build the matrix so row/col 0
    # correspond to the depot regardless of what `depot` the caller passed.
    import numpy as _np  # local import — matches the pattern used elsewhere
    matrix = _np.asarray(duration_matrix, dtype=_np.int64)
    if depot != 0:
        order = [depot] + [i for i in range(n) if i != depot]
        matrix = matrix[_np.ix_(order, order)]
    else:
        order = list(range(n))

    # Build per-stop DeliveryStop including coords (if any) so PyVRPTspSolver
    # can collapse identical-coordinate clusters into super-nodes.
    if coordinates is not None:
        stops = [
            _srv.DeliveryStop(
                stop_id=original_idx,
                service_duration=0,
                x=float(coordinates[original_idx][0]),
                y=float(coordinates[original_idx][1]),
            )
            for original_idx in order[1:]
        ]
        depot_lon, depot_lat = coordinates[depot]
        depot_stop = _srv.DeliveryStop(
            stop_id=depot,
            service_duration=0,
            x=float(depot_lon),
            y=float(depot_lat),
        )
    else:
        stops = [
            _srv.DeliveryStop(stop_id=original_idx, service_duration=0)
            for original_idx in order[1:]
        ]
        depot_stop = _srv.DeliveryStop(stop_id=depot, service_duration=0)

    solver = _srv.PyVRPTspSolver(
        max_runtime_seconds=time_limit_seconds,
        seed=seed,
        display=False,
    )
    sequence = solver.solve(
        depot=depot_stop,
        stops=stops,
        time_matrix=matrix,
    )

    # `sequence` is already a list of ORIGINAL node indices (we stuffed the
    # original index into `stop_id`), so just prepend the depot to match the
    # convention used by `vroom_tsp_solve` and `lkh_tsp_solve`.
    visited = [depot] + [int(sid) for sid in sequence]

    # Defensive: if PyVRP ever drops a node, append it so callers never lose
    # a stop. Mirrors the guard at the bottom of `vroom_tsp_solve`.
    seen = set(visited)
    for i in range(n):
        if i not in seen:
            visited.append(i)
    return visited
