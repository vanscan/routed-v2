"""Map Alerts endpoints — community-reported hazards, cameras, police, etc.

    GET    /alerts                  → active alerts within a radius of a point
    POST   /alerts                  → report an alert (dedupes within 100 m)
    POST   /alerts/{id}/confirm     → confirm + extend lifetime
    POST   /alerts/{id}/dismiss     → downvote / remove
    DELETE /alerts/{id}             → reporter-only delete
    GET    /alerts/types            → static metadata for the report UI

Fully self-contained: no shared helpers, no module-level caches. `db` and
`get_current_user` are lazy-imported from `server` inside a thin dependency
wrapper — same pattern as `routes/stops.py` / `routes/auth.py` — so this
module loads cleanly before `server.py` has finished initialising.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from haversine import haversine, Unit

from models.alerts import MapAlert, AlertCreate

logger = logging.getLogger("server")
router = APIRouter()

# Default expiry times for different alert types (in minutes)
ALERT_EXPIRY_MINUTES = {
    "police": 30,
    "speed_camera_mobile": 60,
    "hazard": 120,
    "accident": 180,
    "road_work": 480,
    "speed_camera_fixed": None,  # Permanent
}


async def _current_user(request: Request):
    """Dep wrapper — defers the `server` import until the first request so the
    module can load before `server.py` finishes defining its symbols."""
    from server import get_current_user  # noqa: WPS433
    return await get_current_user(request)


@router.get("/alerts")
async def get_alerts(
    lat: float = Query(..., description="Current latitude"),
    lng: float = Query(..., description="Current longitude"),
    radius_km: float = Query(10, description="Search radius in kilometers"),
    request: Request = None
):
    """Get all active alerts within radius of current location"""
    from server import db  # noqa: WPS433
    try:
        # Get current time for expiry check
        now = datetime.now(timezone.utc)

        # Find alerts that haven't expired
        cursor = db.map_alerts.find({
            "$or": [
                {"expires_at": None},  # Permanent alerts
                {"expires_at": {"$gt": now}}  # Non-expired alerts
            ]
        }, {"_id": 0})

        alerts = []
        async for alert in cursor:
            # Calculate distance
            alert_coords = (alert["latitude"], alert["longitude"])
            user_coords = (lat, lng)
            distance_km = haversine(user_coords, alert_coords, unit=Unit.KILOMETERS)

            if distance_km <= radius_km:
                alert["distance_meters"] = distance_km * 1000
                alerts.append(alert)

        # Sort by distance
        alerts.sort(key=lambda x: x["distance_meters"])

        return alerts
    except Exception as e:
        logger.error(f"Error getting alerts: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/alerts")
async def create_alert(alert_data: AlertCreate, request: Request, current_user=Depends(_current_user)):
    """Report a new alert"""
    from server import db  # noqa: WPS433
    try:
        # Check for duplicate alerts nearby (within 100 meters)
        now = datetime.now(timezone.utc)
        cursor = db.map_alerts.find({
            "type": alert_data.type,
            "$or": [
                {"expires_at": None},
                {"expires_at": {"$gt": now}}
            ]
        }, {"_id": 0})

        async for existing in cursor:
            existing_coords = (existing["latitude"], existing["longitude"])
            new_coords = (alert_data.latitude, alert_data.longitude)
            distance_m = haversine(existing_coords, new_coords, unit=Unit.METERS)

            if distance_m < 100:
                # Update confirmations on existing alert
                await db.map_alerts.update_one(
                    {"id": existing["id"]},
                    {
                        "$inc": {"confirmations": 1},
                        "$set": {"last_confirmed_at": now}
                    }
                )
                existing["confirmations"] += 1
                return existing

        # Create new alert
        is_permanent = alert_data.type == "speed_camera_fixed"
        expiry_minutes = ALERT_EXPIRY_MINUTES.get(alert_data.type, 60)

        alert = MapAlert(
            type=alert_data.type,
            latitude=alert_data.latitude,
            longitude=alert_data.longitude,
            reported_by=current_user.user_id,
            description=alert_data.description,
            speed_limit=alert_data.speed_limit,
            direction=alert_data.direction,
            is_permanent=is_permanent,
            expires_at=None if is_permanent else (now + timedelta(minutes=expiry_minutes))
        )

        await db.map_alerts.insert_one(alert.model_dump())
        logger.info("New alert created: type=%s", alert.type)

        return alert.model_dump()
    except Exception as e:
        logger.error(f"Error creating alert: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/alerts/{alert_id}/confirm")
async def confirm_alert(alert_id: str, request: Request, current_user=Depends(_current_user)):
    """Confirm an alert still exists (extends its lifetime)"""
    from server import db  # noqa: WPS433
    try:
        now = datetime.now(timezone.utc)

        alert = await db.map_alerts.find_one({"id": alert_id}, {"_id": 0})
        if not alert:
            raise HTTPException(status_code=404, detail="Alert not found")

        # Extend expiry time if not permanent
        update_data = {
            "$inc": {"confirmations": 1},
            "$set": {"last_confirmed_at": now}
        }

        if not alert.get("is_permanent"):
            expiry_minutes = ALERT_EXPIRY_MINUTES.get(alert["type"], 60)
            update_data["$set"]["expires_at"] = now + timedelta(minutes=expiry_minutes)

        await db.map_alerts.update_one({"id": alert_id}, update_data)

        # Return updated alert
        updated = await db.map_alerts.find_one({"id": alert_id}, {"_id": 0})
        return updated
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error confirming alert: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/alerts/{alert_id}/dismiss")
async def dismiss_alert(alert_id: str, request: Request, current_user=Depends(_current_user)):
    """Mark an alert as no longer valid"""
    from server import db  # noqa: WPS433
    try:
        alert = await db.map_alerts.find_one({"id": alert_id}, {"_id": 0})
        if not alert:
            raise HTTPException(status_code=404, detail="Alert not found")

        # Decrease confirmations or delete if no confirmations left
        if alert.get("confirmations", 1) <= 1:
            await db.map_alerts.delete_one({"id": alert_id})
            return {"message": "Alert deleted"}
        else:
            await db.map_alerts.update_one(
                {"id": alert_id},
                {"$inc": {"confirmations": -1}}
            )
            updated = await db.map_alerts.find_one({"id": alert_id}, {"_id": 0})
            return updated
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error dismissing alert: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/alerts/{alert_id}")
async def delete_alert(alert_id: str, request: Request):
    """Delete an alert (admin or reporter only)"""
    from server import db  # noqa: WPS433
    try:
        user = await _current_user(request)

        alert = await db.map_alerts.find_one({"id": alert_id}, {"_id": 0})
        if not alert:
            raise HTTPException(status_code=404, detail="Alert not found")

        # Only allow deletion by the reporter
        if alert.get("reported_by") and alert["reported_by"] != user.user_id:
            raise HTTPException(status_code=403, detail="Not authorized to delete this alert")

        await db.map_alerts.delete_one({"id": alert_id})
        return {"message": "Alert deleted"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting alert: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/alerts/types")
async def get_alert_types():
    """Get all available alert types with their metadata"""
    return [
        {"type": "police", "label": "Police", "icon": "shield", "color": "#3b82f6", "expiry_minutes": 30},
        {"type": "speed_camera_fixed", "label": "Fixed Speed Camera", "icon": "camera", "color": "#ef4444", "expiry_minutes": None},
        {"type": "speed_camera_mobile", "label": "Mobile Speed Camera", "icon": "videocam", "color": "#f97316", "expiry_minutes": 60},
        {"type": "hazard", "label": "Hazard", "icon": "warning", "color": "#eab308", "expiry_minutes": 120},
        {"type": "accident", "label": "Accident", "icon": "car", "color": "#dc2626", "expiry_minutes": 180},
        {"type": "road_work", "label": "Road Work", "icon": "construct", "color": "#f59e0b", "expiry_minutes": 480},
    ]
