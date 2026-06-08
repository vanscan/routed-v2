"""
Late Freight Zipper
===================
Inserts mid-route parcels ("late freight") into an already-running delivery
route WITHOUT re-ordering the locked stops the driver has physically numbered
with a Sharpie (original_sequence).

The optimizer is free to slot new parcels anywhere along the run, but the
relative order of every locked stop is held fixed by a monotonic rank
dimension. New stops are then relabeled alphanumerically against the locked
stop they fall behind ( ... -> 45 -> 45A -> 45B -> 46 ... ), so the driver's
physical numbering never lies.

Engine:  Google OR-Tools  (PATH_CHEAPEST_ARC  +  GUIDED_LOCAL_SEARCH)
Matrix:  Injectable. Pass an OSRM /table matrix in production; a haversine
         fallback is provided for offline correctness testing.

This module is pure / side-effect free. Wrap it in the API layer separately.
"""

from __future__ import annotations

import math
import string
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

# Scale floats -> ints. OR-Tools arc costs are integral; metres * 1 is plenty.
_DISTANCE_SCALE = 1


@dataclass(frozen=True)
class Stop:
    """A single point on the run.

    original_sequence is the Sharpie number for a LOCKED stop, or None for a
    piece of late freight that has not been numbered yet. The depot carries
    sequence 0 by convention.
    """
    id: str
    lat: float
    lon: float
    original_sequence: Optional[int] = None
    is_depot: bool = False

    @property
    def is_locked(self) -> bool:
        return self.original_sequence is not None and not self.is_depot


@dataclass(frozen=True)
class PlannedStop:
    """A stop in the solved route, carrying its final driver-facing label."""
    id: str
    label: str            # "12", "45A", "DEPOT" ...
    lat: float
    lon: float
    original_sequence: Optional[int]
    is_late_freight: bool


@dataclass(frozen=True)
class ZipperResult:
    route: list[PlannedStop]
    total_distance_m: int
    inserted_labels: list[str] = field(default_factory=list)
    solver_status: str = "OK"


# --------------------------------------------------------------------------- #
# Distance matrix
# --------------------------------------------------------------------------- #
def haversine_matrix(stops: Sequence[Stop]) -> list[list[int]]:
    """Great-circle metres between every pair. Stand-in for the OSRM /table
    service during offline testing. Production should pass the OSRM matrix
    straight through to solve_zipper(matrix=...)."""
    R = 6_371_000.0
    n = len(stops)
    out = [[0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            la1, lo1, la2, lo2 = map(
                math.radians,
                (stops[i].lat, stops[i].lon, stops[j].lat, stops[j].lon),
            )
            dlat, dlon = la2 - la1, lo2 - lo1
            a = math.sin(dlat / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlon / 2) ** 2
            d = int(round(R * 2 * math.asin(math.sqrt(a)) * _DISTANCE_SCALE))
            out[i][j] = out[j][i] = d
    return out


# --------------------------------------------------------------------------- #
# Solver
# --------------------------------------------------------------------------- #
def solve_zipper(
    stops: Sequence[Stop],
    matrix: Optional[list[list[int]]] = None,
    *,
    return_to_depot: bool = True,
    time_limit_s: int = 5,
) -> ZipperResult:
    """Zipper late freight into the locked run.

    stops[0] MUST be the depot. Locked stops must already be supplied in the
    intended Sharpie order; their relative order is what we preserve. Late
    freight is any stop with original_sequence is None.
    """
    if not stops or not stops[0].is_depot:
        raise ValueError("stops[0] must be the depot")

    locked = [i for i, s in enumerate(stops) if s.is_locked]
    # Validate the Sharpie numbers genuinely ascend in supplied order, else the
    # 'locked order' is ambiguous and we refuse to guess.
    seqs = [stops[i].original_sequence for i in locked]
    if seqs != sorted(seqs):
        raise ValueError(
            f"Locked stops must be supplied in ascending Sharpie order; got {seqs}"
        )

    n = len(stops)
    dist = matrix if matrix is not None else haversine_matrix(stops)

    manager = pywrapcp.RoutingIndexManager(n, 1, 0)  # n nodes, 1 van, depot idx 0
    routing = pywrapcp.RoutingModel(manager)

    # Open route: make the leg back to the depot free so the van can end at the
    # last delivery rather than being dragged home.
    def arc_cost(from_idx: int, to_idx: int) -> int:
        i, j = manager.IndexToNode(from_idx), manager.IndexToNode(to_idx)
        if j == 0 and not return_to_depot:
            return 0
        return dist[i][j]

    transit = routing.RegisterTransitCallback(arc_cost)
    routing.SetArcCostEvaluatorOfAllVehicles(transit)

    # --- The zipper constraint -------------------------------------------- #
    # A unary 'rank' dimension counts how many nodes deep each visit is. By
    # forcing rank(locked[k]) < rank(locked[k+1]) we pin the Sharpie order
    # while leaving every gap open for late freight to drop into.
    routing.AddConstantDimension(
        1,            # +1 rank per hop
        n + 1,        # capacity (upper bound on path length)
        True,         # start cumul at zero
        "rank",
    )
    rank = routing.GetDimensionOrDie("rank")
    solver = routing.solver()
    for a, b in zip(locked, locked[1:]):
        solver.Add(
            rank.CumulVar(manager.NodeToIndex(a))
            < rank.CumulVar(manager.NodeToIndex(b))
        )

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.FromSeconds(time_limit_s)

    solution = routing.SolveWithParameters(params)
    if solution is None:
        return ZipperResult(route=[], total_distance_m=0, solver_status="NO_SOLUTION")

    route = _relabel(stops, manager, routing, solution, return_to_depot)
    inserted = [p.label for p in route if p.is_late_freight]
    return ZipperResult(
        route=route,
        total_distance_m=solution.ObjectiveValue(),
        inserted_labels=inserted,
        solver_status="OK",
    )


def _relabel(stops, manager, routing, solution, return_to_depot) -> list[PlannedStop]:
    """Walk the solved path and assign driver-facing labels. Late freight
    inherits the previous locked stop's number plus A, B, C ..."""
    out: list[PlannedStop] = []
    last_locked_seq: Optional[int] = 0   # before any delivery we're at the depot
    suffix_idx = 0
    idx = routing.Start(0)
    while not routing.IsEnd(idx):
        node = manager.IndexToNode(idx)
        s = stops[node]
        if s.is_depot:
            out.append(PlannedStop(s.id, "DEPOT", s.lat, s.lon, 0, False))
        elif s.is_locked:
            last_locked_seq = s.original_sequence
            suffix_idx = 0
            out.append(
                PlannedStop(s.id, str(s.original_sequence), s.lat, s.lon, s.original_sequence, False)
            )
        else:  # late freight
            label = f"{last_locked_seq}{string.ascii_uppercase[suffix_idx]}"
            suffix_idx += 1
            out.append(PlannedStop(s.id, label, s.lat, s.lon, None, True))
        idx = solution.Value(routing.NextVar(idx))

    if return_to_depot:
        d = stops[0]
        out.append(PlannedStop(d.id, "DEPOT", d.lat, d.lon, 0, False))
    return out
