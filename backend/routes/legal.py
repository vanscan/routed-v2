"""Public legal pages (NO /api prefix — user-facing URLs that Play Console /
users open in their browser, not API endpoints).

    GET /privacy, /privacy-policy, /privacy-policy.html → privacy policy HTML
    GET /terms                                          → terms (same doc for now)

Why served from the backend: keeps the legal copy on a stable, SSL'd
URL under our control (no separate GitHub Pages / Netlify hosting),
and lets us update the policy by editing one file in this repo.

The HTML lives at /app/frontend/public/privacy-policy.html — the same
file referenced by Play Console and the in-app Privacy & Terms screen.
Read at request time (not startup) so a `git pull` propagates instantly
without a backend restart.

Split out of server.py for maintainability. This router is included on
`app` directly (NOT `api_router`) so the paths stay un-prefixed.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

logger = logging.getLogger("server")
router = APIRouter()

_PRIVACY_POLICY_PATH = (
    Path(__file__).resolve().parent.parent.parent / "frontend" / "public" / "privacy-policy.html"
)


def _read_privacy_policy() -> str:
    """Read the privacy policy HTML. Returns a graceful 503 stub if the
    file is missing (shouldn't happen, but better than a 500 traceback)."""
    try:
        return _PRIVACY_POLICY_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return (
            "<!doctype html><html><body style='font-family:sans-serif;padding:2rem'>"
            "<h1>Privacy Policy</h1>"
            "<p>Temporarily unavailable. Email "
            "<a href='mailto:xmltvg@gmail.com'>xmltvg@gmail.com</a> for a copy.</p>"
            "</body></html>"
        )


@router.get("/privacy", include_in_schema=False)
@router.get("/privacy-policy", include_in_schema=False)
@router.get("/privacy-policy.html", include_in_schema=False)
async def privacy_policy_page():
    """Serve the privacy policy HTML at three URL spellings (Play Console,
    in-app, and human-friendly). Cached for 5 min so the CDN can absorb
    crawler traffic; legal pages rarely change but the file is the
    source of truth so we don't want minutes of stale content."""
    return HTMLResponse(
        content=_read_privacy_policy(),
        headers={
            "Cache-Control": "public, max-age=300, s-maxage=300",
            "X-Robots-Tag": "index, follow",
        },
    )


@router.get("/terms", include_in_schema=False)
async def terms_redirect():
    """Terms summary currently lives inside the privacy policy doc.
    Reserved for a future standalone Terms file."""
    return HTMLResponse(content=_read_privacy_policy())
