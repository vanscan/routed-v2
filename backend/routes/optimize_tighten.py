"""Cluster-tighten helpers and endpoints split from optimize.py.

    POST /optimize/tighten-cluster    → one-tap single-stop zig-zag fix
    POST /optimize/tighten-clusters   → iterative whole-route tightener
    GET  /optimize/algorithms         → static algorithm catalogue
    GET  /generoute/status            → Generoute API config probe

Helpers (`_two_opt_pass`, `_haversine_path_km`, `_relocate_stop_haversine`,
`_iterative_haversine_tighten`, `_persist_pending_order`,
`_osrm_verify_relocation`) are re-exported from optimize.py so existing
`from server import X` call sites keep working.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from haversine import Unit, haversine

from models import TightenClusterRequest, User
from routes.billing import require_pro as _billing_require_pro

logger = logging.getLogger("server")
router = APIRouter()


async def _current_user(request: Request):
    from server import get_current_user  # noqa: WPS433
    return await get_current_user(request)


def _two_opt_pass(seq: List[dict]) -> Tuple[List[dict], int]:
    """Single 2-opt sweep: try every pair of non-adjacent edges
    `(seq[i], seq[i+1])` and `(seq[j], seq[j+1])`, and reverse the
    interior segment `seq[i+1..j]` whenever the swap shortens the
    haversine path. Greedy first-improvement with restart — i.e. as
    soon as one improving swap is found we restart the outer scan, so
    interleaved spikes that single-stop relocation cannot reach get
    untangled in a single call.

    Returns `(new_seq, swaps_applied)`. The original list is not mutated.

    Why 2-opt fixes things `_relocate_stop_haversine` cannot:
      Single-stop relocate moves *one* node to its best insertion point.
      But a route like `...A B C D E F G...` where the visually-spiky
      stop is B, and the only improvement is the *swap* of edge `A→B`
      with edge `F→G` (giving `...A F E D C B G...` reversed), is
      invisible to single-stop search — moving B alone yields no gain
      because B's best insertion is exactly its current spot. 2-opt
      sees the edge pair and reverses the run.

    Cost: O(n²) per scan, restart on improvement. For n=167 a single
    sweep is ~28k pair checks × ~50 ns each = ~1.5 ms. Even with 50
    restarts that's <100 ms — fits under the auto-tighten budget.
    """
    n = len(seq)
    if n < 4:
        return list(seq), 0
    current = list(seq)
    swaps = 0
    improved = True
    # Cap scans defensively — pathological data could otherwise loop
    # near-forever. Real routes converge in 1-3 scans.
    max_scans = 50
    # Cluster-locality guard: reject any swap whose longest NEW edge is
    # more than 1.5× the longest OLD edge it replaces. This blocks the
    # 2026-05-11 regression where 2-opt would collapse two medium edges
    # (e.g. 20 km + 20 km, the natural bridges between two clusters)
    # into one tiny + one giant edge (e.g. 0.1 km + 39 km). The total
    # path shrinks, so the haversine improvement check passes, but the
    # giant new edge crosses a cluster boundary and drops a stop into
    # the wrong cluster. By capping per-edge growth, we keep the
    # solver inside the cluster graph the solver itself produced.
    # 1.5× allows legitimate "lengthen one edge slightly to remove a
    # zig-zag" within a cluster while rejecting cross-cluster jumps.
    LOCALITY_MULTIPLIER = 1.5
    while improved and swaps < max_scans * n:
        improved = False
        cur_len = _haversine_path_km(current)
        # Outer i goes up to n-3 so j has room. Inner j starts at i+2 to
        # avoid adjacent edges (whose swap is a no-op).
        for i in range(n - 3):
            ai = current[i]
            bi = current[i + 1]
            d_ab = haversine(
                (ai["latitude"], ai["longitude"]),
                (bi["latitude"], bi["longitude"]),
                unit=Unit.KILOMETERS,
            )
            for j in range(i + 2, n - 1):
                cj = current[j]
                dj = current[j + 1]
                # Existing edges: ai→bi and cj→dj
                # Swap to: ai→cj and bi→dj  (reverses the bi..cj segment)
                d_cd = haversine(
                    (cj["latitude"], cj["longitude"]),
                    (dj["latitude"], dj["longitude"]),
                    unit=Unit.KILOMETERS,
                )
                d_ac = haversine(
                    (ai["latitude"], ai["longitude"]),
                    (cj["latitude"], cj["longitude"]),
                    unit=Unit.KILOMETERS,
                )
                d_bd = haversine(
                    (bi["latitude"], bi["longitude"]),
                    (dj["latitude"], dj["longitude"]),
                    unit=Unit.KILOMETERS,
                )
                # Strict 1e-9 epsilon to dodge floating-point oscillation
                # that would otherwise let two equivalent tours flip back
                # and forth forever.
                if d_ac + d_bd + 1e-9 < d_ab + d_cd:
                    # Cluster-locality guard (see top of function). Reject
                    # any swap whose longest new edge exceeds 1.5× the
                    # longest old edge it replaces. Prevents 2-opt from
                    # creating cross-cluster bridges that haversine-sum
                    # tolerates but drivers visibly hate.
                    max_old = d_ab if d_ab > d_cd else d_cd
                    max_new = d_ac if d_ac > d_bd else d_bd
                    if max_new > max_old * LOCALITY_MULTIPLIER:
                        continue
                    current = current[: i + 1] + current[i + 1 : j + 1][::-1] + current[j + 1 :]
                    swaps += 1
                    improved = True
                    break  # restart outer scan from i=0
            if improved:
                break
        # Defensive: if the path didn't actually shrink despite improved=True,
        # bail. (Shouldn't trigger; the strict epsilon makes it monotonic.)
        if improved and _haversine_path_km(current) >= cur_len - 1e-9:
            break
    return current, swaps


def _filter_actionable_warnings(
    cleaned: List[dict], warnings: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Drop warnings whose `suspect_id` cannot be improved by *any* further
    single-stop relocation on the already-tightened sequence.

    Without this filter the banner lies: it shows "15 detour stops" even
    when Tighten All would do nothing because every flagged stop is at
    its haversine-optimal position. Filtering down to only the warnings
    the relocator can actually address keeps the UX honest — if 0
    warnings remain, the banner hides entirely.

    We only check single-stop relocation here (not 2-opt) because the
    callers already run `_two_opt_pass` upstream as part of the cleaning
    pipeline. Anything 2-opt can fix has been fixed; what's left is
    only what relocate can address. If relocate can't either, it's a
    permanent geometric quirk (e.g. a peninsula stop with truly no
    better neighbour) — silently informational, not actionable.
    """
    if not warnings:
        return []
    id_to_idx = {s["id"]: i for i, s in enumerate(cleaned) if "id" in s}
    out: List[Dict[str, Any]] = []
    for w in warnings:
        idx = id_to_idx.get(w.get("suspect_id"))
        if idx is None:
            continue
        _, _, before, after = _relocate_stop_haversine(cleaned, idx)
        if after < before - 1e-6:
            out.append(w)
    return out


def _haversine_path_km(seq: List[dict]) -> float:
    """Sum of haversine distances along an ordered list of stop dicts."""
    total = 0.0
    for i in range(len(seq) - 1):
        a, b = seq[i], seq[i + 1]
        total += haversine(
            (a["latitude"], a["longitude"]),
            (b["latitude"], b["longitude"]),
            unit=Unit.KILOMETERS,
        )
    return total


def _relocate_stop_haversine(
    pending: List[dict], suspect_idx: int
) -> Tuple[List[dict], int, float, float]:
    """Lift `pending[suspect_idx]` and reinsert it where the haversine path
    is shortest. Returns `(new_seq, new_position, before_km, after_km)`.

    The original list is not mutated.
    """
    suspect = pending[suspect_idx]
    rest = [s for i, s in enumerate(pending) if i != suspect_idx]

    before_km = _haversine_path_km(pending)
    best_seq = pending
    best_cost = before_km
    best_position = suspect_idx

    for pos in range(len(rest) + 1):
        candidate = rest[:pos] + [suspect] + rest[pos:]
        cost = _haversine_path_km(candidate)
        if cost < best_cost:
            best_cost = cost
            best_seq = candidate
            best_position = pos

    return best_seq, best_position, before_km, best_cost


def _iterative_haversine_tighten(
    seq: List[dict], max_passes: int = 50
) -> Tuple[List[dict], List[Dict[str, Any]]]:
    """Repeatedly relocate the worst cluster spike (largest `extra_km`) on
    `seq`, then run a 2-opt edge-swap sweep, alternating until both
    converge (no further haversine improvement is possible).

    Returns `(new_seq, moves)`. `moves` is empty when the input was
    already clean. The original list is not mutated. Use this for the
    pure-geometric pass; pair with `_osrm_verify_relocation` to make sure
    the cleaned route also wins on driving time.

    Why both relocate AND 2-opt: relocate moves a single stop to its
    haversine-best insertion point; 2-opt reverses an edge pair. Each
    can fix things the other cannot — interleaved spikes (stop 21
    visited mid-cluster of 119-124) are 2-opt-improvable but often
    relocate-stuck, while isolated detours (one stop far from the line
    A→C) are relocate-improvable but often 2-opt-stuck. Alternating
    catches both. We attribute every improvement to a recorded `move`
    so the audit log stays informative even when 2-opt does the heavy
    lifting.

    `max_passes=50` is a *ceiling*, not a target — natural exit is the
    no-improvement break inside each inner loop. 50 is well above any
    realistic spike count for a 200-stop manifest and still bounds
    runtime pathologically.
    """
    from server import detect_cluster_spikes  # noqa: WPS433
    moves: List[Dict[str, Any]] = []
    current = list(seq)

    def _relocate_loop():
        """One full single-stop-relocate sweep. Mutates `current` and
        appends to `moves`. Returns count of moves applied this sweep."""
        nonlocal current
        applied = 0
        for _ in range(max_passes):
            warnings = detect_cluster_spikes(current)
            if not warnings:
                break
            worst = max(warnings, key=lambda w: w["extra_km"])
            suspect_idx = next(
                (i for i, s in enumerate(current) if s.get("id") == worst["suspect_id"]),
                None,
            )
            if suspect_idx is None:
                break
            new_seq, new_pos, before_km, after_km = _relocate_stop_haversine(
                current, suspect_idx
            )
            if after_km >= before_km - 1e-6:
                break
            moves.append({
                "moved_stop_id": worst["suspect_id"],
                "from_position": suspect_idx,
                "to_position": new_pos,
                "saved_km": round(before_km - after_km, 3),
                "kind": "relocate",
            })
            current = new_seq
            applied += 1
        return applied

    # Alternate relocate ↔ 2-opt up to `max_passes` rounds. Each round
    # accepts only strict haversine improvements, so the loop is monotone
    # and terminates when both move generators are stuck.
    for _ in range(max_passes):
        relocated = _relocate_loop()
        before_two_opt_km = _haversine_path_km(current)
        new_seq, swaps = _two_opt_pass(current)
        if swaps:
            current = new_seq
            moves.append({
                "moved_stop_id": None,
                "from_position": None,
                "to_position": None,
                "saved_km": round(before_two_opt_km - _haversine_path_km(current), 3),
                "kind": "two_opt",
                "swaps": swaps,
            })
        if relocated == 0 and swaps == 0:
            break
    return current, moves


async def _persist_pending_order(user_id: str, ordered: List[dict]) -> None:
    """Bulk-write the new `order` field for every stop in `ordered`."""
    from server import db  # noqa: WPS433
    from pymongo import UpdateOne as _BulkOp
    bulk_ops = [
        _BulkOp(
            {"id": s["id"], "user_id": user_id},
            {"$set": {"order": i}},
        )
        for i, s in enumerate(ordered)
    ]
    if bulk_ops:
        await db.stops.bulk_write(bulk_ops, ordered=False)


async def _osrm_verify_relocation(
    original_seq: List[dict],
    proposed_seq: List[dict],
    slack_seconds: int = 0,
    slack_ratio: float = 0.0,
) -> Tuple[List[dict], Optional[int], Optional[int], bool]:
    """Cost two stop sequences against the OSRM duration matrix and pick
    whichever takes less time on the road.

    Haversine is a fine visual proxy for "this looks zig-zaggy", but real
    drivers feel OSRM seconds. We fetch the duration matrix once (on the
    proposed sequence — same stops as original, just reordered) and re-cost
    both orderings. If OSRM agrees the relocation is faster (or ties), we
    keep it. If it disagrees, we roll back to the original. The matrix is
    only fetched once per call; for medium routes it's already cached by
    the wider /api/optimize matrix cache.

    `slack_seconds` / `slack_ratio` tune how much driving-time the cleaned
    sequence is allowed to add. Defaults are zero (strict OSRM-wins) for
    manual tighten endpoints, so an explicit user tap never makes them
    slower. The auto-tighten path inside `/api/optimize` passes a small
    tolerance because real drivers prefer a visually clean route over a
    1–2% faster one with a single obvious cross-suburb detour. The actual
    threshold is `max(slack_seconds, before_s * slack_ratio)`.

    Returns:
        (chosen_seq, before_seconds, after_seconds, rolled_back)
        before_seconds / after_seconds are `None` if OSRM was unreachable
        and verification couldn't be performed (in which case we fall
        through, keeping the proposed sequence).
    """
    from server import _osrm_duration_matrix  # noqa: WPS433
    try:
        # Pull the matrix straight from the local OSRM Table service. This
        # used to call `calculate_duration_matrix`, which silently falls
        # back to a haversine estimate for N>25 (the Mapbox cap), so on
        # 100+-stop routes the verification was a haversine check
        # masquerading as an OSRM check — defeating the entire purpose
        # of "did the cleaner sequence actually win on driving time?".
        # Using `_osrm_duration_matrix` keeps every verification grounded
        # in real road-network seconds (with the public OSRM demo as a
        # last-resort fallback inside that helper).
        duration_matrix = await _osrm_duration_matrix(proposed_seq)
    except Exception as exc:
        logger.debug(f"OSRM duration matrix fetch failed: {exc}")
        return proposed_seq, None, None, False

    if not duration_matrix:
        return proposed_seq, None, None, False

    # `proposed_seq` and `original_seq` are permutations of the same stops, so
    # we can build the id→row map once on `proposed_seq` and use it to look up
    # rows for either ordering.
    id_to_row = {s["id"]: i for i, s in enumerate(proposed_seq)}

    def _seq_seconds(seq: List[dict]) -> int:
        total = 0
        for k in range(len(seq) - 1):
            i = id_to_row[seq[k]["id"]]
            j = id_to_row[seq[k + 1]["id"]]
            total += int(duration_matrix[i][j])
        return total

    before_s = _seq_seconds(original_seq)
    after_s = _seq_seconds(proposed_seq)

    tolerance = max(slack_seconds, int(before_s * slack_ratio))
    if after_s > before_s + tolerance:
        # OSRM disagrees with the visual fix beyond the allowed slack. Roll back.
        return original_seq, before_s, after_s, True
    return proposed_seq, before_s, after_s, False


@router.post("/optimize/tighten-cluster")
async def tighten_cluster(
    request: TightenClusterRequest,
    current_user: User = Depends(_current_user),
    _pro=Depends(_billing_require_pro),
):
    """One-tap "fix this zig-zag" handler.

    Lifts a single suspect stop B (identified by `suspect_id`) out of its
    current slot and re-inserts it at the position that minimises the
    haversine perimeter of the route. Then double-checks against OSRM
    seconds — if the relocation actually costs driving time we roll back
    rather than mislead the driver. Persists the chosen order to Mongo and
    returns the refreshed sequence + remaining cluster warnings.
    """
    from server import db, detect_cluster_spikes  # noqa: WPS433
    pending = await db.stops.find(
        {"user_id": current_user.user_id, "completed": {"$ne": True}},
        {"_id": 0},
    ).sort("order", 1).to_list(2000)

    suspect_idx = next(
        (i for i, s in enumerate(pending) if s.get("id") == request.suspect_id),
        None,
    )
    if suspect_idx is None:
        raise HTTPException(
            status_code=404,
            detail=f"suspect_id {request.suspect_id} not found in pending stops",
        )
    if len(pending) < 3:
        raise HTTPException(
            status_code=400,
            detail="Need at least 3 pending stops to tighten a cluster",
        )

    proposed, best_position, before_km, after_km = _relocate_stop_haversine(
        pending, suspect_idx
    )
    chosen, before_s, after_s, rolled_back = await _osrm_verify_relocation(
        pending, proposed
    )
    await _persist_pending_order(current_user.user_id, chosen)

    return {
        "message": (
            "Visual fix declined: OSRM says driving time would increase"
            if rolled_back
            else "Cluster tightened"
        ),
        "moved_stop_id": request.suspect_id,
        "from_position": suspect_idx,
        "to_position": suspect_idx if rolled_back else best_position,
        "rolled_back": rolled_back,
        "haversine_km_before": round(before_km, 3),
        "haversine_km_after": round(
            before_km if rolled_back else after_km, 3
        ),
        "saved_km": (
            0.0 if rolled_back else round(max(0.0, before_km - after_km), 3)
        ),
        "driving_seconds_before": before_s,
        "driving_seconds_after": (
            before_s if rolled_back else after_s
        ),
        "driving_seconds_saved": (
            None
            if before_s is None or after_s is None
            else 0
            if rolled_back
            else max(0, before_s - after_s)
        ),
        "stops": chosen,
        "optimized_sequence": [s["id"] for s in chosen],
        "cluster_warnings": _filter_actionable_warnings(
            chosen, detect_cluster_spikes(chosen)
        ),
    }


@router.post("/optimize/tighten-clusters")
async def tighten_all_clusters(
    current_user: User = Depends(_current_user),
    _pro=Depends(_billing_require_pro),
):
    """Iteratively tighten every detected spike in the current pending route.

    Loop until `detect_cluster_spikes` returns an empty list (or we hit a
    safety cap). On every pass we relocate the *worst* spike — the one
    with the largest `extra_km` (most map-distance wasted) — and persist
    the move at the end of the loop. This produces a strictly-improving
    haversine path with no manual intervention.

    The safety cap (`MAX_PASSES = 10`) prevents runaway loops in pathological
    cases where two spikes oscillate; in practice real-world routes
    converge in 1–3 passes.
    """
    from server import db, detect_cluster_spikes  # noqa: WPS433
    pending = await db.stops.find(
        {"user_id": current_user.user_id, "completed": {"$ne": True}},
        {"_id": 0},
    ).sort("order", 1).to_list(2000)

    if len(pending) < 3:
        return {
            "message": "Nothing to tighten",
            "moves": [],
            "passes": 0,
            "haversine_km_before": round(_haversine_path_km(pending), 3),
            "haversine_km_after": round(_haversine_path_km(pending), 3),
            "saved_km": 0.0,
            "stops": pending,
            "optimized_sequence": [s["id"] for s in pending],
            "cluster_warnings": [],
        }

    initial_km = _haversine_path_km(pending)

    # Delegate to the shared tightener: alternates relocate + 2-opt until
    # both move generators are stuck. This is the same engine the auto-
    # tighten path inside /api/optimize uses, so a manual tap and an
    # automatic pass produce the same final state.
    current, moves = _iterative_haversine_tighten(pending)

    if moves:
        # Apply the haversine-best chain BEFORE the OSRM verification.
        # The verification will roll back to `pending` if OSRM disagrees.
        chosen, before_s, after_s, rolled_back = await _osrm_verify_relocation(
            pending, current
        )
        await _persist_pending_order(current_user.user_id, chosen)
        current = chosen
    else:
        before_s = after_s = None
        rolled_back = False

    final_km = _haversine_path_km(current)
    # Observability — one line per tighten call so we can answer "is this
    # actually doing anything?" from the prod log without instrumenting
    # the silent rolled-back path. Includes everything that decides the
    # user-visible outcome: move count, OSRM verdict, and km/seconds
    # delta.
    logger.info(
        "[tighten-clusters] user=%s pending=%d moves=%d rolled_back=%s "
        "haversine_before=%.2fkm haversine_after=%.2fkm "
        "osrm_before=%ss osrm_after=%ss",
        current_user.user_id,
        len(pending),
        len(moves),
        rolled_back,
        initial_km,
        final_km,
        before_s if before_s is not None else "n/a",
        after_s if after_s is not None else "n/a",
    )
    return {
        "message": (
            "Visual fix declined: OSRM says driving time would increase"
            if rolled_back
            else (
                f"Tightened {len(moves)} cluster"
                f"{'s' if len(moves) != 1 else ''}"
                if moves
                else "Route already clean"
            )
        ),
        "moves": [] if rolled_back else moves,
        "passes": 0 if rolled_back else len(moves),
        "rolled_back": rolled_back,
        "haversine_km_before": round(initial_km, 3),
        "haversine_km_after": round(final_km, 3),
        "saved_km": round(max(0.0, initial_km - final_km), 3),
        "driving_seconds_before": before_s,
        "driving_seconds_after": (
            before_s if rolled_back else after_s
        ),
        "driving_seconds_saved": (
            None
            if before_s is None or after_s is None
            else max(0, before_s - after_s)
            if not rolled_back
            else 0
        ),
        "stops": current,
        "optimized_sequence": [s["id"] for s in current],
        "cluster_warnings": (
            # Honest banner: when OSRM rolled the chain back, the algorithm
            # has just *proven* nothing in the current route is fixable
            # without driving longer than the slack budget allows. Suppress
            # the banner entirely — leaving stale warnings on screen after
            # a no-op tap is the precise UI lie this whole feature was
            # designed to eliminate.
            []
            if rolled_back
            else _filter_actionable_warnings(
                current, detect_cluster_spikes(current)
            )
        ),
    }


@router.get("/optimize/algorithms")
async def list_optimization_algorithms(response: Response):
    """List available optimization algorithms with descriptions"""
    response.headers["Cache-Control"] = "public, max-age=86400"  # 24h — static data
    return {
        "algorithms": [
            {
                "id": "auto",
                "name": "Auto Select",
                "description": "Automatically selects the best algorithm based on route size",
                "best_for": "All route sizes"
            },
            {
                "id": "alns",
                "name": "ALNS Hybrid",
                "description": "Adaptive Large Neighbourhood Search with Simulated Annealing and Local Search polish",
                "best_for": "Medium to large routes (10-100+ stops)",
                "complexity": "O(iterations × n)"
            },
            {
                "id": "ortools",
                "name": "OR-Tools",
                "description": "Google OR-Tools single-vehicle optimization prioritizing travel time, then distance",
                "best_for": "High-quality sequencing with fallback safety",
                "complexity": "CP-SAT / local search"
            },
            {
                "id": "pyvrp",
                "name": "PyVRP (HGS)",
                "description": "State-of-the-art Hybrid Genetic Search — minimises total driving time (OSRM duration matrix) for a single driver pure TSP",
                "best_for": "Fastest drop-off sequencing, no time windows, 10-200 stops",
                "complexity": "HGS population-based metaheuristic"
            },
            {
                "id": "nearest_neighbor",
                "name": "Nearest Neighbor",
                "description": "Fast greedy algorithm that always visits the closest unvisited stop",
                "best_for": "Large routes (50+ stops), quick estimates",
                "complexity": "O(n²)"
            },
            {
                "id": "two_opt",
                "name": "2-Opt",
                "description": "Improvement heuristic that reverses route segments to reduce distance",
                "best_for": "Small to medium routes (up to 25 stops)",
                "complexity": "O(n²) per iteration"
            },
            {
                "id": "simulated_annealing",
                "name": "Simulated Annealing",
                "description": "Probabilistic meta-heuristic inspired by metallurgy. Accepts worse solutions early to escape local optima",
                "best_for": "Medium routes (15-40 stops)",
                "complexity": "O(n × iterations)"
            },
            {
                "id": "genetic",
                "name": "Genetic Algorithm",
                "description": "Evolutionary algorithm that evolves a population of solutions through selection, crossover, and mutation",
                "best_for": "Complex routes with many constraints (20-60 stops)",
                "complexity": "O(population × generations × n)"
            },
            {
                "id": "clarke_wright",
                "name": "Clarke-Wright Savings",
                "description": "Classic VRP algorithm that builds routes by merging based on distance savings from depot",
                "best_for": "Delivery routes starting from a depot/warehouse",
                "complexity": "O(n² log n)"
            },
            {
                "id": "generoute",
                "name": "Generoute",
                "description": "Cloud-based route optimization using real road network data via Generoute API",
                "best_for": "Accurate road-based optimization (up to 1000 stops)",
                "complexity": "Cloud API"
            },
            {
                "id": "mapbox",
                "name": "Mapbox Optimization",
                "description": "Road-based optimization using Mapbox Optimization API",
                "best_for": "Small routes requiring accurate driving directions (up to 12 stops)",
                "complexity": "Cloud API"
            },
        ]
    }


@router.get("/generoute/status")
async def generoute_status(current_user: User = Depends(_current_user)):
    """Check if Generoute API is configured and available."""
    from server import GENEROUTE_API_KEY  # noqa: WPS433
    configured = bool(GENEROUTE_API_KEY)
    return {
        "configured": configured,
        "api_key_set": configured,
        "services": {
            "route_optimization": configured
        }
    }
