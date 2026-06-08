"""Regression test: GET /api/stops/export/xlsx must return a valid XLSX
even when some stops have null/missing latitude or longitude (legacy rows
from failed geocoding or direct DB inserts that bypass Pydantic validation).

The pre-fix code called round(stop.get("latitude", 0), 6) — but dict.get()
only substitutes the default when the KEY is absent, not when the value is
None.  A stored latitude=None caused round(None, 6) → TypeError → HTTP 500.

Pattern matches test_billing.py: seed Mongo directly, hit localhost:8001.
"""
from __future__ import annotations

import datetime
import uuid

import pytest

requests = pytest.importorskip("requests")
pymongo = pytest.importorskip("pymongo")

API = "http://localhost:8001/api"


@pytest.fixture(scope="module")
def backend_alive():
    try:
        r = requests.get(f"{API}/healthz", timeout=2)
    except Exception:
        pytest.skip("Backend not reachable at localhost:8001")
    if r.status_code != 200:
        pytest.skip(f"Backend healthz returned {r.status_code}")


@pytest.fixture
def user_with_mixed_stops(backend_alive):
    client = pymongo.MongoClient("mongodb://localhost:27017")
    db = client["test_database"]
    uid = f"xlsx_{uuid.uuid4().hex[:8]}"
    tok = f"sess_{uuid.uuid4().hex}"
    db.users.insert_one({"user_id": uid, "email": f"{uid}@x.com", "name": "T"})
    db.user_sessions.insert_one({
        "session_token": tok, "user_id": uid,
        "expires_at": datetime.datetime.utcnow() + datetime.timedelta(hours=1),
    })
    db.stops.insert_many([
        # Normal stop — fully geocoded
        {
            "id": f"{uid}_ok", "user_id": uid, "address": "1 Normal St",
            "latitude": -27.4698, "longitude": 153.0251,
            "order": 0, "completed": False,
        },
        # Legacy/failed-geocode stop — null coordinates stored in Mongo
        {
            "id": f"{uid}_null", "user_id": uid, "address": "2 Null St",
            "latitude": None, "longitude": None,
            "order": 1, "completed": False,
        },
        # String-typed coordinates from a bad import path
        {
            "id": f"{uid}_str", "user_id": uid, "address": "3 String St",
            "latitude": "-27.9000", "longitude": "153.2000",
            "order": 2, "completed": True,
        },
    ])
    headers = {"Authorization": f"Bearer {tok}"}
    yield headers, uid, db
    db.users.delete_many({"user_id": uid})
    db.user_sessions.delete_many({"user_id": uid})
    db.stops.delete_many({"user_id": uid})


def test_export_xlsx_with_null_coordinates(user_with_mixed_stops):
    """Export must return 200 + a non-trivial XLSX even with null-coord rows.

    Before the fix this returned HTTP 500 (round(None, 6) TypeError)."""
    headers, uid, _db = user_with_mixed_stops
    r = requests.get(f"{API}/stops/export/xlsx", headers=headers, timeout=10)
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text[:300]}"
    assert r.headers.get("content-type", "").startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    ), f"Unexpected content-type: {r.headers.get('content-type')}"
    # A minimal XLSX (header row + 3 data rows + footer) is well over 4 KB.
    assert len(r.content) > 4_000, f"XLSX suspiciously small: {len(r.content)} bytes"


def test_export_xlsx_empty_route(backend_alive):
    """Export with zero stops must return 200 + a valid (but small) XLSX —
    not a 500 or empty body."""
    client = pymongo.MongoClient("mongodb://localhost:27017")
    db = client["test_database"]
    uid = f"xlsx_empty_{uuid.uuid4().hex[:8]}"
    tok = f"sess_{uuid.uuid4().hex}"
    db.users.insert_one({"user_id": uid, "email": f"{uid}@x.com", "name": "E"})
    db.user_sessions.insert_one({
        "session_token": tok, "user_id": uid,
        "expires_at": datetime.datetime.utcnow() + datetime.timedelta(hours=1),
    })
    try:
        headers = {"Authorization": f"Bearer {tok}"}
        r = requests.get(f"{API}/stops/export/xlsx", headers=headers, timeout=10)
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        # Even a header-only XLSX is a few KB.
        assert len(r.content) > 1_000, "Empty-route XLSX is suspiciously small"
    finally:
        db.users.delete_many({"user_id": uid})
        db.user_sessions.delete_many({"user_id": uid})
