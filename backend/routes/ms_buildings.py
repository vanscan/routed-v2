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

        features: List[dict] = []
        for line in raw.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                features.append(json.loads(line))
            except Exception:
                pass

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
    clipped = [f for f in features if _in_bbox(f, lon_min, lat_min, lon_max, lat_max)]
    fc = json.dumps({"type": "FeatureCollection", "features": clipped})
    return Response(content=fc.encode(), media_type="application/json", headers=_HEADERS)
