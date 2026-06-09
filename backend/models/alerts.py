"""Map-alert domain models — police, speed cameras, hazards, road work."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field


class AlertType:
    """String constants for alert types — NOT a Pydantic model on purpose.

    Used as `AlertType.POLICE` etc. throughout the alert routes for
    type-safety without paying a BaseModel allocation per request. Kept
    in this module so a one-stop-shop import of `models.alerts` gets
    you both the constants and the request/response shapes.
    """
    POLICE = "police"
    SPEED_CAMERA_FIXED = "speed_camera_fixed"
    SPEED_CAMERA_MOBILE = "speed_camera_mobile"
    HAZARD = "hazard"
    ACCIDENT = "accident"
    ROAD_WORK = "road_work"


class MapAlert(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: str  # police, speed_camera_fixed, speed_camera_mobile, hazard, accident, road_work
    latitude: float
    longitude: float
    reported_by: Optional[str] = None  # user_id who reported
    description: Optional[str] = None
    speed_limit: Optional[int] = None  # For speed cameras
    direction: Optional[str] = None  # Direction of travel (e.g., "northbound")
    is_permanent: bool = False  # True for fixed cameras, False for user reports
    confirmations: int = 1  # Number of users who confirmed this alert
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: Optional[datetime] = None  # When the alert expires (for non-permanent)
    last_confirmed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AlertCreate(BaseModel):
    # Constrain to the six known alert types so callers can't pollute the
    # collection with arbitrary strings (the report UI only ever sends these).
    type: Literal[
        "police",
        "speed_camera_fixed",
        "speed_camera_mobile",
        "hazard",
        "accident",
        "road_work",
    ]
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    description: Optional[str] = None
    speed_limit: Optional[int] = None
    direction: Optional[str] = None


class AlertResponse(BaseModel):
    """Public-facing alert shape returned to unauthenticated callers.

    Deliberately excludes identity and lifecycle fields (reported_by,
    created_at, expires_at, last_confirmed_at) to prevent activity
    tracking or deanonymisation of reporting drivers.
    """
    id: str
    type: str
    latitude: float
    longitude: float
    description: Optional[str] = None
    speed_limit: Optional[int] = None
    direction: Optional[str] = None
    is_permanent: bool
    confirmations: int
    distance_meters: Optional[float] = None  # Distance from query location
