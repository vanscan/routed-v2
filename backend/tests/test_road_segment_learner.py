"""Live integration test for road_segment_learner.

Hits the public OSRM /match endpoint with a small synthetic breadcrumb,
verifies it produces edge keys, and that record/score round-trips through
MongoDB cleanly.
"""
import asyncio
import os
import sys
sys.path.insert(0, "/app/backend")

from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient
from ml.road_segment_learner import (
    record_route_breadcrumb,
    score_polyline,
    get_stats,
    reset,
    _decimate,
    _osrm_match,
    _nodes_to_edge_keys,
)

TEST_USER = "test_road_learner_user"

# Real Sunshine Coast QLD coords (Alexandra Headland area) — local
# OSRM is loaded with QLD-only data so coords must be in-region. We
# pulled these from OSRM /route to guarantee they sit on real road
# centerlines without GPS noise to confuse the matcher.
BREADCRUMB = [
    {"lat": -26.672757, "lng": 153.112329},
    {"lat": -26.672626, "lng": 153.112134},
    {"lat": -26.672583, "lng": 153.112043},
    {"lat": -26.672400, "lng": 153.111800},
    {"lat": -26.672100, "lng": 153.111400},
]


async def main():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ.get("DB_NAME", "test_database")]
    await reset(db, TEST_USER)
    print("Reset done.")

    # 1. Test decimation
    dec = _decimate(BREADCRUMB)
    print(f"Decimated: {len(BREADCRUMB)} → {len(dec)} points")
    assert len(dec) >= 2

    # 2. Test OSRM match directly
    coords = [(p["lng"], p["lat"]) for p in BREADCRUMB]
    nodes = await _osrm_match(coords)
    print(f"OSRM /match returned {len(nodes) if nodes else 0} nodes")
    assert nodes and len(nodes) >= 2, "OSRM /match should return ≥2 nodes"

    edges = _nodes_to_edge_keys(nodes)
    print(f"Derived {len(edges)} unique edges")
    assert len(edges) >= 1

    # 3. Test full record_route_breadcrumb
    result = await record_route_breadcrumb(db, TEST_USER, BREADCRUMB)
    print(f"Record result: {result}")
    assert result["recorded"] is True, "Should record successfully"
    assert result["edges"] >= 1

    # 4. Test get_stats
    stats = await get_stats(db, TEST_USER)
    print(f"Stats: {stats}")
    assert stats["total_edges"] >= 1

    # 5. Run record twice more to build up "frequent" edges (≥3 uses)
    for _ in range(2):
        await record_route_breadcrumb(db, TEST_USER, BREADCRUMB)
    stats2 = await get_stats(db, TEST_USER)
    print(f"Stats after 3 records: {stats2}")
    assert stats2["frequent_edges"] >= 1, "expected frequent edges after 3 traversals"
    assert stats2["ready"] is True

    # 6. Test score_polyline with the same path → should be high score
    score, matched, total = await score_polyline(db, TEST_USER, coords)
    print(f"Familiarity score: {score:.2f} ({matched}/{total} edges matched)")
    assert score > 0.5, f"familiarity should be high after 3 same-path traversals (got {score})"

    # 7. Cleanup
    await reset(db, TEST_USER)
    print("✅ Phase B road learner test passed.")


if __name__ == "__main__":
    asyncio.run(main())
