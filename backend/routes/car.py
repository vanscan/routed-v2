"""Android Auto in-car endpoints.

    POST /car/stop-action   → delivered / skip / failed actions from the car UI
    GET  /car/next-stops    → optimized incomplete-stop feed for car surfaces

Split out of server.py for maintainability. `db` and `get_current_user`
are lazy-imported from `server` inside a thin dependency wrapper — same
pattern as `routes/stops.py` — so this module loads cleanly before
`server.py` has finished initialising.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from models import CarStopActionRequest, Stop

logger = logging.getLogger("server")
router = APIRouter()


async def _current_user(request: Request):
    """Dep wrapper — defers the `server` import until the first request so the
    module can load before `server.py` finishes defining its symbols."""
    from server import get_current_user  # noqa: WPS433
    return await get_current_user(request)


@router.post("/car/stop-action", response_model=Stop)
async def car_stop_action(action_data: CarStopActionRequest, current_user=Depends(_current_user)):
    """Android Auto in-car stop actions: delivered, skip, failed."""
    from server import db  # noqa: WPS433
    existing = await db.stops.find_one({"id": action_data.stop_id, "user_id": current_user.user_id}, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="Stop not found")

    now_utc = datetime.now(timezone.utc)
    update_payload: Dict[str, Any] = {}

    if action_data.action == "delivered":
        update_payload = {
            "completed": True,
            "completed_at": now_utc,
            "delivery_status": "delivered",
            "failure_reason": None,
        }
    elif action_data.action == "skip":
        update_payload = {
            "completed": False,
            "completed_at": None,
            "delivery_status": "skipped",
            "failure_reason": action_data.reason,
        }
    elif action_data.action == "failed":
        update_payload = {
            "completed": False,
            "completed_at": None,
            "delivery_status": "failed",
            "failure_reason": action_data.reason,
        }

    await db.stops.update_one({"id": action_data.stop_id, "user_id": current_user.user_id}, {"$set": update_payload})
    updated = await db.stops.find_one({"id": action_data.stop_id, "user_id": current_user.user_id}, {"_id": 0})
    return Stop(**updated)


@router.get("/car/next-stops", response_model=List[Stop])
async def car_next_stops(current_user=Depends(_current_user), limit: int = Query(default=20, ge=1, le=100)):
    """Optimized stop feed for Android Auto surfaces."""
    from server import db  # noqa: WPS433
    cursor = db.stops.find(
        {"user_id": current_user.user_id, "completed": {"$ne": True}},
        {"_id": 0}
    ).sort("order", 1).limit(limit)
    stops = await cursor.to_list(limit)
    return [Stop(**s) for s in stops]
