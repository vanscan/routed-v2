"""
FastAPI router for the Late Freight Zipper.

Resilience directive: every endpoint returns a clean JSON envelope. We never
let an exception bubble to the ASGI layer, because an unhandled 500 at the
edge surfaces as a Cloudflare 520 and the driver's app just sees a dead route.
A controlled 500 with a JSON body is debuggable; a 520 is not.

Matrix: built from the standalone OSRM /table service. If OSRM is unreachable
we fail loud with a typed error rather than silently falling back to
straight-line haversine, because a haversine route in dense suburbs will send
a van the wrong way down a one-way street.
"""

from __future__ import annotations

import os

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from late_freight_zipper import Stop, solve_zipper

router = APIRouter(prefix="/route", tags=["routing"])

# OSRM lives on the same Coolify Docker network. Resolve it by the compose
# *service name* (Coolify does not use Fly-style .internal DNS). Override via
# the OSRM_BASE_URL env var if OSRM is a separate Coolify resource on the
# shared 'coolify' network (then use its container name, e.g.
# http://osrm-<uuid>:5000). Per the backend convention this uses .get() with a
# safe default rather than os.environ[].
OSRM_BASE_URL = os.environ.get("OSRM_BASE_URL", "http://osrm:5000")
OSRM_TABLE_URL = f"{OSRM_BASE_URL.rstrip('/')}/table/v1/driving/"


class StopIn(BaseModel):
    id: str
    lat: float
    lon: float
    original_sequence: int | None = None
    is_depot: bool = False


class ZipperRequest(BaseModel):
    stops: list[StopIn] = Field(..., min_length=2)
    return_to_depot: bool = True
    time_limit_s: int = Field(5, ge=1, le=30)


async def _osrm_matrix(stops: list[StopIn]) -> list[list[int]]:
    """Pull a metres distance matrix from OSRM /table. Coordinates are lon,lat."""
    coords = ";".join(f"{s.lon},{s.lat}" for s in stops)
    url = f"{OSRM_TABLE_URL}{coords}?annotations=distance"
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(url)
        r.raise_for_status()
        data = r.json()
    if data.get("code") != "Ok":
        raise RuntimeError(f"OSRM rejected the table request: {data.get('code')}")
    # OSRM distances come back as floats (metres); OR-Tools needs ints.
    return [[int(round(d or 0)) for d in row] for row in data["distances"]]


@router.post("/zipper")
async def zipper(req: ZipperRequest):
    try:
        stops = [
            Stop(s.id, s.lat, s.lon, s.original_sequence, s.is_depot)
            for s in req.stops
        ]

        try:
            matrix = await _osrm_matrix(req.stops)
        except (httpx.HTTPError, RuntimeError) as exc:
            # OSRM down or malformed -> typed 503, not a silent bad route.
            return JSONResponse(
                status_code=503,
                content={"ok": False, "error": "osrm_unavailable", "detail": str(exc)},
            )

        result = solve_zipper(
            stops,
            matrix=matrix,
            return_to_depot=req.return_to_depot,
            time_limit_s=req.time_limit_s,
        )

        if result.solver_status != "OK":
            return JSONResponse(
                status_code=422,
                content={"ok": False, "error": "no_solution", "detail": result.solver_status},
            )

        return {
            "ok": True,
            "total_distance_m": result.total_distance_m,
            "inserted_labels": result.inserted_labels,
            "route": [
                {
                    "id": p.id,
                    "label": p.label,
                    "lat": p.lat,
                    "lon": p.lon,
                    "original_sequence": p.original_sequence,
                    "is_late_freight": p.is_late_freight,
                }
                for p in result.route
            ],
        }

    except ValueError as exc:
        # Bad input (e.g. Sharpie numbers not ascending, no depot) -> 400.
        return JSONResponse(status_code=400, content={"ok": False, "error": "bad_input", "detail": str(exc)})
    except Exception as exc:  # last line of defence: controlled 500, never a 520
        return JSONResponse(
            status_code=500,
            content={"ok": False, "error": "internal", "detail": type(exc).__name__},
        )
