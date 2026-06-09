"""Unit tests for the pure analysis helpers in routes/optimize_tools.py.

We test `_analyze_route_characteristics` and `_recommend_algorithm` directly
rather than booting the FastAPI app: the recommendation decision tree is pure
(no DB, no network), so importing the module and calling the helpers is both
faster and far more robust than the live-HTTP `test_recommendation.py` suite
(which needs a running server and only ever exercised the happy path).

`routes/optimize_tools.py` does `from server import get_current_user` at module
top, so importing it pulls in `server`, which reads `MONGO_URL` at import time.
We set a default before importing so the test is self-contained even without a
populated `backend/.env` (Motor connects lazily — no live DB is touched).
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "routed_test")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

# Import `server` first: it wires the route routers at the end of its module
# body, so importing `routes.optimize_tools` cold would hit a partially-loaded
# `server` (circular import). Once `server` is fully loaded, the route module is
# already initialised.
import server  # noqa: E402,F401

from routes.optimize_tools import (  # noqa: E402
    _analyze_route_characteristics,
    _recommend_algorithm,
)


def _line_matrix(n: int, step: float = 1.0):
    """Symmetric distance matrix for n stops evenly spaced on a line."""
    return [[abs(i - j) * step for j in range(n)] for i in range(n)]


# ── _analyze_route_characteristics ────────────────────────────────────────

def test_analyze_returns_only_stop_count_for_trivial_route():
    chars = _analyze_route_characteristics([{}], [[0.0]])
    assert chars == {"stop_count": 1}


def test_analyze_reports_expected_keys_and_spread():
    n = 5
    stops = [{} for _ in range(n)]
    chars = _analyze_route_characteristics(stops, _line_matrix(n, step=2.0))
    assert chars["stop_count"] == n
    # Farthest pair on a 5-point line with step 2.0 is (5-1)*2 = 8.
    assert chars["spread_km"] == 8.0
    for key in (
        "avg_distance_km",
        "avg_nn_distance_km",
        "cluster_ratio",
        "cluster_count",
        "complexity",
    ):
        assert key in chars


@pytest.mark.parametrize(
    "n,expected",
    [(5, "low"), (14, "low"), (15, "medium"), (59, "medium"), (60, "high"), (120, "high")],
)
def test_analyze_complexity_buckets(n, expected):
    stops = [{} for _ in range(n)]
    chars = _analyze_route_characteristics(stops, _line_matrix(n))
    assert chars["complexity"] == expected


def test_analyze_nearest_neighbour_is_step_for_evenly_spaced_line():
    n = 6
    chars = _analyze_route_characteristics([{} for _ in range(n)], _line_matrix(n, step=1.0))
    # Each point's nearest neighbour is exactly one step away.
    assert chars["avg_nn_distance_km"] == 1.0


# ── _recommend_algorithm ───────────────────────────────────────────────────

def test_recommend_none_below_two_stops():
    rec = _recommend_algorithm({"stop_count": 1})
    assert rec["algorithm"] == "none"
    assert rec["confidence"] == 1.0


def test_recommend_two_opt_for_tiny_routes():
    rec = _recommend_algorithm({"stop_count": 8})
    assert rec["algorithm"] == "two_opt"
    assert "nearest_neighbor" in rec["alternatives"]


def test_recommend_ortools_for_small_few_clusters():
    rec = _recommend_algorithm({"stop_count": 20, "cluster_count": 2, "cluster_ratio": 0.5})
    assert rec["algorithm"] == "ortools"


def test_recommend_alns_for_medium_multicluster():
    rec = _recommend_algorithm({"stop_count": 50, "cluster_count": 5, "cluster_ratio": 0.5})
    assert rec["algorithm"] == "alns"


def test_recommend_alns_for_large_tightly_clustered():
    rec = _recommend_algorithm({"stop_count": 200, "cluster_count": 2, "cluster_ratio": 0.1})
    assert rec["algorithm"] == "alns"
    assert rec["confidence"] >= 0.9


@pytest.mark.parametrize("n", [9, 20, 30, 80, 300])
def test_recommend_always_returns_required_fields(n):
    rec = _recommend_algorithm({"stop_count": n, "cluster_count": 3, "cluster_ratio": 0.4})
    assert set(rec) >= {"algorithm", "confidence", "reasoning"}
    assert 0.0 < rec["confidence"] <= 1.0
