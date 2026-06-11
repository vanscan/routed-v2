"""JWT/session/user authentication helpers extracted from server.py.

All Supabase JWT validation, Google ID token verification, session management,
and the `get_current_user` / `get_optional_user` FastAPI dependencies live here.
server.py re-exports every public name so `from server import get_current_user`
in routes/* and tests keeps working unchanged.

`db` is imported from `server` at call time (inside function bodies) to avoid
a circular import — server.py imports this module, so we cannot import server
at module load time. `User`, `UserSession` are imported from `models` directly
since that package has no server dependency.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt as pyjwt
from fastapi import HTTPException, Request
from jwt import PyJWKClient

from google.oauth2 import id_token as google_id_token
from google.auth.transport import requests as google_auth_requests

from models import User, UserSession  # noqa: E402

logger = logging.getLogger("server")

# ──────────────────────────────────────────────────────────────────────────────
# Auth config — read from environment at module load (load_dotenv runs first
# in server.py, before this module is imported).
# ──────────────────────────────────────────────────────────────────────────────

SUPABASE_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")

_SUPABASE_JWKS_URL = f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json" if SUPABASE_URL else None
_supabase_jwks_cache: Optional[dict] = None
_supabase_jwks_cache_time: float = 0
_JWKS_CACHE_TTL = 3600

GOOGLE_WEB_CLIENT_ID = os.environ.get("GOOGLE_WEB_CLIENT_ID", "")
GOOGLE_ANDROID_CLIENT_ID = os.environ.get("GOOGLE_ANDROID_CLIENT_ID", "")
GOOGLE_IOS_CLIENT_ID = os.environ.get("GOOGLE_IOS_CLIENT_ID", "")

_GOOGLE_CLIENT_IDS = [
    cid for cid in [GOOGLE_WEB_CLIENT_ID, GOOGLE_ANDROID_CLIENT_ID, GOOGLE_IOS_CLIENT_ID]
    if cid
]

logger.info(
    "[auth] Google Client IDs loaded: %d IDs configured (WEB=%s, ANDROID=%s, IOS=%s)",
    len(_GOOGLE_CLIENT_IDS),
    'YES' if GOOGLE_WEB_CLIENT_ID else 'NO',
    'YES' if GOOGLE_ANDROID_CLIENT_ID else 'NO',
    'YES' if GOOGLE_IOS_CLIENT_ID else 'NO',
)

DEV_MODE = os.environ.get('DEV_MODE', 'false').lower() in ('true', '1', 'yes')

DEV_USER = User(
    user_id='dev-user-123',
    email='dev@example.com',
    name='Dev User',
    picture=None,
    created_at=datetime.now(timezone.utc)
)


# ──────────────────────────────────────────────────────────────────────────────
# JWKS helpers
# ──────────────────────────────────────────────────────────────────────────────

def _fetch_supabase_jwks() -> Optional[dict]:
    """Fetch and cache Supabase JWKS (public keys for ES256 verification)."""
    global _supabase_jwks_cache, _supabase_jwks_cache_time
    import time
    import urllib.request
    import json

    now = time.time()
    if _supabase_jwks_cache and (now - _supabase_jwks_cache_time) < _JWKS_CACHE_TTL:
        return _supabase_jwks_cache

    if not _SUPABASE_JWKS_URL or not SUPABASE_ANON_KEY:
        logger.warning("[auth] Cannot fetch JWKS: SUPABASE_URL=%s, ANON_KEY=%s",
                       'SET' if SUPABASE_URL else 'NOT SET',
                       'SET' if SUPABASE_ANON_KEY else 'NOT SET')
        return None

    try:
        req = urllib.request.Request(_SUPABASE_JWKS_URL)
        req.add_header('apikey', SUPABASE_ANON_KEY)
        with urllib.request.urlopen(req, timeout=10) as resp:
            _supabase_jwks_cache = json.loads(resp.read().decode())
            _supabase_jwks_cache_time = now
            logger.info("[auth] Supabase JWKS fetched successfully, keys=%d",
                        len(_supabase_jwks_cache.get('keys', [])))
            return _supabase_jwks_cache
    except Exception as e:
        logger.warning("[auth] Failed to fetch Supabase JWKS: %s", e)
        return _supabase_jwks_cache


def _get_supabase_signing_key(kid: str) -> Optional[bytes]:
    """Get the Supabase public key for ES256 verification by key ID."""
    from cryptography.hazmat.backends import default_backend
    import base64

    jwks = _fetch_supabase_jwks()
    if not jwks:
        return None

    for key_data in jwks.get('keys', []):
        if key_data.get('kid') == kid and key_data.get('alg') == 'ES256':
            try:
                x = base64.urlsafe_b64decode(key_data['x'] + '==')
                y = base64.urlsafe_b64decode(key_data['y'] + '==')
                from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePublicNumbers, SECP256R1
                public_numbers = EllipticCurvePublicNumbers(
                    x=int.from_bytes(x, 'big'),
                    y=int.from_bytes(y, 'big'),
                    curve=SECP256R1()
                )
                public_key = public_numbers.public_key(default_backend())
                logger.debug("[auth] Successfully loaded Supabase public key for kid=%s", kid)
                return public_key
            except Exception as e:
                logger.warning("[auth] Failed to parse Supabase public key: %s", e)
                return None

    logger.warning("[auth] Key ID %s not found in Supabase JWKS", kid)
    return None


def _decode_google_id_token(token: str) -> Optional[dict]:
    """Decode and validate a Google ID token (ES256 signed)."""
    try:
        logger.info("[auth] Attempting to verify Google ID token, token_length=%d, token_preview=%s...",
                    len(token), token[:20] if len(token) > 20 else token)

        request = google_auth_requests.Request()

        if _GOOGLE_CLIENT_IDS:
            for client_id in _GOOGLE_CLIENT_IDS:
                try:
                    payload = google_id_token.verify_oauth2_token(
                        token,
                        request,
                        audience=client_id
                    )
                    issuer = payload.get('iss', '')
                    if issuer not in ['accounts.google.com', 'https://accounts.google.com']:
                        logger.warning("[auth] Google ID token has invalid issuer: %s", issuer)
                        continue
                    logger.info("[auth] Google ID token verified successfully with client_id, email=%s, sub=%s",
                                payload.get("email"), payload.get("sub"))
                    return payload
                except ValueError as e:
                    logger.debug("[auth] Google token verification failed for client_id %s: %s",
                                 client_id[:20] + '...', e)
                    continue

        logger.warning("[auth] Google ID token rejected: no configured client ID matched")
        return None

    except Exception as e:
        logger.warning("[auth] Google ID token verification failed: %s", e)
        return None


_supabase_jwk_client: Optional[PyJWKClient] = None


def _get_supabase_jwk_client() -> Optional[PyJWKClient]:
    """Get or create a cached PyJWKClient for Supabase ES256 token verification."""
    global _supabase_jwk_client

    if _supabase_jwk_client is not None:
        return _supabase_jwk_client

    if not SUPABASE_URL:
        logger.warning("[auth] SUPABASE_URL not configured, cannot create JWKS client")
        return None

    try:
        jwks_url = f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json"
        _supabase_jwk_client = PyJWKClient(jwks_url)
        logger.info("[auth] Created Supabase JWKS client for URL: %s", jwks_url)
        return _supabase_jwk_client
    except Exception as e:
        logger.warning("[auth] Failed to create Supabase JWKS client: %s", e)
        return None


def _decode_supabase_jwt(token: str) -> Optional[dict]:
    """Decode and validate a Supabase JWT access token.

    Supports both HS256 (legacy, using SUPABASE_JWT_SECRET) and
    ES256 (newer, using Supabase JWKS public keys).
    """
    if not token:
        return None

    logger.info(
        "[auth] Attempting to decode Supabase JWT, token_length=%d, secret_length=%d, token_preview=%s...",
        len(token), len(SUPABASE_JWT_SECRET), token[:20] if len(token) > 20 else token,
    )

    try:
        header = pyjwt.get_unverified_header(token)
        alg = header.get('alg', 'unknown')
        kid = header.get('kid', 'none')
        logger.debug("[auth] Token header: alg=%s, kid=%s", alg, kid)
    except Exception as e:
        logger.warning("[auth] Failed to read token header: %s", e)
        return None

    if alg == 'ES256':
        jwk_client = _get_supabase_jwk_client()
        if jwk_client:
            try:
                signing_key = jwk_client.get_signing_key_from_jwt(token)
                payload = pyjwt.decode(
                    token,
                    signing_key.key,
                    algorithms=["ES256"],
                    audience="authenticated",
                )
                logger.info("[auth] Supabase ES256 JWT verified successfully, sub=%s, email=%s",
                            payload.get("sub"), payload.get("email"))
                return payload
            except pyjwt.ExpiredSignatureError:
                logger.warning("[auth] Supabase ES256 JWT expired")
                return None
            except pyjwt.InvalidAudienceError:
                logger.warning("[auth] Supabase ES256 JWT invalid audience")
                return None
            except Exception as e:
                logger.warning("[auth] Supabase ES256 JWT verification failed: %s", e)
                return None
        else:
            logger.warning("[auth] No JWKS client available for ES256 verification, SUPABASE_URL=%s",
                           SUPABASE_URL[:30] if SUPABASE_URL else 'NOT SET')
            return None

    if not SUPABASE_JWT_SECRET:
        logger.warning("[auth] SUPABASE_JWT_SECRET not configured for HS256 verification")
        return None

    try:
        payload = pyjwt.decode(
            token,
            SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated",
        )
        logger.info("[auth] Supabase HS256 JWT verified successfully, sub=%s, email=%s",
                    payload.get("sub"), payload.get("email"))
        return payload
    except pyjwt.ExpiredSignatureError:
        logger.warning("[auth] Supabase HS256 JWT expired")
        return None
    except pyjwt.InvalidAudienceError:
        logger.warning("[auth] Supabase HS256 JWT invalid audience")
        return None
    except pyjwt.InvalidTokenError as e:
        logger.warning("[auth] Supabase HS256 JWT invalid: %s", e)
        return None


async def _get_or_create_user_from_supabase(payload: dict) -> Optional[User]:
    """Find or create a MongoDB user record from Supabase JWT or Google ID token payload."""
    from server import db  # noqa: WPS433
    supabase_uid = payload.get("sub")
    email = payload.get("email")

    if not supabase_uid or not email:
        logger.warning("[auth] JWT payload missing sub or email")
        return None

    email = email.lower()

    existing = await db.users.find_one({"email": email}, {"_id": 0})

    if existing:
        if existing.get("supabase_uid") != supabase_uid:
            await db.users.update_one(
                {"email": email},
                {"$set": {"supabase_uid": supabase_uid}},
            )
        return User(**existing)

    from routes.auth import SIGNUPS_DISABLED, ALLOWED_USERS
    if ALLOWED_USERS and email not in ALLOWED_USERS:
        logger.warning("[auth] Bearer-token login rejected: email not in ALLOWED_USERS (%s)", email)
        return None
    if SIGNUPS_DISABLED:
        from routes.waitlist import is_waitlist_approved
        approved = await is_waitlist_approved(db, email)
        if not approved:
            logger.warning("[auth] Bearer-token auto-provision blocked: signups disabled and %s not approved", email)
            return None

    user_metadata = payload.get("user_metadata", {})
    name = (
        user_metadata.get("full_name") or
        user_metadata.get("name") or
        payload.get("name") or
        email.split("@")[0]
    )
    picture = (
        user_metadata.get("avatar_url") or
        user_metadata.get("picture") or
        payload.get("picture")
    )

    user_id = f"user_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)

    issuer = payload.get("iss", "")
    from urllib.parse import urlparse as _urlparse
    _issuer_host = (_urlparse(issuer).hostname or issuer).lower()
    provider = "google" if _issuer_host == "accounts.google.com" else "supabase"

    user_doc = {
        "user_id": user_id,
        "email": email,
        "name": name,
        "picture": picture,
        "supabase_uid": supabase_uid,
        "provider": provider,
        "created_at": now,
    }

    await db.users.insert_one(user_doc)
    logger.info("[auth] Created user from %s: email=%s, user_id=%s", provider, email, user_id)

    return User(**user_doc)


async def get_session_from_request(request: Request) -> Optional[UserSession]:
    """Get session from request — supports both legacy sessions and Supabase JWTs."""
    from server import db  # noqa: WPS433
    auth_header = request.headers.get("Authorization")
    session_token = None

    if auth_header and auth_header.startswith("Bearer "):
        session_token = auth_header.split(" ")[1]

        if session_token and not session_token.startswith(("ses_", "rvw_")):
            payload = _decode_supabase_jwt(session_token)
            if payload:
                request.state.supabase_payload = payload
                supabase_uid = payload.get("sub")
                return UserSession(
                    user_id=f"supabase:{supabase_uid}",
                    session_token=session_token,
                    expires_at=datetime.fromtimestamp(payload.get("exp", 0), tz=timezone.utc),
                    created_at=datetime.fromtimestamp(payload.get("iat", 0), tz=timezone.utc),
                )

            google_payload = _decode_google_id_token(session_token)
            if google_payload:
                request.state.supabase_payload = google_payload
                request.state.is_google_token = True
                google_sub = google_payload.get("sub")
                return UserSession(
                    user_id=f"supabase:{google_sub}",
                    session_token=session_token,
                    expires_at=datetime.fromtimestamp(google_payload.get("exp", 0), tz=timezone.utc),
                    created_at=datetime.fromtimestamp(google_payload.get("iat", 0), tz=timezone.utc),
                )

    if not session_token:
        session_token = request.cookies.get("session_token")

    if not session_token:
        return None

    session = await db.user_sessions.find_one({"session_token": session_token}, {"_id": 0})
    if not session:
        return None

    expires_at = session["expires_at"]
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    if expires_at < now:
        return None

    _SESSION_LIFETIME = timedelta(days=7)
    new_expiry = now + _SESSION_LIFETIME

    if (new_expiry - expires_at) > timedelta(hours=1):
        await db.user_sessions.update_one(
            {"session_token": session_token},
            {"$set": {"expires_at": new_expiry}},
        )
        logger.debug(
            "[session-refresh] Extended session for user=%s, new expiry=%s",
            session.get("user_id"), new_expiry.isoformat(),
        )

    return UserSession(**session)


async def get_current_user(request: Request) -> User:
    """Get current authenticated user from request.

    Supports:
    1. DEV_MODE bypass (for local development)
    2. Supabase JWT (via Authorization: Bearer header)
    3. Legacy session token (via cookie or Authorization header)
    """
    from server import db  # noqa: WPS433
    if DEV_MODE:
        return DEV_USER

    session = await get_session_from_request(request)
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")

    if session.user_id.startswith("supabase:"):
        supabase_payload = getattr(request.state, "supabase_payload", None)
        if not supabase_payload:
            raise HTTPException(status_code=401, detail="Invalid Supabase session")

        user = await _get_or_create_user_from_supabase(supabase_payload)
        if not user:
            raise HTTPException(status_code=401, detail="Failed to resolve Supabase user")

        return user

    user = await db.users.find_one({"user_id": session.user_id}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    return User(**user)


async def get_optional_user(request: Request) -> Optional[User]:
    try:
        return await get_current_user(request)
    except HTTPException:
        return None
