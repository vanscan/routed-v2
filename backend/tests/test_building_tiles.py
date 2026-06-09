"""Unit tests for routes/building_tiles.py — self-hosted GeoJSON tile serving.

`building_tiles.py` only lazy-imports `server` (inside `_resolve_tile_db_path`),
so the module imports cleanly on its own. We exercise the async tile endpoints
directly against a temporary SQLite DB whose schema mirrors the real
`buildings.db` (`tiles(z, x, y, data)` with gzip-compressed GeoJSON), and assert
the empty-FeatureCollection fallbacks for the missing-DB and missing-tile paths.
"""
import asyncio
import gzip
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

import routes.building_tiles as bt  # noqa: E402

_TILE_JSON = b'{"type":"FeatureCollection","features":[{"type":"Feature","properties":{"height":12}}]}'


@pytest.fixture
def tile_db(tmp_path):
    """A temp buildings.db with one known tile at (15, 100, 200)."""
    db_path = tmp_path / "buildings.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE tiles (z INTEGER, x INTEGER, y INTEGER, data BLOB)")
    conn.execute("CREATE TABLE metadata (name TEXT, value TEXT)")
    conn.execute(
        "INSERT INTO tiles VALUES (?, ?, ?, ?)",
        (15, 100, 200, gzip.compress(_TILE_JSON)),
    )
    conn.execute("INSERT INTO metadata VALUES ('format', 'geojson')")
    conn.execute("INSERT INTO metadata VALUES ('maxzoom', '16')")
    conn.commit()
    conn.close()
    return str(db_path)


@pytest.fixture(autouse=True)
def _reset_module_globals():
    """Each test starts with a fresh, un-opened tile-DB connection so one
    test's temp DB never leaks into the next."""
    saved_conn, saved_path = bt._building_tile_db, bt._tile_db_path
    bt._building_tile_db = None
    bt._tile_db_path = None
    yield
    if bt._building_tile_db is not None:
        try:
            bt._building_tile_db.close()
        except Exception:
            pass
    bt._building_tile_db, bt._tile_db_path = saved_conn, saved_path


def _point_to(db_path):
    """Pin the module to a specific DB file without invoking the
    server-dependent path resolver."""
    bt._tile_db_path = db_path
    bt._building_tile_db = None


def test_known_tile_returns_decompressed_geojson(tile_db):
    _point_to(tile_db)
    resp = asyncio.run(bt.get_building_tile(15, 100, 200))
    assert resp.media_type == "application/json"
    assert resp.body == _TILE_JSON
    # CORS + cache headers are required for the map client.
    assert resp.headers["Access-Control-Allow-Origin"] == "*"
    assert "max-age" in resp.headers["Cache-Control"]


def test_missing_tile_returns_empty_feature_collection(tile_db):
    _point_to(tile_db)
    resp = asyncio.run(bt.get_building_tile(15, 999, 999))
    assert resp.body == bt._EMPTY_FC


def test_missing_db_returns_empty_feature_collection(tmp_path):
    _point_to(str(tmp_path / "does_not_exist.db"))
    resp = asyncio.run(bt.get_building_tile(15, 100, 200))
    assert resp.body == bt._EMPTY_FC


def test_metadata_returns_key_values(tile_db):
    _point_to(tile_db)
    meta = asyncio.run(bt.get_building_tile_metadata())
    assert meta == {"format": "geojson", "maxzoom": "16"}


def test_metadata_reports_error_when_db_absent(tmp_path):
    _point_to(str(tmp_path / "nope.db"))
    meta = asyncio.run(bt.get_building_tile_metadata())
    assert "error" in meta


def test_get_tile_db_caches_connection(tile_db):
    _point_to(tile_db)
    first = bt._get_tile_db()
    second = bt._get_tile_db()
    assert first is second  # connection is opened once and reused


def test_resolve_tile_db_path_returns_existing_candidate(tmp_path, monkeypatch):
    """When a candidate file exists, the resolver returns it. ROOT_DIR is
    lazy-imported from `server`, so we stub the module rather than importing
    the heavy real one."""
    import types

    fake_server = types.ModuleType("server")
    fake_server.ROOT_DIR = tmp_path  # ROOT_DIR.parent / 'tiles' / 'buildings.db'
    monkeypatch.setitem(sys.modules, "server", fake_server)

    tiles_dir = tmp_path.parent / "tiles"
    tiles_dir.mkdir(exist_ok=True)
    target = tiles_dir / "buildings.db"
    target.write_bytes(b"")

    assert bt._resolve_tile_db_path() == str(target)


def test_resolve_tile_db_path_falls_back_to_canonical(tmp_path, monkeypatch):
    """With no candidate present, the resolver returns the canonical dev path
    (first candidate) so the warning log points at the expected location."""
    import types

    fake_server = types.ModuleType("server")
    fake_server.ROOT_DIR = tmp_path
    monkeypatch.setitem(sys.modules, "server", fake_server)

    resolved = bt._resolve_tile_db_path()
    assert resolved == str(tmp_path.parent / "tiles" / "buildings.db")
