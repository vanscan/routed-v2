"""Van Layout endpoints — per-driver bin-grid configuration.

    GET /van-layout  → the driver's saved grid shape, or a 3×3 default
    PUT /van-layout  → persist a chosen grid shape (idempotent upsert)

The driver picks a grid shape once (2×3 / 3×3 / 3×4); we persist it on their
account and reuse it across every route. Bin coordinates use spreadsheet
notation: rows A, B, C (top→bottom) and columns 1, 2, 3 (left→right), so a
3×3 van's bottom-right bin is C3.

`db` and `get_current_user` are lazy-imported from `server` inside the
handlers — same deferred-import pattern as the other route modules — so the
module loads cleanly before `server.py` finishes initialising. `VanLayout` is
the shared Pydantic model from `models/van_layout.py`. `ALLOWED_VAN_SHAPES`
lives here and is re-exported from `server` for backward compatibility.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request

from models import VanLayout

logger = logging.getLogger("server")
router = APIRouter()

# Allowed shapes are explicitly enumerated to prevent drivers from
# accidentally configuring a 50×50 van layout that would break the UI.
ALLOWED_VAN_SHAPES = {(2, 3), (3, 3), (3, 4)}


async def _current_user(request: Request):
    """Dep wrapper — defers the `server` import until the first request."""
    from server import get_current_user  # noqa: WPS433
    return await get_current_user(request)


@router.get("/van-layout")
async def get_van_layout(request: Request, current_user=Depends(_current_user)):
    """Return the driver's saved van layout, or a 3×3 default."""
    from server import db  # noqa: WPS433
    doc = await db.van_layouts.find_one(
        {"user_id": current_user.user_id}, {"_id": 0, "user_id": 0}
    )
    if not doc:
        return {"rows": 3, "cols": 3, "is_default": True}
    return {"rows": int(doc["rows"]), "cols": int(doc["cols"]), "is_default": False}


@router.put("/van-layout")
async def save_van_layout(
    layout: VanLayout,
    request: Request,
    current_user=Depends(_current_user),
):
    """Persist the driver's chosen grid shape. Idempotent upsert."""
    from server import db  # noqa: WPS433
    if (layout.rows, layout.cols) not in ALLOWED_VAN_SHAPES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported van layout {layout.rows}×{layout.cols}. "
                f"Allowed: {sorted(ALLOWED_VAN_SHAPES)}."
            ),
        )
    await db.van_layouts.update_one(
        {"user_id": current_user.user_id},
        {"$set": {
            "rows": layout.rows,
            "cols": layout.cols,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }},
        upsert=True,
    )
    return {"rows": layout.rows, "cols": layout.cols, "is_default": False}
