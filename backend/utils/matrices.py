"""Distance and duration matrix builders extracted from server.py.

All Mapbox/OSRM matrix helpers, the TTLCache class, cache instances, and the
OSRM circuit-breaker live here. server.py re-exports every public name so
call sites (routes/*, tests) that do `from server import X` keep working.

Config vars (OSRM_URL, MAPBOX_TOKEN, OSRM_PUBLIC_URL, OSRM_URL_PROD) are read
from `server` at *call time* via deferred imports — never at module load — to
avoid circular imports and to pick up the startup-promoted OSRM_URL value.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time as _time
from collections import OrderedDict
from typing import Any, Dict, List, Optional

import httpx
from haversine import Unit, haversine

logger = logging.getLogger("server")


# ──────────────────────────────────────────────────────────────────────────────
# In-memory cache
# ──────────────────────────────────────────────────────────────────────────────

class TTLCache:
    """In-memory LRU cache with TTL eviction and hit/miss counters"""
    def __init__(self, maxsize=200, ttl=30):
        self._cache: OrderedDict = OrderedDict()
        self._maxsize = maxsize
        self._ttl = ttl
        self.hits = 0
        self.misses = 0

    def get(self, key: str):
        if key in self._cache:
            val, ts = self._cache[key]
            if _time.monotonic() - ts < self._ttl:
                self._cache.move_to_end(key)
                self.hits += 1
                return val
            del self._cache[key]
        self.misses += 1
        return None

    def set(self, key: str, value):
        if key in self._cache:
            del self._cache[key]
        self._cache[key] = (value, _time.monotonic())
        if len(self._cache) > self._maxsize:
            self._cache.popitem(last=False)

    def stats(self) -> dict:
        total = self.hits + self.misses
        return {
            "entries": len(self._cache),
            "maxsize": self._maxsize,
            "ttl_seconds": self._ttl,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / total * 100, 1) if total > 0 else 0,
        }


# Directions cache — avoids redundant Mapbox calls on repeated GPS ticks
_directions_cache = TTLCache(maxsize=200, ttl=30)

# OSRM duration matrix cache — TTL=600s (10 min), max 50 route matrices
_osrm_matrix_cache = TTLCache(maxsize=50, ttl=600)

# Separate cache for OSRM distance matrices (km)
_osrm_distance_cache = TTLCache(maxsize=50, ttl=600)


# ──────────────────────────────────────────────────────────────────────────────
# OSRM circuit breaker
# ──────────────────────────────────────────────────────────────────────────────

_osrm_consecutive_failures = 0
_osrm_suppress_until = 0.0
_OSRM_FAIL_THRESHOLD = 3
_OSRM_SUPPRESS_SECONDS = 300


def _osrm_enabled() -> bool:
    """True when OSRM should be attempted on this request."""
    from server import OSRM_URL  # noqa: WPS433
    if not OSRM_URL:
        return False
    if _osrm_consecutive_failures >= _OSRM_FAIL_THRESHOLD and _time.time() < _osrm_suppress_until:
        return False
    return True


def _osrm_log_failure(context: str, exc) -> None:
    """Log an OSRM failure once; after threshold is reached, suppress for a window."""
    global _osrm_consecutive_failures, _osrm_suppress_until
    now = _time.time()
    _osrm_consecutive_failures += 1
    if now < _osrm_suppress_until:
        return
    logger.warning("%s: %s", context, exc)
    if _osrm_consecutive_failures >= _OSRM_FAIL_THRESHOLD:
        _osrm_suppress_until = now + _OSRM_SUPPRESS_SECONDS
        logger.warning(
            "OSRM unreachable (%d consecutive failures). Suppressing OSRM attempts for %ds; falling back to Mapbox.",
            _osrm_consecutive_failures, _OSRM_SUPPRESS_SECONDS,
        )


def _osrm_note_success() -> None:
    """Reset the circuit breaker after a successful OSRM response."""
    global _osrm_consecutive_failures, _osrm_suppress_until
    if _osrm_consecutive_failures:
        _osrm_consecutive_failures = 0
        _osrm_suppress_until = 0.0


# ──────────────────────────────────────────────────────────────────────────────
# Matrix builders
# ──────────────────────────────────────────────────────────────────────────────

async def calculate_road_distance_km(stops: List[dict]) -> Optional[float]:
    """Calculate total road distance via OSRM Route API (primary) or Mapbox (fallback)."""
    from server import MAPBOX_TOKEN, OSRM_URL  # noqa: WPS433
    if len(stops) < 2:
        return None

    if _osrm_enabled():
        try:
            coord_list = [f"{s['longitude']},{s['latitude']}" for s in stops]
            coords = ";".join(coord_list)
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(
                    f"{OSRM_URL}/route/v1/driving/{coords}",
                    params={"overview": "false"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("code") == "Ok" and data.get("routes"):
                        _osrm_note_success()
                        total_meters = data["routes"][0].get("distance", 0)
                        return round(total_meters / 1000, 2)
        except Exception as e:
            logger.warning("OSRM road distance calculation failed: %s", e)

    if not MAPBOX_TOKEN:
        return None
    try:
        coord_list = [f"{s['longitude']},{s['latitude']}" for s in stops]
        MAX_WP = 25
        total_meters = 0.0
        async with httpx.AsyncClient(timeout=15.0) as client:
            for i in range(0, len(coord_list), MAX_WP - 1):
                chunk = coord_list[i:i + MAX_WP]
                if len(chunk) < 2:
                    break
                resp = await client.get(
                    f"https://api.mapbox.com/directions/v5/mapbox/driving/{';'.join(chunk)}",
                    params={"access_token": MAPBOX_TOKEN, "overview": "false"},
                )
                if resp.status_code != 200:
                    return None
                data = resp.json()
                routes = data.get("routes", [])
                if not routes:
                    return None
                total_meters += routes[0].get("distance", 0)
        return round(total_meters / 1000, 2)
    except Exception as e:
        logger.warning("Road distance calculation failed: %s", e)
        return None


def calculate_distance_matrix(stops: List[dict]) -> List[List[float]]:
    """Calculate distance matrix between all stops using haversine"""
    n = len(stops)
    matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i != j:
                coord1 = (stops[i]["latitude"], stops[i]["longitude"])
                coord2 = (stops[j]["latitude"], stops[j]["longitude"])
                matrix[i][j] = haversine(coord1, coord2, unit=Unit.KILOMETERS)
    return matrix


async def _mapbox_matrix_batch(stops: List[dict]) -> Optional[List[List[float]]]:
    """Call Mapbox Matrix API for a batch of up to 25 stops.
    Returns distance matrix in km, or None on failure."""
    from server import MAPBOX_TOKEN  # noqa: WPS433
    if not stops or len(stops) > 25 or not MAPBOX_TOKEN:
        return None

    coords = ";".join(f"{s['longitude']},{s['latitude']}" for s in stops)
    url = f"https://api.mapbox.com/directions-matrix/v1/mapbox/driving/{coords}"
    params = {
        "access_token": MAPBOX_TOKEN,
        "annotations": "distance,duration",
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, timeout=30)

        if response.status_code != 200:
            logger.warning("Mapbox Matrix API returned %s", response.status_code)
            return None

        data = response.json()
        if data.get("code") != "Ok":
            logger.warning("Mapbox Matrix API error: %s", data.get("code"))
            return None

        distances = data.get("distances")
        if not distances:
            return None

        n = len(stops)
        matrix = [[0.0] * n for _ in range(n)]
        for i in range(n):
            for j in range(n):
                if i != j and distances[i][j] is not None:
                    matrix[i][j] = distances[i][j] / 1000.0
                elif i != j:
                    c1 = (stops[i]["latitude"], stops[i]["longitude"])
                    c2 = (stops[j]["latitude"], stops[j]["longitude"])
                    matrix[i][j] = haversine(c1, c2, unit=Unit.KILOMETERS)
        return matrix
    except Exception as exc:
        logger.warning("Mapbox Matrix API call failed: %s", exc)
        return None


async def _mapbox_duration_matrix_batch(stops: List[dict]) -> Optional[List[List[int]]]:
    """Call Mapbox Matrix API for up to 25 stops.
    Returns DURATION matrix in integer seconds, or None on failure."""
    from server import MAPBOX_TOKEN  # noqa: WPS433
    if not stops or len(stops) > 25 or not MAPBOX_TOKEN:
        return None

    coords = ";".join(f"{s['longitude']},{s['latitude']}" for s in stops)
    url = f"https://api.mapbox.com/directions-matrix/v1/mapbox/driving/{coords}"
    params = {
        "access_token": MAPBOX_TOKEN,
        "annotations": "duration",
    }

    try:
        async with httpx.AsyncClient() as client_http:
            response = await client_http.get(url, params=params, timeout=30)

        if response.status_code != 200:
            logger.warning("Mapbox Duration Matrix API returned %s", response.status_code)
            return None

        data = response.json()
        if data.get("code") != "Ok":
            logger.warning("Mapbox Duration Matrix API error: %s", data.get("code"))
            return None

        durations = data.get("durations")
        if not durations:
            return None

        n = len(stops)
        matrix = [[0] * n for _ in range(n)]
        for i in range(n):
            for j in range(n):
                if i != j and durations[i][j] is not None:
                    matrix[i][j] = max(1, int(durations[i][j]))
                elif i != j:
                    c1 = (stops[i]["latitude"], stops[i]["longitude"])
                    c2 = (stops[j]["latitude"], stops[j]["longitude"])
                    km = haversine(c1, c2, unit=Unit.KILOMETERS)
                    matrix[i][j] = max(1, int(km / 30.0 * 3600))
        return matrix
    except Exception as exc:
        logger.warning("Mapbox Duration Matrix API call failed: %s", exc)
        return None


def _haversine_duration_matrix(stops: List[dict]) -> List[List[int]]:
    """Fallback: estimate travel-time matrix (seconds) from haversine at 30 km/h."""
    n = len(stops)
    matrix = [[0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i != j:
                km = haversine(
                    (stops[i]["latitude"], stops[i]["longitude"]),
                    (stops[j]["latitude"], stops[j]["longitude"]),
                    unit=Unit.KILOMETERS,
                )
                matrix[i][j] = max(1, int(km / 30.0 * 3600))
    return matrix


def _osrm_cache_key(stops: List[dict]) -> str:
    """Generate a deterministic, ORDER-INDEPENDENT cache key from stop coordinates."""
    sorted_coords = sorted(
        (round(s['latitude'], 6), round(s['longitude'], 6)) for s in stops
    )
    coord_str = "|".join(f"{lat},{lng}" for lat, lng in sorted_coords)
    return hashlib.sha256(coord_str.encode()).hexdigest()[:16]


def detect_cluster_spikes(
    stops: List[dict],
    spike_ratio: float = 0.5,
    min_detour_km: float = 0.10,
) -> List[Dict[str, Any]]:
    """Flag visual "spike" triplets in an already-optimised stop sequence.

    For each consecutive (A, B, C) we compute haversine distances and ask:
    is the *straight-line* A→C distance much smaller than the detour
    A→B→C? If so, B sits well off the natural A→C line and the route will
    look like a zig-zag on the map even when the OSRM time-matrix says
    visiting B in the middle is optimal (e.g. one-way pair, highway split,
    cul-de-sac inside a cluster).

    Returns a list of warning dicts the frontend can render as
    "tighten cluster?" hints — empty list when the route is clean. The
    optimised order itself is NEVER mutated by this helper.
    """
    warnings: List[Dict[str, Any]] = []
    n = len(stops)
    if n < 3 or spike_ratio <= 0:
        return warnings

    for i in range(1, n - 1):
        a, b, c = stops[i - 1], stops[i], stops[i + 1]
        try:
            ac = haversine(
                (a["latitude"], a["longitude"]),
                (c["latitude"], c["longitude"]),
                unit=Unit.KILOMETERS,
            )
            ab = haversine(
                (a["latitude"], a["longitude"]),
                (b["latitude"], b["longitude"]),
                unit=Unit.KILOMETERS,
            )
            bc = haversine(
                (b["latitude"], b["longitude"]),
                (c["latitude"], c["longitude"]),
                unit=Unit.KILOMETERS,
            )
        except (KeyError, TypeError):
            continue
        detour = ab + bc
        if detour < min_detour_km:
            continue
        ratio = ac / detour if detour > 0 else 1.0
        if ratio < spike_ratio:
            warnings.append({
                "position": i,
                "prev_id": a.get("id"),
                "suspect_id": b.get("id"),
                "next_id": c.get("id"),
                "straight_km": round(ac, 3),
                "detour_km": round(detour, 3),
                "ratio": round(ratio, 3),
                "extra_km": round(detour - ac, 3),
            })
    return warnings


async def _osrm_duration_matrix(stops: List[dict]) -> Optional[List[List[int]]]:
    """Fetch full NxN duration matrix from OSRM Table service.

    Tries the locally-configured OSRM first, then falls back to the public
    OSRM demo server if the local one is unreachable (circuit breaker open).
    """
    from server import OSRM_PUBLIC_URL, OSRM_URL, OSRM_URL_PROD  # noqa: WPS433
    n = len(stops)
    if n < 2:
        return None

    cache_key = _osrm_cache_key(stops)
    cached = _osrm_matrix_cache.get(cache_key)
    if cached is not None:
        logger.info("OSRM matrix CACHE HIT (%d stops, key=%s)", n, cache_key)
        return cached

    candidates: List[tuple[str, str]] = []
    _seen_urls: set[str] = set()

    def _add_candidate(label: str, url: str) -> None:
        if url and url not in _seen_urls:
            candidates.append((label, url))
            _seen_urls.add(url)

    if _osrm_enabled() and OSRM_URL.startswith(('http://localhost', 'http://127.', 'http://[::1]')):
        _add_candidate("local", OSRM_URL)
    if _osrm_enabled() and OSRM_URL != OSRM_PUBLIC_URL:
        _add_candidate("primary", OSRM_URL)
    _add_candidate("prod", OSRM_URL_PROD)
    _add_candidate("public", OSRM_PUBLIC_URL)

    for label, base_url in candidates:
        matrix = await _osrm_duration_matrix_for_url(stops, base_url, label)
        if matrix is not None:
            logger.info(
                "OSRM matrix RESOLVED via [%s] %s for %d stops",
                label, base_url, n,
            )
            _osrm_matrix_cache.set(cache_key, matrix)
            return matrix
    logger.warning(
        "OSRM matrix UNRESOLVED for %d stops after trying %d candidate(s): %s "
        "— caller will fall back to Mapbox/haversine (degraded clustering)",
        n, len(candidates), [c[0] for c in candidates],
    )
    return None


async def _osrm_duration_matrix_for_url(
    stops: List[dict], base_url: str, label: str
) -> Optional[List[List[int]]]:
    """Single-URL variant of the OSRM duration matrix fetch.

    Returns the N×N matrix on success, None on failure so the caller can try
    the next candidate URL.
    """
    n = len(stops)
    OSRM_BATCH = 100

    _is_loopback = base_url.startswith(('http://localhost', 'http://127.', 'http://[::1]'))
    _connect_timeout = 2.0 if _is_loopback else 10.0
    OSRM_TIMEOUT = httpx.Timeout(connect=_connect_timeout, read=45.0, write=10.0, pool=5.0)

    try:
        async with httpx.AsyncClient(timeout=OSRM_TIMEOUT) as client:
            coords = ";".join(f"{s['longitude']},{s['latitude']}" for s in stops)
            resp = await client.get(
                f"{base_url}/table/v1/driving/{coords}",
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == "Ok" and data.get("durations"):
                    logger.info("OSRM[%s] duration matrix: full %dx%d in single call", label, n, n)
                    if label == "local":
                        _osrm_note_success()
                    return [
                        [max(1, int(round(d))) if d is not None else 9999 for d in row]
                        for row in data["durations"]
                    ]

            if n <= OSRM_BATCH:
                return None

            HALF = 40
            batches = [list(range(i, min(i + HALF, n))) for i in range(0, n, HALF)]

            matrix = _haversine_duration_matrix(stops)

            sem = asyncio.Semaphore(1)

            async def _fetch_cross(src_ids, dst_ids):
                async with sem:
                    all_ids = list(src_ids) + [i for i in dst_ids if i not in set(src_ids)]
                    if len(all_ids) > OSRM_BATCH:
                        return None
                    idx_map = {gid: loc for loc, gid in enumerate(all_ids)}
                    coords = ";".join(f"{stops[i]['longitude']},{stops[i]['latitude']}" for i in all_ids)
                    src_local = ";".join(str(idx_map[i]) for i in src_ids)
                    dst_local = ";".join(str(idx_map[i]) for i in dst_ids)

                    for attempt in range(3):
                        resp = await client.get(
                            f"{base_url}/table/v1/driving/{coords}",
                            params={"sources": src_local, "destinations": dst_local},
                            timeout=30,
                        )
                        if resp.status_code == 429:
                            await asyncio.sleep(1.0 * (attempt + 1))
                            continue
                        if resp.status_code != 200:
                            return None
                        data = resp.json()
                        if data.get("code") != "Ok" or not data.get("durations"):
                            return None
                        return (data["durations"], src_ids, dst_ids)
                    return None

            tasks = [_fetch_cross(sb, db) for sb in batches for db in batches]
            results = await asyncio.gather(*tasks)

            upgraded = 0
            for result in results:
                if result is None:
                    continue
                sub, src_ids, dst_ids = result
                for i, gi in enumerate(src_ids):
                    for j, gj in enumerate(dst_ids):
                        val = sub[i][j]
                        if val is not None and gi != gj:
                            matrix[gi][gj] = max(1, int(round(val)))
                            upgraded += 1

            total_cells = n * (n - 1)
            if upgraded < int(total_cells * 0.7):
                logger.warning(
                    "OSRM[%s] matrix only %d/%d cells upgraded (%.0f%%) — rejecting, will try next candidate",
                    label, upgraded, total_cells, 100.0 * upgraded / max(1, total_cells),
                )
                return None

            logger.info(
                "OSRM[%s] duration matrix: %d/%d cells upgraded (%d batches)",
                label, upgraded, total_cells, len(tasks),
            )
            if label == "local":
                _osrm_note_success()
            return matrix

    except Exception as exc:
        _osrm_log_failure(f"OSRM[{label}] duration matrix failed", exc)
        return None


async def _osrm_distance_matrix(stops: List[dict]) -> Optional[List[List[float]]]:
    """Fetch full NxN distance matrix (km) from OSRM Table service.

    Uses annotations=distance to get road distances instead of durations.
    Cached with 10-min TTL. Returns matrix of floats in km, or None on failure.
    """
    from server import OSRM_URL  # noqa: WPS433
    n = len(stops)
    if n < 2 or not _osrm_enabled():
        return None

    cache_key = "dist_" + _osrm_cache_key(stops)
    cached = _osrm_distance_cache.get(cache_key)
    if cached is not None:
        logger.info("OSRM distance matrix CACHE HIT (%d stops)", n)
        return cached

    OSRM_BATCH = 100

    try:
        coords = ";".join(f"{s['longitude']},{s['latitude']}" for s in stops)

        if n <= OSRM_BATCH:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.get(
                    f"{OSRM_URL}/table/v1/driving/{coords}",
                    params={"annotations": "distance"},
                )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == "Ok" and data.get("distances"):
                    logger.info("OSRM distance matrix: full %dx%d in single call", n, n)
                    matrix = [
                        [round(d / 1000.0, 4) if d is not None else 999.0 for d in row]
                        for row in data["distances"]
                    ]
                    _osrm_distance_cache.set(cache_key, matrix)
                    return matrix

        matrix = [[0.0] * n for _ in range(n)]
        for i in range(n):
            for j in range(n):
                if i != j:
                    c1 = (stops[i]["latitude"], stops[i]["longitude"])
                    c2 = (stops[j]["latitude"], stops[j]["longitude"])
                    matrix[i][j] = haversine(c1, c2, unit=Unit.KILOMETERS)

        batch_stops_list = []
        for start in range(0, n, OSRM_BATCH):
            end = min(start + OSRM_BATCH, n)
            batch_stops_list.append((start, end))

        async def fetch_distance_batch(s, e):
            sub_coords = ";".join(f"{stops[i]['longitude']},{stops[i]['latitude']}" for i in range(s, e))
            async with httpx.AsyncClient(timeout=60.0) as client:
                r = await client.get(
                    f"{OSRM_URL}/table/v1/driving/{sub_coords}",
                    params={"annotations": "distance"},
                )
            if r.status_code == 200:
                d = r.json()
                if d.get("code") == "Ok" and d.get("distances"):
                    return (s, e, d["distances"])
            return None

        tasks = [fetch_distance_batch(s, e) for s, e in batch_stops_list]
        results = await asyncio.gather(*tasks)

        upgraded = 0
        for result in results:
            if result is None:
                continue
            s, e, distances = result
            for li, gi in enumerate(range(s, e)):
                for lj, gj in enumerate(range(s, e)):
                    val = distances[li][lj]
                    if val is not None and gi != gj:
                        matrix[gi][gj] = round(val / 1000.0, 4)
                        upgraded += 1

        logger.info("OSRM distance matrix: %d/%d cells upgraded (%d batches)", upgraded, n * (n - 1), len(tasks))
        _osrm_distance_cache.set(cache_key, matrix)
        return matrix

    except Exception as exc:
        logger.warning("OSRM distance matrix failed: %s", exc)
        return None


async def calculate_duration_matrix(stops: List[dict]) -> List[List[int]]:
    """Build NxN driving-duration matrix (integer seconds) using Mapbox.

    Used as FALLBACK when OSRM is unavailable.
    - N <= 25: single Mapbox Matrix API call.
    - N > 25: haversine estimate (use OSRM for larger routes).
    """
    from server import MAPBOX_TOKEN  # noqa: WPS433
    n = len(stops)
    fallback = _haversine_duration_matrix(stops)

    if n <= 1 or not MAPBOX_TOKEN:
        return fallback

    try:
        if n <= 25:
            dur = await _mapbox_duration_matrix_batch(stops)
            if dur:
                logger.info("Duration matrix: full %dx%d from Mapbox", n, n)
                return dur
        logger.info("Duration matrix: %dx%d haversine estimate (Mapbox limit exceeded)", n, n)
        return fallback

    except Exception as exc:
        logger.warning("Duration matrix build failed, using haversine estimate: %s", exc)
        return fallback


async def calculate_road_distance_matrix(stops: List[dict]) -> List[List[float]]:
    """Build distance matrix using OSRM road distances (primary) or Mapbox (fallback)."""
    from server import MAPBOX_TOKEN  # noqa: WPS433
    n = len(stops)
    haversine_matrix = calculate_distance_matrix(stops)

    if n <= 1:
        return haversine_matrix

    osrm_dist = await _osrm_distance_matrix(stops)
    if osrm_dist:
        logger.info("Road distance matrix: full %dx%d from OSRM", n, n)
        return osrm_dist

    if not MAPBOX_TOKEN:
        return haversine_matrix

    try:
        if n <= 25:
            road = await _mapbox_matrix_batch(stops)
            if road:
                logger.info("Road distance matrix: full %dx%d from Mapbox", n, n)
                return road
            return haversine_matrix

        CLUSTER_SIZE = 25
        sorted_indices = sorted(range(n), key=lambda i: (
            round(stops[i]['latitude'] * 100),
            stops[i]['longitude'],
        ))

        clusters = []
        for i in range(0, n, CLUSTER_SIZE):
            clusters.append(sorted_indices[i:i + CLUSTER_SIZE])

        matrix = [row[:] for row in haversine_matrix]

        upgraded = 0
        for cluster_indices in clusters:
            if len(cluster_indices) < 2:
                continue
            cluster_stops = [stops[i] for i in cluster_indices]
            road_sub = await _mapbox_matrix_batch(cluster_stops)
            if road_sub:
                for ci, gi in enumerate(cluster_indices):
                    for cj, gj in enumerate(cluster_indices):
                        matrix[gi][gj] = road_sub[ci][cj]
                upgraded += len(cluster_indices)

        logger.info(
            "Road distance matrix: %d/%d stops upgraded to Mapbox road distances (%d clusters)",
            upgraded, n, len(clusters),
        )
        return matrix

    except Exception as exc:
        logger.warning("Road distance matrix build failed, using haversine: %s", exc)
        return haversine_matrix


async def _mapbox_cross_batch_query(
    client: httpx.AsyncClient,
    stops: List[dict],
    src_global: List[int],
    dst_global: List[int],
    sem: asyncio.Semaphore,
) -> Optional[tuple]:
    """Single Mapbox Matrix API call for a (source_batch, dest_batch) pair."""
    from server import MAPBOX_TOKEN  # noqa: WPS433
    async with sem:
        combined_global = list(src_global)
        dst_only = [i for i in dst_global if i not in set(src_global)]
        combined_global.extend(dst_only)

        if len(combined_global) > 25:
            return None

        global_to_local = {gi: li for li, gi in enumerate(combined_global)}
        local_src = [global_to_local[gi] for gi in src_global]
        local_dst = [global_to_local[gi] for gi in dst_global]

        coords = ";".join(
            f"{stops[gi]['longitude']},{stops[gi]['latitude']}"
            for gi in combined_global
        )
        url = f"https://api.mapbox.com/directions-matrix/v1/mapbox/driving/{coords}"
        params = {
            "access_token": MAPBOX_TOKEN,
            "annotations": "distance",
            "sources": ";".join(str(i) for i in local_src),
            "destinations": ";".join(str(i) for i in local_dst),
        }

        try:
            resp = await client.get(url, params=params, timeout=30)
            if resp.status_code != 200:
                return None
            data = resp.json()
            if data.get("code") != "Ok":
                return None
            distances = data.get("distances")
            if not distances:
                return None

            sub = []
            for row in distances:
                sub.append([
                    round(d / 1000.0, 4) if d is not None else None
                    for d in row
                ])
            return (sub, src_global, dst_global)
        except Exception as exc:
            logger.debug("Mapbox cross-batch failed: %s", exc)
            return None


async def calculate_full_road_distance_matrix(stops: List[dict]) -> List[List[float]]:
    """Build FULL NxN road distance matrix using OSRM (primary) or Mapbox cross-batch (fallback)."""
    from server import MAPBOX_TOKEN  # noqa: WPS433
    n = len(stops)
    haversine_matrix = calculate_distance_matrix(stops)

    if n <= 1:
        return haversine_matrix

    osrm_dist = await _osrm_distance_matrix(stops)
    if osrm_dist:
        logger.info("Full road distance matrix: %dx%d from OSRM", n, n)
        return osrm_dist

    if not MAPBOX_TOKEN:
        return haversine_matrix

    if n <= 25:
        road = await _mapbox_matrix_batch(stops)
        if road:
            return road
        return haversine_matrix

    try:
        BATCH_SIZE = 12
        batches = []
        for i in range(0, n, BATCH_SIZE):
            batches.append(list(range(i, min(i + BATCH_SIZE, n))))

        matrix = [row[:] for row in haversine_matrix]

        sem = asyncio.Semaphore(10)
        async with httpx.AsyncClient() as client:
            tasks = [
                _mapbox_cross_batch_query(client, stops, src_batch, dst_batch, sem)
                for src_batch in batches
                for dst_batch in batches
            ]
            results = await asyncio.gather(*tasks)

        upgraded = 0
        for result in results:
            if result is None:
                continue
            sub, src_global, dst_global = result
            for i, gi in enumerate(src_global):
                for j, gj in enumerate(dst_global):
                    if gi != gj and sub[i][j] is not None:
                        matrix[gi][gj] = sub[i][j]
                        upgraded += 1

        total_cells = n * (n - 1)
        logger.info(
            "Full road matrix: %d/%d cells upgraded to Mapbox road distances (%d API calls)",
            upgraded, total_cells, len(tasks),
        )
        return matrix

    except Exception as exc:
        logger.warning("Full road matrix build failed, using haversine: %s", exc)
        return haversine_matrix
