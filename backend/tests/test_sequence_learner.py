"""Unit-ish test for the sequence learner — runs end-to-end against
the live local MongoDB. Safe to re-run; uses a throwaway user_id.
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/app/backend")

from motor.motor_asyncio import AsyncIOMotorClient
from ml.sequence_learner import (
    record_completion,
    apply_preferences,
    get_stats,
    reset,
)

TEST_USER = "test_seq_learner_user"

# Fake stops at three distinct addresses; we'll deliver C → A → B
# three times in a row so the learner has high confidence.
def make_route(delivery_order_keys, when):
    """Build a route_doc with 3 stops delivered in the given order."""
    base = datetime(2026, 5, 24, 8, 0, 0, tzinfo=timezone.utc) + timedelta(days=when)
    coords = {
        "A": (-26.7, 153.1),
        "B": (-26.71, 153.11),
        "C": (-26.72, 153.12),
    }
    stops = []
    for i, k in enumerate(delivery_order_keys):
        lat, lng = coords[k]
        stops.append({
            "id": f"{k}-{when}",
            "lat": lat,
            "lng": lng,
            "completed": True,
            "completed_at": (base + timedelta(minutes=10 * i)).isoformat(),
        })
    return {"stops": stops}


async def main():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ.get("DB_NAME", "test_database")]

    # Clean slate
    await reset(db, TEST_USER)
    print("Reset done.")

    # Record three completions: C → A → B
    for day in range(3):
        route = make_route(["C", "A", "B"], day)
        s = await record_completion(db, TEST_USER, route)
        print(f"Day {day}: {s}")

    # Stats: 3 pairs (CA, CB, AB) should be high-confidence
    stats = await get_stats(db, TEST_USER)
    print("Stats:", stats)
    assert stats["high_confidence_pairs"] == 3, (
        f"expected 3 high-conf pairs, got {stats['high_confidence_pairs']}"
    )

    # Now feed the optimizer a different order: A → B → C
    # The learner should reorder to C → A → B (or close to it).
    optimized = [
        {"id": "A-new", "lat": -26.7, "lng": 153.1},
        {"id": "B-new", "lat": -26.71, "lng": 153.11},
        {"id": "C-new", "lat": -26.72, "lng": 153.12},
    ]
    result = await apply_preferences(db, TEST_USER, optimized)
    print("Result:", result)
    print("Final order:", [s["id"] for s in optimized])
    # We expect at least one swap. After single forward pass starting
    # from A→B→C and prefs C-before-A, C-before-B, A-before-B:
    # i=0: A,B → pref says A-before-B is winner (A first). No swap.
    # i=1: B,C → pref says C-before-B. Swap → A,C,B
    # i=3: stop. Final: A,C,B (one swap).
    assert result["applied"] is True, "expected at least one swap"
    assert len(result["swaps"]) >= 1, "expected swaps list to be non-empty"
    print(f"✅ Applied {len(result['swaps'])} swap(s)")

    # Clean up
    await reset(db, TEST_USER)
    print("✅ All assertions passed.")

if __name__ == "__main__":
    # Load env from backend/.env if running standalone
    from dotenv import load_dotenv
    load_dotenv("/app/backend/.env")
    asyncio.run(main())
