"""
Route Telepathy — Phase A: Sequence Preference Learner.

Mechanism
─────────
1. On each route completion, extract the delivered stops' actual ordering
   (by `completed_at`) and compare against every other delivered stop.
   For each ordered pair (A delivered before B), increment a counter in
   the `sequence_preferences` collection.

2. On the next optimization, after the solver returns its sequence, scan
   each adjacent pair (i, i+1). If the user has a high-confidence
   preference that the LATER stop should come first, swap them.

A "location key" identifies a place across runs even when its `id` rotates:
   loc_key = f"{round(lat, 5)}:{round(lng, 5)}"
   (~1 m precision — same driveway always hashes the same).

Storage shape (collection: `sequence_preferences`)
   {
     user_id: "user_2a7d88cbb419",
     pair_key: "<a_loc>::<b_loc>",   # sorted alphabetically
     a_before_b: int,                 # times the smaller key came first
     b_before_a: int,                 # times the larger key came first
     last_seen: ISO datetime,
   }

Confidence
   conf = abs(a_before_b - b_before_a) / max(1, a_before_b + b_before_a)
   We only act when conf >= 0.6 AND total observations >= 3.

This module is scoped to a single user_id at the caller level — we never
share preferences across drivers.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import logging

logger = logging.getLogger(__name__)

# Tunables — kept conservative for v1 so we don't make aggressive swaps.
MIN_OBSERVATIONS = 3
CONFIDENCE_THRESHOLD = 0.6


def _loc_key(stop: Dict[str, Any]) -> Optional[str]:
    """Stable identity for a stop across imports/route runs."""
    lat = stop.get("lat") or stop.get("latitude")
    lng = stop.get("lng") or stop.get("longitude")
    if lat is None or lng is None:
        return None
    try:
        return f"{round(float(lat), 5)}:{round(float(lng), 5)}"
    except (TypeError, ValueError):
        return None


def _pair_key(a: str, b: str) -> Tuple[str, bool]:
    """Sort (a, b) alphabetically. Returns (key, a_came_first).

    `a_came_first` indicates which side of the pair_key corresponds to the
    OBSERVED ordering — used to know which counter to increment.
    """
    if a < b:
        return f"{a}::{b}", True
    return f"{b}::{a}", False


async def record_completion(db, user_id: str, route_doc: Dict[str, Any]) -> Dict[str, int]:
    """Extract sequence preferences from a freshly-archived route.

    Returns a small summary for logging/UI: how many pairs were observed.
    """
    stops = route_doc.get("stops") or []
    delivered = [
        s for s in stops
        if s.get("completed") and s.get("completed_at")
    ]
    # Need at least 2 delivered stops to form a pair.
    if len(delivered) < 2:
        return {"pairs_recorded": 0, "stops_delivered": len(delivered)}

    # Sort by actual completion time → the ground-truth driver-chosen order.
    def _ts(s):
        v = s.get("completed_at")
        if isinstance(v, datetime):
            return v
        try:
            return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        except Exception:
            return datetime.min.replace(tzinfo=timezone.utc)

    delivered.sort(key=_ts)
    keys = [_loc_key(s) for s in delivered]
    keys = [k for k in keys if k]  # drop any without coords

    now = datetime.now(timezone.utc).isoformat()
    pairs_recorded = 0
    # For every (earlier, later) pair: record that earlier preceded later.
    # We use bulk operations to keep it cheap even on 50+ stop routes.
    bulk_ops = []
    from pymongo import UpdateOne

    for i, earlier in enumerate(keys):
        for later in keys[i + 1:]:
            if earlier == later:
                continue
            pair_key, earlier_is_a = _pair_key(earlier, later)
            inc_field = "a_before_b" if earlier_is_a else "b_before_a"
            bulk_ops.append(UpdateOne(
                {"user_id": user_id, "pair_key": pair_key},
                {
                    "$inc": {inc_field: 1},
                    "$set": {"last_seen": now},
                    "$setOnInsert": {
                        "a_before_b" if not earlier_is_a else "b_before_a": 0,
                    },
                },
                upsert=True,
            ))
            pairs_recorded += 1

    if bulk_ops:
        await db.sequence_preferences.bulk_write(bulk_ops, ordered=False)

    return {"pairs_recorded": pairs_recorded, "stops_delivered": len(delivered)}


async def _load_prefs(db, user_id: str, loc_keys: List[str]) -> Dict[str, Dict[str, Any]]:
    """Fetch every preference record relevant to the supplied stops."""
    if not loc_keys:
        return {}
    # Build the set of pair_keys we could possibly need.
    pair_keys = set()
    for i, a in enumerate(loc_keys):
        for b in loc_keys[i + 1:]:
            if a and b and a != b:
                pk, _ = _pair_key(a, b)
                pair_keys.add(pk)
    if not pair_keys:
        return {}
    cursor = db.sequence_preferences.find(
        {"user_id": user_id, "pair_key": {"$in": list(pair_keys)}},
        {"_id": 0},
    )
    docs = await cursor.to_list(length=len(pair_keys))
    return {d["pair_key"]: d for d in docs}


def _confidence(doc: Dict[str, Any]) -> Tuple[float, int, bool]:
    """Return (confidence, total_obs, a_before_b_is_winner)."""
    a = int(doc.get("a_before_b", 0))
    b = int(doc.get("b_before_a", 0))
    total = a + b
    if total == 0:
        return 0.0, 0, False
    return abs(a - b) / total, total, a >= b


async def apply_preferences(
    db,
    user_id: str,
    optimized_stops: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Mutate `optimized_stops` in-place, swapping adjacent pairs where
    the driver has a strong historical preference for the opposite order.

    Returns metadata about which swaps were applied (for the response so
    the UI can show "We re-ordered 3 stops based on your history").
    """
    if len(optimized_stops) < 2:
        return {"applied": False, "swaps": [], "reason": "too_few_stops"}

    loc_keys = [_loc_key(s) for s in optimized_stops]
    prefs = await _load_prefs(db, user_id, [k for k in loc_keys if k])
    if not prefs:
        return {"applied": False, "swaps": [], "reason": "no_history"}

    swaps: List[Dict[str, Any]] = []
    # Single forward pass — keeps it deterministic and bounded.
    # A second pass could further refine but risks cascading reorderings
    # that contradict each other; one pass is enough for v1.
    i = 0
    while i < len(optimized_stops) - 1:
        a_key, b_key = loc_keys[i], loc_keys[i + 1]
        if not a_key or not b_key or a_key == b_key:
            i += 1
            continue
        pair_key, a_is_first_in_key = _pair_key(a_key, b_key)
        doc = prefs.get(pair_key)
        if not doc:
            i += 1
            continue
        conf, total, a_wins = _confidence(doc)
        if total < MIN_OBSERVATIONS or conf < CONFIDENCE_THRESHOLD:
            i += 1
            continue
        # Decide whether the historical winner contradicts current order.
        # `a_wins` means the alphabetically-smaller key wins historically.
        historical_first = pair_key.split("::")[0] if a_wins else pair_key.split("::")[1]
        current_first = a_key  # what the solver put first
        if historical_first != current_first:
            # Swap.
            optimized_stops[i], optimized_stops[i + 1] = optimized_stops[i + 1], optimized_stops[i]
            loc_keys[i], loc_keys[i + 1] = loc_keys[i + 1], loc_keys[i]
            swaps.append({
                "from_index": i,
                "to_index": i + 1,
                "stop_a_id": optimized_stops[i + 1].get("id"),
                "stop_b_id": optimized_stops[i].get("id"),
                "confidence": round(conf, 2),
                "observations": total,
            })
            # Skip the next slot since we just touched it.
            i += 2
        else:
            i += 1

    if swaps:
        logger.info(
            "[sequence_learner] applied %d swap(s) for user=%s",
            len(swaps), user_id,
        )
    return {
        "applied": len(swaps) > 0,
        "swaps": swaps,
        "reason": "ok" if swaps else "no_strong_preferences",
    }


async def get_stats(db, user_id: str) -> Dict[str, Any]:
    """Lightweight stats for the 'Telepathy' UI card."""
    cursor = db.sequence_preferences.find(
        {"user_id": user_id},
        {"_id": 0, "pair_key": 1, "a_before_b": 1, "b_before_a": 1, "last_seen": 1},
    )
    docs = await cursor.to_list(length=10000)
    if not docs:
        return {
            "total_pairs": 0,
            "high_confidence_pairs": 0,
            "ready": False,
            "needs_more_routes": True,
        }
    high_conf = 0
    for d in docs:
        conf, total, _ = _confidence(d)
        if total >= MIN_OBSERVATIONS and conf >= CONFIDENCE_THRESHOLD:
            high_conf += 1
    return {
        "total_pairs": len(docs),
        "high_confidence_pairs": high_conf,
        "ready": high_conf > 0,
        "needs_more_routes": high_conf == 0,
        "min_observations": MIN_OBSERVATIONS,
        "confidence_threshold": CONFIDENCE_THRESHOLD,
    }


async def reset(db, user_id: str) -> int:
    """Wipe all learned preferences for this user. Returns rows deleted."""
    result = await db.sequence_preferences.delete_many({"user_id": user_id})
    return int(result.deleted_count or 0)
