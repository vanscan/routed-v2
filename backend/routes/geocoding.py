"""Heavy geocoding endpoints for stops.

    POST /stops/{stop_id}/regeocode  → re-geocode one stop's address
    POST /stops/refresh-suburbs      → backfill missing suburbs for all stops

Split out of server.py for maintainability. Geocoding helpers come from
`routes/_geocoding.py`; `db` and `get_current_user` are lazy-imported from
`server` inside a thin dependency wrapper — same pattern as
`routes/stops.py` — so this module loads cleanly before `server.py` has
finished initialising.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from models import RegeocodeStopRequest, RegeocodeStopResponse, Stop
from routes._geocoding import (
    _build_stop_geocode_metadata,
    extract_suburb_from_address,
    geocode_address_async,
    reverse_geocode_suburb,
)

logger = logging.getLogger("server")
router = APIRouter()


async def _current_user(request: Request):
    """Dep wrapper — defers the `server` import until the first request so the
    module can load before `server.py` finishes defining its symbols."""
    from server import get_current_user  # noqa: WPS433
    return await get_current_user(request)


@router.post("/stops/{stop_id}/regeocode", response_model=RegeocodeStopResponse)
async def regeocode_stop(
    stop_id: str,
    payload: Optional[RegeocodeStopRequest] = None,
    current_user=Depends(_current_user),
):
    """Re-geocode a stop's address. Keeps existing coordinates if geocoding fails."""
    from server import db  # noqa: WPS433
    existing = await db.stops.find_one({"id": stop_id, "user_id": current_user.user_id}, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="Stop not found")

    payload_address = payload.address if payload else None
    address_input = payload_address if payload_address is not None else existing.get("address", "")
    clean_address = re.sub(r"\s+", " ", str(address_input).replace("\n", " ").replace("\r", " ")).strip()
    if not clean_address:
        raise HTTPException(status_code=400, detail="Address is required for re-geocoding")

    geo_result = await geocode_address_async(clean_address, user_id=current_user.user_id)

    if not geo_result:
        metadata = dict(existing.get("geocode_metadata") or {})
        metadata["geocode_needs_fix"] = True
        metadata["geocode_status"] = "failed"
        metadata["geocode_issue"] = "Geocoding failed for current address; previous coordinates retained."
        metadata["import_original_address"] = clean_address

        await db.stops.update_one(
            {"id": stop_id, "user_id": current_user.user_id},
            {
                "$set": {
                    "address": clean_address,
                    "geocode_metadata": metadata,
                }
            },
        )

        updated_failed = await db.stops.find_one({"id": stop_id, "user_id": current_user.user_id}, {"_id": 0})
        return RegeocodeStopResponse(
            success=True,
            geocoded=False,
            message="Could not geocode this address. Previous coordinates were kept.",
            stop=Stop(**updated_failed),
        )

    suburb = extract_suburb_from_address(geo_result.get("place_name", clean_address))
    if not suburb:
        suburb = await reverse_geocode_suburb(geo_result["latitude"], geo_result["longitude"])

    metadata = _build_stop_geocode_metadata(geo_result) or {}
    metadata["import_original_address"] = clean_address
    metadata["geocoded_formatted_address"] = geo_result.get("place_name", "")
    metadata["geocode_needs_fix"] = False
    metadata["geocode_status"] = "ok"
    metadata.pop("geocode_issue", None)

    await db.stops.update_one(
        {"id": stop_id, "user_id": current_user.user_id},
        {
            "$set": {
                "address": clean_address,
                "latitude": geo_result["latitude"],
                "longitude": geo_result["longitude"],
                "suburb": suburb,
                "geocode_metadata": metadata,
            }
        },
    )

    updated = await db.stops.find_one({"id": stop_id, "user_id": current_user.user_id}, {"_id": 0})
    return RegeocodeStopResponse(
        success=True,
        geocoded=True,
        message="Address geocoded and stop location updated.",
        stop=Stop(**updated),
    )


@router.post("/stops/refresh-suburbs")
async def refresh_suburbs(current_user=Depends(_current_user)):
    """Refresh/update suburbs for all stops that don't have one"""
    from server import db  # noqa: WPS433
    stops = await db.stops.find({"user_id": current_user.user_id}, {"_id": 0}).to_list(1000)

    updated_count = 0
    for stop in stops:
        # Skip if already has suburb
        if stop.get("suburb"):
            continue

        # Try to extract suburb from address first
        suburb = extract_suburb_from_address(stop.get("address", ""))

        # If not found, try reverse geocoding
        if not suburb and stop.get("latitude") and stop.get("longitude"):
            suburb = await reverse_geocode_suburb(stop["latitude"], stop["longitude"])

        # Update if we found a suburb
        if suburb:
            await db.stops.update_one(
                {"id": stop["id"]},
                {"$set": {"suburb": suburb}}
            )
            updated_count += 1

        # Small delay to avoid rate limiting
        await asyncio.sleep(0.05)

    return {"message": f"Updated suburbs for {updated_count} stops", "updated_count": updated_count}
