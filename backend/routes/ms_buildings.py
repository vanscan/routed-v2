"""
Microsoft Global ML Building Footprints tile proxy.

Fetches building footprints from Microsoft's public dataset
(https://github.com/microsoft/GlobalMLBuildingFootprints) and returns
them as GeoJSON per tile for the native MapLibre map.

The dataset is indexed by Bing Maps quadkey at zoom 9.  This module:
  1. Lazily fetches the dataset-links CSV (tries multiple URL candidates).
  2. For each tile request, resolves the containing Z9 quadkey.
  3. Downloads + decompresses the per-quadkey .geojsonl.gz file.
  4. Clips features to the requested tile bbox.
  5. Caches in-process; disk cache attempted but ignored on read-only FS.
"""
from __future__ import annotations

import asyncio
import gzip
import json
import logging
import math
from typing import Dict, List, Optional

import httpx
from fastapi import APIRouter, Response

from . import _tile_cache as disk_cache

logger = logging.getLogger("server")
router = APIRouter()

# Try the Azure Static Website (z5) endpoint first — it bypasses blob
# container access policies.  Fall back to the direct blob URL.
_CSV_CANDIDATES = [
    "https://minedbuildings.z5.web.core.windows.net/global-buildings/dataset-links.csv",
    "https://minedbuildings.blob.core.windows.net/global-buildings/dataset-links.csv",
]

# If the CSV index cannot be fetched, fall back to constructing URLs
# directly using known Microsoft release-date strings (newest first).
_KNOWN_DATES = ["2023-04-25", "2022-07-12", "2022-09-21"]
_BLOB_BASE = "https://minedbuildings.blob.core.windows.net/global-buildings"
_WEB_BASE = "https://minedbuildings.z5.web.core.windows.net/global-buildings"

_DISK_TTL_S = 30 * 24 * 60 * 60
_EMPTY = b'{"type":"FeatureCollection","features":[]}'
_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Cache-Control": "public, max-age=86400",
}

_qk_to_url: Dict[str, str] = {}
_z9_cache: Dict[str, List[dict]] = {}
_index_loaded = False
_index_lock = asyncio.Lock()
_fetch_locks: Dict[str, asyncio.Lock] = {}


def _tile_to_quadkey(z: int, x: int, y: int) -> str:
    qk: list[str] = []
    for i in range(z, 0, -1):
        digit = 0
        mask = 1 << (i - 1)
        if x & mask:
            digit += 1
        if y & mask:
            digit += 2
        qk.append(str(digit))
    return "".join(qk)


def _tile_bbox(z: int, x: int, y: int):
    n = 2.0 ** z
    lon_min = x / n * 360.0 - 180.0
    lon_max = (x + 1) / n * 360.0 - 180.0
    lat_max = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    lat_min = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    return lon_min, lat_min, lon_max, lat_max


def _in_bbox(feat: dict, lon_min: float, lat_min: float,
             lon_max: float, lat_max: float) -> bool:
    try:
        ring = feat["geometry"]["coordinates"][0]
        lon, lat = ring[0][0], ring[0][1]
        return lon_min <= lon <= lon_max and lat_min <= lat <= lat_max
    except Exception:
        return False


def _polygon_area_m2(coordinates: list) -> float:
    """Shoelace area of the outer ring, projected to metres."""
    if not coordinates:
        return 0.0
    ring = coordinates[0]
    if len(ring) < 3:
        return 0.0
    lat_c = sum(p[1] for p in ring) / len(ring)
    lat_scale = 111_320.0
    lon_scale = 111_320.0 * math.cos(math.radians(lat_c))
    area = 0.0
    n = len(ring)
    for i in range(n):
        j = (i + 1) % n
        xi = ring[i][0] * lon_scale
        yi = ring[i][1] * lat_scale
        xj = ring[j][0] * lon_scale
        yj = ring[j][1] * lat_scale
        area += xi * yj - xj * yi
    return abs(area) / 2.0


def _enrich_height(feat: dict) -> dict:
    """Add ``height_est`` and ``confidence_norm`` properties to a feature.

    ``height_est`` is the best available height in metres:
    - Uses the dataset ``height`` value when it is a positive number.
    - Falls back to a footprint-area heuristic otherwise:
        ≥ 3 000 m²  → 15 m  (large commercial / warehouse)
        ≥ 1 000 m²  → 10 m  (medium commercial / multi-storey)
        ≥  300 m²   →  7 m  (standard residential / small commercial)
        <  300 m²   →  4 m  (small outbuilding / garage)

    ``confidence_norm`` is the dataset confidence clamped to [0, 1];
    features with a missing or -1 confidence get 0.5 (neutral).
    """
    props = feat.get("properties") or {}

    try:
        h = float(props.get("height", -1))
    except (TypeError, ValueError):
        h = -1.0

    try:
        confidence = float(props.get("confidence", -1))
    except (TypeError, ValueError):
        confidence = -1.0

    if h > 0:
        height_est = h
    else:
        try:
            area = _polygon_area_m2(
                feat.get("geometry", {}).get("coordinates", [])
            )
        except Exception:
            area = 0.0

        if area >= 3_000:
            height_est = 15.0
        elif area >= 1_000:
            height_est = 10.0
        elif area >= 300:
            height_est = 7.0
        else:
            height_est = 4.0

    conf_norm = max(0.0, min(1.0, confidence)) if confidence >= 0 else 0.5

    new_props = {**props, "height_est": height_est, "confidence_norm": conf_norm}
    return {**feat, "properties": new_props}


def _build_fallback_urls(qk: str) -> list[str]:
    """Construct candidate Azure URLs directly without the CSV index."""
    urls = []
    for date in _KNOWN_DATES:
        urls.append(f"{_BLOB_BASE}/{date}/Australia/{qk}.geojsonl.gz")
        urls.append(f"{_WEB_BASE}/{date}/Australia/{qk}.geojsonl.gz")
    return urls


async def _load_index() -> None:
    global _index_loaded
    async with _index_lock:
        if _index_loaded:
            return
        logger.info("MS buildings: fetching dataset-links.csv")
        loaded = False
        async with httpx.AsyncClient(timeout=30.0) as client:
            for csv_url in _CSV_CANDIDATES:
                try:
                    resp = await client.get(csv_url, follow_redirects=True)
                    if resp.status_code == 200:
                        count = 0
                        for line in resp.text.splitlines():
                            if not line or line.startswith("Location"):
                                continue
                            parts = line.split(",", 3)
                            if len(parts) < 3:
                                continue
                            location, qk, url = (
                                parts[0].strip(), parts[1].strip(), parts[2].strip()
                            )
                            if "Australia" in location:
                                _qk_to_url[qk] = url
                                count += 1
                        logger.info(
                            "MS buildings: indexed %d AU quadkeys from %s", count, csv_url
                        )
                        loaded = True
                        break
                    else:
                        logger.warning(
                            "MS buildings: CSV %s returned HTTP %s", csv_url, resp.status_code
                        )
                except Exception as exc:
                    logger.warning("MS buildings: CSV %s failed: %s", csv_url, exc)

        if not loaded:
            logger.info(
                "MS buildings: CSV unavailable — will use direct URL construction per quadkey"
            )
        _index_loaded = True


async def _fetch_z9(qk: str) -> Optional[List[dict]]:
    disk_key = f"ms-buildings:z9:{qk}"
    try:
        hit = await disk_cache.get(disk_key, max_age_s=_DISK_TTL_S)
        if hit:
            feats = json.loads(hit[0])
            _z9_cache[qk] = feats
            return feats
    except Exception:
        pass

    # Candidates: CSV-provided URL first, then direct date-based fallbacks
    candidates: list[str] = []
    if qk in _qk_to_url:
        candidates.append(_qk_to_url[qk])
    candidates.extend(_build_fallback_urls(qk))

    if qk not in _fetch_locks:
        _fetch_locks[qk] = asyncio.Lock()
    async with _fetch_locks[qk]:
        if qk in _z9_cache:
            return _z9_cache[qk]

        raw: Optional[bytes] = None
        used_url = ""
        async with httpx.AsyncClient(timeout=40.0) as client:
            for url in candidates:
                try:
                    resp = await client.get(url, follow_redirects=True)
                    if resp.status_code == 200:
                        raw = resp.content
                        used_url = url
                        break
                    logger.debug("MS buildings: %s → HTTP %s", url, resp.status_code)
                except Exception as exc:
                    logger.debug("MS buildings: %s failed: %s", url, exc)

        if raw is None:
            logger.info("MS buildings: no data found for qk9=%s", qk)
            return None

        logger.info("MS buildings: downloading qk9=%s from %s", qk, used_url)
        try:
            raw = gzip.decompress(raw)
        except Exception as exc:
            logger.warning("MS buildings: gzip decompress failed qk=%s: %s", qk, exc)
            return None

        raw_features: List[dict] = []
        for line in raw.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw_features.append(json.loads(line))
            except Exception:
                pass

        features = [_enrich_height(f) for f in raw_features]

        try:
            await disk_cache.put(
                disk_key, json.dumps(features).encode(), "application/json"
            )
        except Exception:
            pass

        _z9_cache[qk] = features
        logger.info("MS buildings: cached %d footprints for qk9=%s", len(features), qk)
        return features


@router.get("/tiles/ms-buildings/{z}/{x}/{y}.json")
async def get_ms_buildings_tile(z: int, x: int, y: int):
    if z < 13:
        return Response(
            content=_EMPTY, media_type="application/json", headers=_HEADERS
        )

    if not _index_loaded:
        await _load_index()

    shift = max(z - 9, 0)
    qk = _tile_to_quadkey(9, x >> shift, y >> shift)

    features = _z9_cache.get(qk)
    if features is None:
        features = await _fetch_z9(qk)

    if not features:
        return Response(
            content=_EMPTY, media_type="application/json", headers=_HEADERS
        )

    lon_min, lat_min, lon_max, lat_max = _tile_bbox(z, x, y)
    clipped = [
        f
        for f in features
        if _in_bbox(f, lon_min, lat_min, lon_max, lat_max)
    ]
    fc = json.dumps({"type": "FeatureCollection", "features": clipped})
    return Response(content=fc.encode(), media_type="application/json", headers=_HEADERS)
