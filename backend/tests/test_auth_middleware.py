"""Unit tests for the JWT/session authentication helpers in middleware/auth.py.

These tests cover:
  - _decode_supabase_jwt: valid HS256, expired, wrong secret, garbage input
  - get_current_user: DEV_MODE bypass, missing Authorization header,
    valid Bearer token (HS256 Supabase JWT)
  - get_optional_user: never raises, returns None on bad/missing token

Hermetic — no live DB or backend required.  All calls into server.db and
the route-layer helpers are patched at the sys.modules level before the
auth module is imported so no circular-import issues arise.

Usage (from repo root):
    cd /home/user/routed-v2/backend && pytest tests/test_auth_middleware.py -v
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Environment and sys.path setup ───────────────────────────────────────────
# Must happen BEFORE the middleware is imported so module-level os.environ
# reads in auth.py see sensible defaults.
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "routed_test")
os.environ.setdefault("SUPABASE_JWT_SECRET", "")
os.environ.setdefault("SUPABASE_URL", "")
os.environ.setdefault("DEV_MODE", "false")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# ── Stub out heavy server/route modules so we don't pull in the entire stack ─
# middleware/auth.py does `from server import db` inside function bodies, so
# those calls resolve to this mock at call time without triggering a real import.
_db_mock = MagicMock()
_db_mock.user_sessions.find_one = AsyncMock(return_value=None)
_db_mock.users.find_one = AsyncMock(return_value=None)
_db_mock.users.insert_one = AsyncMock(return_value=None)
_db_mock.users.update_one = AsyncMock(return_value=None)

_server_mock = MagicMock()
_server_mock.db = _db_mock

_routes_auth_mock = MagicMock()
_routes_auth_mock.SIGNUPS_DISABLED = False
_routes_auth_mock.ALLOWED_USERS = []

sys.modules.setdefault("server", _server_mock)
sys.modules.setdefault("routes", MagicMock())
sys.modules.setdefault("routes.auth", _routes_auth_mock)
sys.modules.setdefault("routes.waitlist", MagicMock())

# ── Now import the module under test ─────────────────────────────────────────
import middleware.auth as auth_mod  # noqa: E402

import jwt as pyjwt  # noqa: E402

from fastapi import HTTPException  # noqa: E402
from models import User  # noqa: E402
from starlette.requests import Request  # noqa: E402

# ── Constants ─────────────────────────────────────────────────────────────────
_SECRET = "a-super-long-test-secret-for-hmac-sha256-algorithm-32plus"


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_request(headers: dict[str, str] | None = None) -> Request:
    """Build a minimal Starlette Request with the given HTTP headers."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "headers": [
            (k.lower().encode(), v.encode())
            for k, v in (headers or {}).items()
        ],
    }
    return Request(scope)


def _make_hs256_token(
    payload: dict | None = None,
    secret: str = _SECRET,
    exp_offset: timedelta | None = None,
) -> str:
    """Encode a HS256 JWT.  Adds 'aud': 'authenticated' if not present."""
    base = {
        "sub": "user-abc",
        "email": "test@example.com",
        "role": "authenticated",
        "aud": "authenticated",
    }
    if payload:
        base.update(payload)
    if exp_offset is not None:
        base["exp"] = datetime.now(timezone.utc) + exp_offset
    return pyjwt.encode(base, secret, algorithm="HS256")


# ── _decode_supabase_jwt tests ────────────────────────────────────────────────

class TestDecodeSupabaseJwt:
    """Tests for the pure JWT-decoding helper."""

    def setup_method(self):
        # Point the module at our test secret before each test.
        auth_mod.SUPABASE_JWT_SECRET = _SECRET

    def teardown_method(self):
        auth_mod.SUPABASE_JWT_SECRET = ""

    def test_valid_hs256_token_returns_payload(self):
        """A well-formed HS256 JWT signed with the configured secret decodes."""
        token = _make_hs256_token()
        result = auth_mod._decode_supabase_jwt(token)

        assert result is not None
        assert result["sub"] == "user-abc"
        assert result["email"] == "test@example.com"
        assert result["aud"] == "authenticated"

    def test_expired_token_returns_none(self):
        """An expired JWT must return None rather than raise."""
        token = _make_hs256_token(exp_offset=timedelta(hours=-2))
        result = auth_mod._decode_supabase_jwt(token)
        assert result is None

    def test_wrong_secret_returns_none(self):
        """A token signed with a different secret fails verification silently."""
        token = _make_hs256_token(secret="totally-different-secret-that-is-wrong-32")
        # Module uses _SECRET from setup_method — mismatch expected.
        result = auth_mod._decode_supabase_jwt(token)
        assert result is None

    def test_garbage_token_returns_none(self):
        """An unstructured string that is not a valid JWT returns None."""
        result = auth_mod._decode_supabase_jwt("not.a.jwt")
        assert result is None

    def test_empty_string_returns_none(self):
        """Empty input short-circuits before any decoding attempt."""
        result = auth_mod._decode_supabase_jwt("")
        assert result is None

    def test_no_secret_configured_returns_none(self):
        """When SUPABASE_JWT_SECRET is empty the HS256 path bails out."""
        auth_mod.SUPABASE_JWT_SECRET = ""
        token = _make_hs256_token()
        result = auth_mod._decode_supabase_jwt(token)
        assert result is None


# ── get_current_user tests ────────────────────────────────────────────────────

class TestGetCurrentUser:
    """Tests for the FastAPI dependency that resolves the authenticated user."""

    def setup_method(self):
        auth_mod.DEV_MODE = False
        auth_mod.SUPABASE_JWT_SECRET = _SECRET
        # Reset db mocks to predictable state.
        _db_mock.user_sessions.find_one = AsyncMock(return_value=None)
        _db_mock.users.find_one = AsyncMock(return_value=None)

    def teardown_method(self):
        auth_mod.DEV_MODE = False
        auth_mod.SUPABASE_JWT_SECRET = ""

    @pytest.mark.asyncio
    async def test_dev_mode_returns_dev_user(self):
        """When DEV_MODE is True, get_current_user skips auth entirely."""
        auth_mod.DEV_MODE = True
        request = make_request()
        result = await auth_mod.get_current_user(request)

        assert result is auth_mod.DEV_USER
        assert result.email == "dev@example.com"

    @pytest.mark.asyncio
    async def test_missing_authorization_header_raises_401(self):
        """No Authorization header → 401 HTTPException."""
        request = make_request()
        with pytest.raises(HTTPException) as exc_info:
            await auth_mod.get_current_user(request)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_invalid_bearer_token_raises_401(self):
        """A Bearer token that fails JWT decoding and Google verification → 401."""
        # 'invalid.token.here' won't parse as either a Supabase JWT or a Google
        # ID token, so the session falls through to None → 401.
        request = make_request({"Authorization": "Bearer invalid.token.here"})
        with pytest.raises(HTTPException) as exc_info:
            await auth_mod.get_current_user(request)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_valid_supabase_bearer_token_returns_user(self):
        """A valid HS256 Supabase JWT resolves to the expected User object."""
        token = _make_hs256_token(
            {"sub": "supa-uid-999", "email": "alice@example.com"},
            exp_offset=timedelta(hours=1),
        )
        request = make_request({"Authorization": f"Bearer {token}"})

        expected_user = User(
            user_id="resolved-user-123",
            email="alice@example.com",
            name="Alice",
            created_at=datetime.now(timezone.utc),
        )

        with patch.object(
            auth_mod,
            "_get_or_create_user_from_supabase",
            new=AsyncMock(return_value=expected_user),
        ):
            result = await auth_mod.get_current_user(request)

        assert result.email == "alice@example.com"
        assert result.user_id == "resolved-user-123"

    @pytest.mark.asyncio
    async def test_valid_token_but_user_resolution_fails_raises_401(self):
        """If _get_or_create_user_from_supabase returns None, a 401 is raised."""
        token = _make_hs256_token(exp_offset=timedelta(hours=1))
        request = make_request({"Authorization": f"Bearer {token}"})

        with patch.object(
            auth_mod,
            "_get_or_create_user_from_supabase",
            new=AsyncMock(return_value=None),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await auth_mod.get_current_user(request)

        assert exc_info.value.status_code == 401


# ── get_optional_user tests ───────────────────────────────────────────────────

class TestGetOptionalUser:
    """get_optional_user must never raise — it returns None on any auth failure."""

    def setup_method(self):
        auth_mod.DEV_MODE = False
        auth_mod.SUPABASE_JWT_SECRET = _SECRET
        _db_mock.user_sessions.find_one = AsyncMock(return_value=None)
        _db_mock.users.find_one = AsyncMock(return_value=None)

    def teardown_method(self):
        auth_mod.DEV_MODE = False
        auth_mod.SUPABASE_JWT_SECRET = ""

    @pytest.mark.asyncio
    async def test_no_header_returns_none(self):
        """Missing Authorization header → None, no exception."""
        request = make_request()
        result = await auth_mod.get_optional_user(request)
        assert result is None

    @pytest.mark.asyncio
    async def test_garbage_token_returns_none(self):
        """A malformed Bearer token → None, no exception."""
        request = make_request({"Authorization": "Bearer garbage.token.xyz"})
        result = await auth_mod.get_optional_user(request)
        assert result is None

    @pytest.mark.asyncio
    async def test_expired_token_returns_none(self):
        """An expired JWT in the header → None, no exception."""
        token = _make_hs256_token(exp_offset=timedelta(hours=-1))
        request = make_request({"Authorization": f"Bearer {token}"})
        result = await auth_mod.get_optional_user(request)
        assert result is None

    @pytest.mark.asyncio
    async def test_valid_token_returns_user(self):
        """When the token is valid, get_optional_user propagates the resolved User."""
        token = _make_hs256_token(
            {"sub": "supa-uid-opt", "email": "bob@example.com"},
            exp_offset=timedelta(hours=1),
        )
        request = make_request({"Authorization": f"Bearer {token}"})

        expected_user = User(
            user_id="opt-user-456",
            email="bob@example.com",
            name="Bob",
            created_at=datetime.now(timezone.utc),
        )

        with patch.object(
            auth_mod,
            "_get_or_create_user_from_supabase",
            new=AsyncMock(return_value=expected_user),
        ):
            result = await auth_mod.get_optional_user(request)

        assert result is not None
        assert result.email == "bob@example.com"

    @pytest.mark.asyncio
    async def test_dev_mode_returns_dev_user(self):
        """DEV_MODE is also respected by get_optional_user."""
        auth_mod.DEV_MODE = True
        request = make_request()
        result = await auth_mod.get_optional_user(request)
        assert result is not None
        assert result.email == "dev@example.com"
