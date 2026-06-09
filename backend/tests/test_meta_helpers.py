"""Unit tests for the pure telemetry-rollup helpers in routes/meta.py.

`_percentile` and `_aggregate_rollup` own the maths behind the
`/_meta/telemetry-rollup` endpoint. The existing `test_telemetry_rollup.py`
exercises that endpoint over live HTTP (needs a running server + Mongo); these
tests pin the aggregation maths directly so a regression is caught fast and
without infrastructure.

`routes/meta.py` imports `server` at module top (for the auth dependency), so
we seed a default `MONGO_URL` before importing — Motor connects lazily, so no
live DB is required.
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "routed_test")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

# Import `server` first so its router wiring completes before we import the
# route module directly (avoids a partially-initialised circular import).
import server  # noqa: E402,F401

from routes.meta import (  # noqa: E402
    _aggregate_rollup,
    _percentile,
    _seven_days_ago_iso,
    _today_utc_iso,
)


# ── _percentile ────────────────────────────────────────────────────────────

def test_percentile_empty_is_none():
    assert _percentile([], 0.5) is None


def test_percentile_nearest_rank():
    arr = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert _percentile(arr, 0.0) == 1.0
    assert _percentile(arr, 0.5) == 3.0
    # Nearest-rank with truncation: idx = int(0.95 * 4) = 3 → arr[3].
    assert _percentile(arr, 0.95) == 4.0
    # Only q=1.0 reaches the final element.
    assert _percentile(arr, 1.0) == 5.0


def test_percentile_rounds_to_one_decimal():
    assert _percentile([1.234, 2.345, 3.456], 0.5) == 2.3


def test_percentile_clamps_index_within_bounds():
    # q=1.0 must not index past the end.
    assert _percentile([10.0, 20.0], 1.0) == 20.0


# ── _aggregate_rollup ──────────────────────────────────────────────────────

def test_rollup_empty_returns_zeroed_shape():
    out = _aggregate_rollup([])
    assert out["archived_routes"] == 0
    assert out["best_route"] is None
    assert out["geofence_rate"] is None
    assert out["distance_samples"] == 0
    assert out["service_samples"] == 0


def _stop(method, completion_distance_m=None, arrived_at=None, completed_at=None):
    s = {"arrival_method": method}
    if completion_distance_m is not None:
        s["completion_distance_m"] = completion_distance_m
    if arrived_at is not None:
        s["arrived_at"] = arrived_at
    if completed_at is not None:
        s["completed_at"] = completed_at
    return s


def test_rollup_counts_arrival_methods():
    routes = [
        {
            "stops": [
                _stop("geofence", completion_distance_m=5.0),
                _stop("geofence_inferred"),
                _stop("fallback_completion"),
                _stop("geofence", completion_distance_m=15.0),
            ],
            "summary": {"delivered": 2, "algorithm": "ortools", "total_stops": 4},
        }
    ]
    out = _aggregate_rollup(routes)
    assert out["archived_routes"] == 1
    assert out["geofence_count"] == 2
    assert out["geofence_inferred_count"] == 1
    assert out["fallback_count"] == 1
    # 3 of 4 stops had a proximity-based arrival method.
    assert out["arrival_proximity_rate"] == round(3 / 4, 3)
    # geofence-only share of those with any arrival method = 2/4.
    assert out["geofence_rate"] == round(2 / 4, 3)
    assert out["distance_samples"] == 2
    assert out["completion_distance_p50_m"] is not None


def test_rollup_service_seconds_only_from_geofence_with_timestamps():
    arrived = "2026-06-01T10:00:00+00:00"
    completed = "2026-06-01T10:00:45+00:00"
    routes = [
        {
            "stops": [
                # Counted: real geofence arrival with both timestamps.
                _stop("geofence", arrived_at=arrived, completed_at=completed),
                # Excluded: inferred arrivals carry a constant back-date.
                _stop("geofence_inferred", arrived_at=arrived, completed_at=completed),
            ],
            "summary": {"delivered": 2},
        }
    ]
    out = _aggregate_rollup(routes)
    assert out["service_samples"] == 1
    assert out["service_seconds_p50"] == 45.0


def test_rollup_best_route_is_highest_delivered():
    routes = [
        {"stops": [], "summary": {"delivered": 3, "algorithm": "alns"}},
        {"stops": [], "summary": {"delivered": 9, "algorithm": "ortools"}},
        {"stops": [], "summary": {"delivered": 1, "algorithm": "two_opt"}},
    ]
    out = _aggregate_rollup(routes)
    assert out["best_route"]["delivered"] == 9
    assert out["best_route"]["algorithm"] == "ortools"


def test_rollup_tolerates_malformed_service_timestamps():
    routes = [
        {
            "stops": [_stop("geofence", arrived_at="not-a-date", completed_at="also-bad")],
            "summary": {"delivered": 1},
        }
    ]
    out = _aggregate_rollup(routes)  # must not raise
    assert out["service_samples"] == 0


# ── date window helpers ────────────────────────────────────────────────────

def test_today_utc_iso_is_midnight_utc():
    val = datetime.fromisoformat(_today_utc_iso())
    assert (val.hour, val.minute, val.second, val.microsecond) == (0, 0, 0, 0)
    assert val.tzinfo is not None


def test_seven_days_ago_is_roughly_a_week_back():
    val = datetime.fromisoformat(_seven_days_ago_iso())
    delta = datetime.now(timezone.utc) - val
    assert timedelta(days=6, hours=23) < delta < timedelta(days=7, hours=1)
