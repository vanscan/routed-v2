from fastapi import FastAPI, APIRouter, HTTPException, Depends, Request, Response, UploadFile, File, Form, Query
from fastapi.responses import JSONResponse, HTMLResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import errno
import logging
import traceback
import zipfile

# Suppress httpx INFO logs that leak Mapbox API keys and other tokens
# into the server log. Only WARNING+ messages from httpx are useful.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, Literal, Tuple, Sequence
import uuid
import hashlib
from datetime import datetime, timezone, timedelta
import httpx
from haversine import haversine, Unit
import pandas as pd
import io
import asyncio
import math
from openlocationcode import openlocationcode as olc

ORTOOLS_AVAILABLE = True
ORTOOLS_IMPORT_ERROR: Optional[str] = None
try:
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2
except Exception as _ortools_exc:
    ORTOOLS_AVAILABLE = False
    ORTOOLS_IMPORT_ERROR = str(_ortools_exc)

VROOM_AVAILABLE = True
VROOM_IMPORT_ERROR: Optional[str] = None
try:
    import vroom
except Exception as _vroom_exc:
    VROOM_AVAILABLE = False
    VROOM_IMPORT_ERROR = str(_vroom_exc)

LKH_AVAILABLE = True
LKH_IMPORT_ERROR: Optional[str] = None
try:
    # Read persistent-cache-aware binary path from the installer module.
    # Falls back to /usr/local/bin/LKH for dev environments without /app PVC.
    from install_native_solvers import LKH_BIN_PATH as LKH_SOLVER_PATH  # type: ignore
except Exception:
    LKH_SOLVER_PATH = "/usr/local/bin/LKH"
try:
    import lkh
    if not os.path.isfile(LKH_SOLVER_PATH):
        LKH_AVAILABLE = False
        LKH_IMPORT_ERROR = f"LKH binary not found at {LKH_SOLVER_PATH}"
except Exception as _lkh_exc:
    LKH_AVAILABLE = False
    LKH_IMPORT_ERROR = str(_lkh_exc)

PYVRP_AVAILABLE = True
PYVRP_IMPORT_ERROR: Optional[str] = None
try:
    from solvers.pyvrp_tsp_solver import PyVRPTspSolver, DeliveryStop  # noqa: F401
except Exception as _pyvrp_exc:
    PYVRP_AVAILABLE = False
    PYVRP_IMPORT_ERROR = str(_pyvrp_exc)

# elkai - LKH with bundled native C backend (no separate binary needed)
# Production-ready, fast TSP solver. Preferred over external LKH binary.
ELKAI_AVAILABLE = True
ELKAI_IMPORT_ERROR: Optional[str] = None
try:
    import elkai
except Exception as _elkai_exc:
    ELKAI_AVAILABLE = False
    ELKAI_IMPORT_ERROR = str(_elkai_exc)

# Shared coord-clustering wrapper — gives every TSP solver in the pipeline
# (OR-Tools, LKH, VROOM, ILS, GA…) the same same-doorstep super-node
# protection that PyVRP gets internally. Without this, the "Zero-Cost
# Interleaving" bug was reachable through any fallback path.
from solvers.coord_clustering import cluster_aware_solve  # noqa: E402

# Self-heal: if the LKH binary is missing OR present-but-not-runnable on
# this CPU (e.g. an x86_64 binary cached on a PVC then mounted into an
# aarch64 pod after a fork), trigger a background compile. When it
# finishes, flip LKH_AVAILABLE back to True so subsequent /api/benchmark
# requests include LKH again.
_lkh_needs_install = not LKH_AVAILABLE
if LKH_AVAILABLE:
    try:
        from install_native_solvers import _lkh_binary_runnable as _lkh_check
        if not _lkh_check():
            _lkh_needs_install = True
            LKH_AVAILABLE = False
            LKH_IMPORT_ERROR = (
                f"LKH binary at {LKH_SOLVER_PATH} present but not runnable "
                "on this CPU — scheduling rebuild."
            )
            # _lkh_binary_runnable already logged the ENOEXEC detail once;
            # just log a short INFO here so the startup sequence is readable.
            logging.getLogger(__name__).info(LKH_IMPORT_ERROR)
    except Exception:
        # If the runnability probe itself errors out, fall back to the
        # lazy self-disable in lkh_tsp_solve — don't block server startup.
        pass

if _lkh_needs_install:
    try:
        from install_native_solvers import ensure_lkh_installed_background

        def _on_lkh_installed(ok: bool) -> None:
            # Module-level write is fine — Python globals are process-wide and
            # the benchmark endpoint reads them fresh on each request.
            global LKH_AVAILABLE, LKH_IMPORT_ERROR
            if ok:
                LKH_AVAILABLE = True
                LKH_IMPORT_ERROR = None

        ensure_lkh_installed_background(on_complete=_on_lkh_installed)
    except Exception as _installer_exc:
        logging.getLogger(__name__).warning(
            "[lkh-installer] could not schedule background install: %s", _installer_exc
        )

ALNS_AVAILABLE = True
try:
    from solvers import alns_hybrid_optimize
except Exception as _alns_exc:
    ALNS_AVAILABLE = False

TIMEFOLD_AVAILABLE = False
TIMEFOLD_IMPORT_ERROR: Optional[str] = None

# Timefold is one of 14 VRP solvers and is disabled by default — it needs a
# JDK + matching libjvm.so path which varies across container images. Set
# ENABLE_TIMEFOLD=true in the environment to opt back in (dev only).
# NB: load .env first so this flag is read correctly at module import time
#     (the main load_dotenv() further below runs after this block).
try:
    from dotenv import load_dotenv as _tf_load_dotenv
    _tf_load_dotenv(Path(__file__).parent / '.env')
except Exception:
    pass
_TIMEFOLD_ENABLED = os.environ.get("ENABLE_TIMEFOLD", "false").lower() in ("true", "1", "yes", "on")

if _TIMEFOLD_ENABLED:
    try:
        import os as _os_tf
        _os_tf.environ.setdefault("JAVA_HOME", "/usr/lib/jvm/java-17-openjdk-arm64")
        from timefold_solver import timefold_optimize
        TIMEFOLD_AVAILABLE = True
    except Exception as _tf_exc:
        TIMEFOLD_IMPORT_ERROR = str(_tf_exc)
        timefold_optimize = None
else:
    timefold_optimize = None

# Self-heal: if the Java JDK is missing (production image), apt-get install it
# in the background, then lazy-reimport timefold_solver. Takes ~60s on first run.
# Skipped entirely when ENABLE_TIMEFOLD is off (the default) — keeps prod logs clean.
if _TIMEFOLD_ENABLED and not TIMEFOLD_AVAILABLE:
    try:
        from install_native_solvers import ensure_timefold_installed_background

        def _on_timefold_installed(ok: bool) -> None:
            global TIMEFOLD_AVAILABLE, timefold_optimize, TIMEFOLD_IMPORT_ERROR
            if ok:
                try:
                    # Fresh import — JPype should now find the apt-installed JDK
                    from timefold_solver import timefold_optimize as _tf_opt
                    timefold_optimize = _tf_opt
                    TIMEFOLD_AVAILABLE = True
                    TIMEFOLD_IMPORT_ERROR = None
                except Exception as e:
                    TIMEFOLD_IMPORT_ERROR = f"post-install import failed: {e}"

        ensure_timefold_installed_background(on_complete=_on_timefold_installed)
    except Exception as _installer_exc:
        logging.getLogger(__name__).warning(
            "[timefold-installer] could not schedule background install: %s", _installer_exc
        )

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# Configure logging first
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

if not ORTOOLS_AVAILABLE:
    logger.warning("OR-Tools is not available at startup: %s", ORTOOLS_IMPORT_ERROR)

# MongoDB connection with connection pooling settings for production
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(
    mongo_url,
    # Atlas-safe defaults: avoid eager socket creation and allow slower DNS/network handshakes
    maxPoolSize=50,
    minPoolSize=0,
    serverSelectionTimeoutMS=30000,
    connectTimeoutMS=30000,
    socketTimeoutMS=30000,
    waitQueueTimeoutMS=30000,
    connect=False,
    retryWrites=True,
    retryReads=True
)
db = client[os.environ['DB_NAME']]
APP_START_TIME = datetime.now(timezone.utc)
DB_READY_GRACE_SECONDS = int(os.environ.get('DB_READY_GRACE_SECONDS', '300'))

# ===================== Shared HTTP Client =====================
# Reuses TCP connections across all requests instead of creating/destroying per request
_shared_http_client: httpx.AsyncClient | None = None

async def get_http_client() -> httpx.AsyncClient:
    global _shared_http_client
    if _shared_http_client is None or _shared_http_client.is_closed:
        _shared_http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        )
    return _shared_http_client

# ===================== Directions Cache =====================
# Avoids redundant Mapbox API calls on repeated GPS ticks (same coordinates within 30s)
import time as _time
from collections import OrderedDict

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

_directions_cache = TTLCache(maxsize=200, ttl=30)

# OSRM duration matrix cache — avoids redundant OSRM calls for identical stop sets
# TTL=600s (10 min), max 50 route matrices cached
_osrm_matrix_cache = TTLCache(maxsize=50, ttl=600)

# Mapbox token
MAPBOX_TOKEN = os.environ.get('MAPBOX_TOKEN', '')
EMERGENT_LLM_KEY = os.environ.get('EMERGENT_LLM_KEY', '')

# OSRM routing server URL (for VROOM duration matrix)
OSRM_URL = os.environ.get('OSRM_URL', 'https://router.project-osrm.org')
# Public OSRM demo server, used as a last-ditch fallback when the local
# OSRM is unreachable (e.g. on production where the binary isn't shipped).
# It's rate-limited (~1 req/sec) and has a per-call 100-coord cap, but
# delivers real road-network durations — far better than the Mapbox
# clustered matrix for solver quality. Set empty to disable.
OSRM_PUBLIC_URL = os.environ.get('OSRM_PUBLIC_URL', 'https://router.project-osrm.org')

# ── Route Telepathy allowlist ────────────────────────────────────────────
# Which user_ids get the learned-sequence reorder applied in /api/optimize.
# Configurable via TELEPATHY_USER_IDS (comma-separated). When unset it
# DEFAULTS to the admin allowlist (STRIPE_ADMIN_USER_IDS) so an owner who
# already bypasses the paywall gets Telepathy with zero extra Coolify config.
# The original owner id is always included for back-compat.
_telepathy_csv = os.environ.get('TELEPATHY_USER_IDS', '').strip()
if _telepathy_csv:
    TELEPATHY_USER_IDS = {u.strip() for u in _telepathy_csv.split(',') if u.strip()}
else:
    _tp_admin_csv = os.environ.get('STRIPE_ADMIN_USER_IDS', '')
    TELEPATHY_USER_IDS = {u.strip() for u in _tp_admin_csv.split(',') if u.strip()}
TELEPATHY_USER_IDS.add('user_2a7d88cbb419')
logger.info(f"TELEPATHY_USER_IDS loaded: {TELEPATHY_USER_IDS}")

# Optional production OSRM URL: when set AND the configured OSRM_URL is a
# loopback host that's not actually listening (i.e. we're running on the
# production pod which doesn't ship the OSRM binary), promote this URL to
# OSRM_URL at startup. Lets a single .env file work both in sandbox (fast
# localhost OSRM) and on the Emergent production pod (no OSRM binary)
# without per-environment branching at every call site.
OSRM_URL_PROD = os.environ.get('OSRM_URL_PROD', 'https://pathpilot-osrm.fly.dev').strip()
if OSRM_URL_PROD and OSRM_URL.startswith(('http://localhost', 'http://127.', 'http://[::1]')):
    import socket as _socket
    import time as _osrm_probe_time
    from urllib.parse import urlparse as _urlparse
    _parsed = _urlparse(OSRM_URL)
    _host = _parsed.hostname or 'localhost'
    _port = _parsed.port or 80
    # OSRM is supervisor-managed and may still be loading the .osrm.* mmap
    # files when uvicorn boots (the binary takes ~3-5 s on container start).
    # Retry the probe a few times so we don't spuriously promote to the
    # remote URL on every container restart and silently degrade sandbox
    # latency from <10 ms (local) to ~250 ms (Fly.io).
    _osrm_local_alive = False
    for _attempt in range(15):
        try:
            with _socket.create_connection((_host, _port), timeout=1.0):
                _osrm_local_alive = True
                break
        except Exception:
            _osrm_probe_time.sleep(1.0)
    if not _osrm_local_alive:
        logger.info(
            "Local OSRM at %s unreachable after 15 s; promoting OSRM_URL_PROD=%s",
            OSRM_URL, OSRM_URL_PROD,
        )
        OSRM_URL = OSRM_URL_PROD

# ── OSRM circuit breaker ───────────────────────────────────────────────
# In production (no local OSRM binary), every request fails and floods logs with
# identical warnings. After 3 consecutive failures we suppress subsequent warnings
# for 5 minutes (the fallback to Mapbox still happens transparently). While
# suppressed, we ALSO short-circuit the HTTP attempt entirely — avoids spending
# 2-5 s per request waiting for a TCP timeout on a host we know is unreachable.
_osrm_consecutive_failures = 0
_osrm_suppress_until = 0.0
_OSRM_FAIL_THRESHOLD = 3
_OSRM_SUPPRESS_SECONDS = 300


def _osrm_enabled() -> bool:
    """True when OSRM should be attempted on this request.

    Returns False when the URL is unset, or when the circuit breaker is open
    (i.e. we hit the failure threshold within the suppression window).
    Callers that currently gate on `if OSRM_URL:` should switch to this so
    they don't burn a TCP timeout on every request in production.
    """
    if not OSRM_URL:
        return False
    import time as _time
    if _osrm_consecutive_failures >= _OSRM_FAIL_THRESHOLD and _time.time() < _osrm_suppress_until:
        return False
    return True


def _osrm_log_failure(context: str, exc) -> None:
    """Log an OSRM failure once; after threshold is reached, suppress for a window."""
    global _osrm_consecutive_failures, _osrm_suppress_until
    import time as _time
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

# Generoute API for route optimization
GENEROUTE_API_KEY = os.environ.get('GENEROUTE_API_KEY', '')

app = FastAPI()
api_router = APIRouter(prefix="/api")

# ===================== Models =====================
#
# All Pydantic request/response/domain models live in /app/backend/models/
# now. We re-export them here so legacy imports (`from server import Stop,
# StopUpdate, ...`) used by routes/stops.py and other modules keep working
# transparently. Future modules should prefer importing directly from
# `models` instead of through this re-export layer.

from models import (  # noqa: F401  (re-exports for backward compat)
    User,
    UserSession,
    TimeWindow,
    GeocodeCacheEntry,
    Stop,
    StopCreate,
    StopUpdate,
    RegeocodeStopRequest,
    RegeocodeStopResponse,
    CarStopActionRequest,
    FieldMapping,
    ImportPreviewResponse,
    ImportResult,
    Route,
    ReorderRequest,
    AlertType,
    MapAlert,
    AlertCreate,
    AlertResponse,
    GenerouteLocation,
    GenerouteRequest,
    OptimizationHub,
    RefinementSection,
    OptimizationRequest,
    TightenClusterRequest,
    BenchmarkRequest,
    VanLayout,
)


# ===================== Auth Helpers =====================

# Supabase JWT validation
import jwt as pyjwt  # PyJWT
from jwt import PyJWKClient  # For JWKS-based ES256 verification

# Google ID Token verification
from google.oauth2 import id_token as google_id_token
from google.auth.transport import requests as google_auth_requests

SUPABASE_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")

# Supabase JWKS endpoint for ES256 token verification
# Supabase issues ES256 tokens that need to be verified against their public keys
# Note: The .well-known/jwks.json endpoint requires the apikey header
_SUPABASE_JWKS_URL = f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json" if SUPABASE_URL else None
_supabase_jwks_cache: Optional[dict] = None
_supabase_jwks_cache_time: float = 0
_JWKS_CACHE_TTL = 3600  # Cache JWKS for 1 hour

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
        return _supabase_jwks_cache  # Return stale cache if available


def _get_supabase_signing_key(kid: str) -> Optional[bytes]:
    """Get the Supabase public key for ES256 verification by key ID."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.backends import default_backend
    import base64
    
    jwks = _fetch_supabase_jwks()
    if not jwks:
        return None
    
    for key_data in jwks.get('keys', []):
        if key_data.get('kid') == kid and key_data.get('alg') == 'ES256':
            try:
                # Convert JWK to PEM format for PyJWT
                x = base64.urlsafe_b64decode(key_data['x'] + '==')
                y = base64.urlsafe_b64decode(key_data['y'] + '==')
                
                # Create EC public key from x,y coordinates
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

# Google OAuth Client IDs (needed to verify the audience claim)
GOOGLE_WEB_CLIENT_ID = os.environ.get("GOOGLE_WEB_CLIENT_ID", "")
GOOGLE_ANDROID_CLIENT_ID = os.environ.get("GOOGLE_ANDROID_CLIENT_ID", "")
GOOGLE_IOS_CLIENT_ID = os.environ.get("GOOGLE_IOS_CLIENT_ID", "")

# All valid Google client IDs for audience verification
_GOOGLE_CLIENT_IDS = [
    cid for cid in [GOOGLE_WEB_CLIENT_ID, GOOGLE_ANDROID_CLIENT_ID, GOOGLE_IOS_CLIENT_ID] 
    if cid
]

logger.info("[auth] Google Client IDs loaded: %d IDs configured (WEB=%s, ANDROID=%s, IOS=%s)", 
           len(_GOOGLE_CLIENT_IDS),
           'YES' if GOOGLE_WEB_CLIENT_ID else 'NO',
           'YES' if GOOGLE_ANDROID_CLIENT_ID else 'NO',
           'YES' if GOOGLE_IOS_CLIENT_ID else 'NO')


def _decode_google_id_token(token: str) -> Optional[dict]:
    """Decode and validate a Google ID token (ES256 signed).
    
    Returns the decoded payload if valid, None otherwise.
    Google ID tokens use ES256 and need to be verified against Google's public keys.
    """
    try:
        logger.info("[auth] Attempting to verify Google ID token, token_length=%d, token_preview=%s...",
                   len(token), token[:20] if len(token) > 20 else token)
        
        # Verify the token against Google's public keys
        # This checks signature, expiration, and issuer
        request = google_auth_requests.Request()
        
        # First try with configured client IDs
        if _GOOGLE_CLIENT_IDS:
            for client_id in _GOOGLE_CLIENT_IDS:
                try:
                    payload = google_id_token.verify_oauth2_token(
                        token, 
                        request, 
                        audience=client_id
                    )
                    
                    # Verify the issuer is Google
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
        
        # No configured client ID matched — reject the token.
        # Accepting tokens with audience=None would allow tokens minted for
        # any other Google OAuth client to authenticate against this API.
        logger.warning("[auth] Google ID token rejected: no configured client ID matched (aud=%s)",
                       "unknown — token not decoded without audience check")
        return None
        
    except Exception as e:
        logger.warning("[auth] Google ID token verification failed: %s", e)
        return None


# Cache the Supabase JWKS client to avoid repeated HTTP calls
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
        # Supabase JWKS endpoint
        jwks_url = f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json"
        _supabase_jwk_client = PyJWKClient(jwks_url)
        logger.info("[auth] Created Supabase JWKS client for URL: %s", jwks_url)
        return _supabase_jwk_client
    except Exception as e:
        logger.warning("[auth] Failed to create Supabase JWKS client: %s", e)
        return None


def _decode_supabase_jwt(token: str) -> Optional[dict]:
    """Decode and validate a Supabase JWT access token.
    
    Supports both:
    - HS256 tokens (legacy, using SUPABASE_JWT_SECRET)
    - ES256 tokens (newer, using Supabase JWKS public keys)
    
    Returns the decoded payload if valid, None otherwise.
    """
    if not token:
        return None
    
    logger.info("[auth] Attempting to decode Supabase JWT, token_length=%d, secret_length=%d, token_preview=%s...",
               len(token), len(SUPABASE_JWT_SECRET), token[:20] if len(token) > 20 else token)
    
    # First, peek at the token header to determine algorithm
    try:
        header = pyjwt.get_unverified_header(token)
        alg = header.get('alg', 'unknown')
        kid = header.get('kid', 'none')
        logger.debug("[auth] Token header: alg=%s, kid=%s", alg, kid)
    except Exception as e:
        logger.warning("[auth] Failed to read token header: %s", e)
        return None
    
    # Try ES256 with JWKS (for newer Supabase projects)
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
            logger.warning("[auth] No JWKS client available for ES256 verification, SUPABASE_URL=%s", SUPABASE_URL[:30] if SUPABASE_URL else 'NOT SET')
            return None
    
    # Try HS256 with shared secret (for older Supabase projects)
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
    """Find or create a MongoDB user record from Supabase JWT or Google ID token payload.
    
    Supabase JWT payload contains:
    - sub: Supabase user UUID
    - email: User's email
    - user_metadata: { full_name, avatar_url, ... }
    
    Google ID token payload contains:
    - sub: Google user ID
    - email: User's email
    - name: Full name
    - picture: Profile picture URL
    """
    supabase_uid = payload.get("sub")
    email = payload.get("email")
    
    if not supabase_uid or not email:
        logger.warning("[auth] JWT payload missing sub or email")
        return None
    
    email = email.lower()
    
    # Check for existing user by email (may have been created via legacy auth)
    existing = await db.users.find_one({"email": email}, {"_id": 0})
    
    if existing:
        # Update Supabase UID if not already set
        if existing.get("supabase_uid") != supabase_uid:
            await db.users.update_one(
                {"email": email},
                {"$set": {"supabase_uid": supabase_uid}},
            )
        return User(**existing)
    
    # Apply the same signup-gate checks that the session/register endpoints enforce.
    # Without this, bearer-token auto-provisioning would bypass SIGNUPS_DISABLED
    # and ALLOWED_USERS_CSV controls entirely.
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

    # Create new user from JWT data
    # Try Supabase format first (user_metadata), then Google format (top-level)
    user_metadata = payload.get("user_metadata", {})
    name = (
        user_metadata.get("full_name") or 
        user_metadata.get("name") or 
        payload.get("name") or  # Google ID token format
        email.split("@")[0]
    )
    picture = (
        user_metadata.get("avatar_url") or 
        user_metadata.get("picture") or
        payload.get("picture")  # Google ID token format
    )
    
    user_id = f"user_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)
    
    # Determine provider based on token issuer
    issuer = payload.get("iss", "")
    provider = "google" if "accounts.google.com" in issuer else "supabase"
    
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
    """Get session from request - supports both legacy sessions and Supabase JWTs.
    
    Priority:
    1. Authorization: Bearer <token> header (Supabase JWT, Google ID token, or legacy session token)
    2. session_token cookie (legacy)
    """
    auth_header = request.headers.get("Authorization")
    session_token = None
    
    # Check Authorization header first
    if auth_header and auth_header.startswith("Bearer "):
        session_token = auth_header.split(" ")[1]
        
        # Try to decode as Supabase JWT first
        if session_token and not session_token.startswith(("ses_", "rvw_")):
            # Looks like a JWT (not a legacy prefixed token)
            payload = _decode_supabase_jwt(session_token)
            if payload:
                # Valid Supabase JWT - store in request state for get_current_user
                request.state.supabase_payload = payload
                # Return a synthetic session (user will be resolved in get_current_user)
                supabase_uid = payload.get("sub")
                return UserSession(
                    user_id=f"supabase:{supabase_uid}",  # Marker for Supabase auth
                    session_token=session_token,
                    expires_at=datetime.fromtimestamp(payload.get("exp", 0), tz=timezone.utc),
                    created_at=datetime.fromtimestamp(payload.get("iat", 0), tz=timezone.utc),
                )
            
            # If Supabase JWT failed, try Google ID token (ES256)
            google_payload = _decode_google_id_token(session_token)
            if google_payload:
                # Valid Google ID token - store in request state
                request.state.supabase_payload = google_payload  # Reuse same attr for compatibility
                request.state.is_google_token = True
                google_sub = google_payload.get("sub")
                return UserSession(
                    user_id=f"supabase:{google_sub}",  # Use same prefix for compatibility
                    session_token=session_token,
                    expires_at=datetime.fromtimestamp(google_payload.get("exp", 0), tz=timezone.utc),
                    created_at=datetime.fromtimestamp(google_payload.get("iat", 0), tz=timezone.utc),
                )
    
    # Fallback to cookie
    if not session_token:
        session_token = request.cookies.get("session_token")
    
    if not session_token:
        return None
    
    # Legacy session lookup in MongoDB
    session = await db.user_sessions.find_one({"session_token": session_token}, {"_id": 0})
    if not session:
        return None
    
    # Check expiry with timezone awareness
    expires_at = session["expires_at"]
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    
    now = datetime.now(timezone.utc)
    if expires_at < now:
        return None

    # ── Sliding-window session refresh on EVERY request ─────────────────
    # Extend the session by 7 days on each API call. This keeps active
    # users logged in indefinitely — only truly inactive sessions expire.
    _SESSION_LIFETIME = timedelta(days=7)
    new_expiry = now + _SESSION_LIFETIME
    
    # Only update if expiry would actually change (avoid unnecessary DB writes)
    # We use a 1-hour buffer to prevent updating on every single request
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

# DEV MODE - Skip authentication for development (set via env var)
DEV_MODE = os.environ.get('DEV_MODE', 'false').lower() in ('true', '1', 'yes')
DEV_USER = User(
    user_id='dev-user-123',
    email='dev@example.com',
    name='Dev User',
    picture=None,
    created_at=datetime.now(timezone.utc)
)

# Auth access policy (ALLOWED_USERS / SIGNUPS_DISABLED) lives in routes/auth.py.

async def get_current_user(request: Request) -> User:
    """Get current authenticated user from request.
    
    Supports:
    1. DEV_MODE bypass (for local development)
    2. Supabase JWT (via Authorization: Bearer header)
    3. Legacy session token (via cookie or Authorization header)
    """
    # DEV MODE: Return dev user without authentication
    if DEV_MODE:
        return DEV_USER
    
    session = await get_session_from_request(request)
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    # Check if this is a Supabase JWT session (user_id starts with "supabase:")
    if session.user_id.startswith("supabase:"):
        # Resolve user from Supabase JWT payload stored in request.state
        supabase_payload = getattr(request.state, "supabase_payload", None)
        if not supabase_payload:
            raise HTTPException(status_code=401, detail="Invalid Supabase session")
        
        user = await _get_or_create_user_from_supabase(supabase_payload)
        if not user:
            raise HTTPException(status_code=401, detail="Failed to resolve Supabase user")
        
        return user
    
    # Legacy session: lookup user by user_id in MongoDB
    user = await db.users.find_one({"user_id": session.user_id}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    
    return User(**user)

async def get_optional_user(request: Request) -> Optional[User]:
    try:
        return await get_current_user(request)
    except HTTPException:
        return None

# ===================== Auth Endpoints =====================
# Moved to routes/auth.py. Wired on the shared api_router near the bottom
# of this file alongside tiles/housenumbers routers.

# ── Geocoding helpers moved to routes/_geocoding.py ────────────────────
# Re-imported here so existing callers in this file (regeocode, refresh-
# suburbs) continue to work without any changes.
from routes._geocoding import (  # noqa: E402
    extract_suburb_from_address,
    reverse_geocode_suburb,
    get_user_geocoding_context,
    normalize_address,
    geocode_address_async,
    _call_mapbox_geocode,
    _encode_plus_code,
    _extract_access_navigation_point,
    _extract_rich_feature,
    _build_stop_geocode_metadata,
    _cache_geocode_result,
)

# ── Stop CRUD + reorder moved to routes/stops.py ─────────────────────────
# GET/POST/PUT/DELETE /stops, /stops/{id}, /stops/{id}/complete,
# /stops/{id}/uncomplete, /stops/clear, /stops/reorder, /debug/stops-coords
# are now served from that router.

# Route History + Learning endpoints live in routes/route_history.py
# (wired in via api_router.include_router near the other router includes).

# Regeocode + refresh-suburbs moved to routes/geocoding.py; the Android
# Auto endpoints (/car/stop-action, /car/next-stops) moved to routes/car.py
# — both wired via api_router.include_router near the other router includes.

# XLS Import endpoints (/import/*) live in routes/import_stops.py
# (wired in via api_router.include_router near the other router includes).

# ===================== Route Optimization =====================


async def calculate_road_distance_km(stops: List[dict]) -> Optional[float]:
    """Calculate total road distance via OSRM Route API (primary) or Mapbox (fallback).

    Uses the OSRM Route service to get the actual road distance in km
    for the ordered sequence of stops. Falls back to Mapbox if OSRM unavailable.
    """
    if len(stops) < 2:
        return None

    # --- Primary: OSRM Route API ---
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

    # --- Fallback: Mapbox Directions ---
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
                    matrix[i][j] = distances[i][j] / 1000.0  # meters to km
                elif i != j:
                    # Unreachable pair: fall back to haversine
                    c1 = (stops[i]["latitude"], stops[i]["longitude"])
                    c2 = (stops[j]["latitude"], stops[j]["longitude"])
                    matrix[i][j] = haversine(c1, c2, unit=Unit.KILOMETERS)
        return matrix
    except Exception as exc:
        logger.warning("Mapbox Matrix API call failed: %s", exc)
        return None


async def _mapbox_duration_matrix_batch(stops: List[dict]) -> Optional[List[List[int]]]:
    """Call Mapbox Matrix API for up to 25 stops.
    Returns DURATION matrix in integer seconds, or None on failure.
    This is the primary input for OR-Tools optimization (optimize for driving time)."""
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
                    matrix[i][j] = max(1, int(durations[i][j]))  # seconds, min 1
                elif i != j:
                    # Unreachable: estimate from haversine at 30 km/h
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
    """Generate a deterministic, ORDER-INDEPENDENT cache key from stop coordinates.

    Sorts coords so that re-optimizing the same set of stops (in any order)
    hits the same cache entry. Rounds to 6 decimals (~0.1m precision).
    """
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

    Args:
        stops: optimised stop dicts in visit order. Each must have
               `latitude`, `longitude`, `id` keys.
        spike_ratio: trigger threshold. A triplet is flagged when
            `haversine(A, C) / (haversine(A, B) + haversine(B, C)) < spike_ratio`.
            Default 0.5 — flag when the detour is more than 2× the straight
            A→C distance. Was 0.3 (require >3.3× detour) but real driver
            screenshots showed obvious zig-zags slipping through; we trade
            a few extra OSRM verifications for catching mid-cluster spikes
            that are visually offensive even when the underlying detour
            ratio is "only" 2-3×.
        min_detour_km: floor on `A→B + B→C` to suppress micro-noise.
            Default 0.10 km (was 0.15) so dense urban routes — where every
            stop is 100-200 m apart but a single zig still reads as wrong
            on the screen — get auto-tightened too.
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
            continue  # missing coords on this triplet — skip silently
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
    This is critical for production where the local OSRM binary isn't
    shipped — a real road-network matrix from public OSRM, even rate-limited,
    is strictly better for solver quality than a stitched Mapbox clustered
    matrix (which has approximate cells across cluster seams and causes
    visible zigzags in the optimised route).
    """
    n = len(stops)
    if n < 2:
        return None

    # --- Cache lookup (shared across candidate URLs) ---
    cache_key = _osrm_cache_key(stops)
    cached = _osrm_matrix_cache.get(cache_key)
    if cached is not None:
        logger.info("OSRM matrix CACHE HIT (%d stops, key=%s)", n, cache_key)
        return cached

    # Build the candidate URL list in quality-priority order. The goal: a
    # 200-stop manifest must resolve against a high-capacity OSRM (single
    # NxN call) and must NEVER silently degrade to the rate-limited public
    # demo (100-coord cap → stitched haversine batches → bad clustering)
    # when a dedicated prod OSRM is reachable — even if Coolify didn't set
    # OSRM_URL_PROD (the hardcoded default keeps prod robust).
    candidates: List[tuple[str, str]] = []
    _seen_urls: set[str] = set()

    def _add_candidate(label: str, url: str) -> None:
        if url and url not in _seen_urls:
            candidates.append((label, url))
            _seen_urls.add(url)

    # 1) Fast loopback OSRM (sandbox/dev with a local binary) wins when alive.
    if _osrm_enabled() and OSRM_URL.startswith(('http://localhost', 'http://127.', 'http://[::1]')):
        _add_candidate("local", OSRM_URL)
    # 2) Promoted/primary OSRM_URL when it's a real remote (not the demo).
    if _osrm_enabled() and OSRM_URL != OSRM_PUBLIC_URL:
        _add_candidate("primary", OSRM_URL)
    # 3) Dedicated prod OSRM (large --max-table-size) — always before the demo.
    _add_candidate("prod", OSRM_URL_PROD)
    # 4) Last-ditch rate-limited public demo (100-coord cap).
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
    the next candidate URL. Handles the 100-coord per-call OSRM limit by
    falling back to cross-batch table queries stitched on top of a Haversine
    baseline for any cells the batched queries fail to cover.
    """
    n = len(stops)
    OSRM_BATCH = 100

    # Connect-timeout policy is host-dependent:
    #   • Loopback (localhost) → 2 s fast-fail. A never-listening local OSRM
    #     (e.g. the prod pod with no local binary) bails quickly so we move
    #     on to the next candidate instead of burning the read window.
    #   • Remote OSRM (promoted prod `pathpilot-osrm.fly.dev`, or the public
    #     demo) → 10 s connect. A 2 s connect is far too aggressive for a
    #     remote host that needs a TLS handshake and may be cold-starting
    #     (Fly.io scale-to-zero). Spurious connect timeouts here were the
    #     cause of the prod OSRM silently falling back to the rate-limited
    #     public demo / Mapbox stitching → bad clustering on 200-stop runs.
    _is_loopback = base_url.startswith(('http://localhost', 'http://127.', 'http://[::1]'))
    _connect_timeout = 2.0 if _is_loopback else 10.0
    OSRM_TIMEOUT = httpx.Timeout(connect=_connect_timeout, read=45.0, write=10.0, pool=5.0)

    try:
        async with httpx.AsyncClient(timeout=OSRM_TIMEOUT) as client:
            # Try single call first (local OSRM with --max-table-size=500 supports this)
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

            # If single call fails (e.g., public OSRM TooBig limit), fall back to batching
            if n <= OSRM_BATCH:
                return None  # Small enough for single call; if that failed, give up

            # Batched: split into groups of ≤40 for cross-batch queries
            # Cross-batch: 40 src + 40 dst = 80 unique coords ≤ 100 OSRM limit
            HALF = 40
            batches = [list(range(i, min(i + HALF, n))) for i in range(0, n, HALF)]

            # Start with haversine baseline
            matrix = _haversine_duration_matrix(stops)

            sem = asyncio.Semaphore(1)  # Serialize for public OSRM demo server rate limits

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
                    return None  # All retries exhausted

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
            # Require at least 70% coverage before trusting the matrix; below
            # that the haversine baseline dominates and solvers will again
            # see false diagonals. Letting the caller fall through to the
            # next candidate (or to Mapbox clusters) is the safer choice.
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


# Separate cache for OSRM distance matrices (km)
_osrm_distance_cache = TTLCache(maxsize=50, ttl=600)

async def _osrm_distance_matrix(stops: List[dict]) -> Optional[List[List[float]]]:
    """Fetch full NxN distance matrix (km) from OSRM Table service.

    Uses annotations=distance to get road distances instead of durations.
    Cached with 10-min TTL. Returns matrix of floats in km, or None on failure.
    """
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

        # Batched approach for > 100 stops
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


def _open_path_matrix(matrix: List[List[int]], depot: int) -> List[List[int]]:
    """Convert a closed-loop matrix to an open-path matrix by zeroing return-to-depot.

    Why this exists:
        Delivery routes don't return to depot — the driver finishes at whichever
        stop is last. Closed-loop TSP solvers (LKH, PyVRP via Hybrid Genetic
        Search with `end_depot`) optimise the full Hamiltonian cycle including
        the return leg back to the start. The "optimal" cycle is often
        catastrophically wrong for open-path delivery: the solver routes
        `depot → far_cluster → ... → near_cluster → back_to_depot` because that
        minimises the cycle, but the driver actually drives `depot → far_cluster
        → ... → near_cluster` and stops there — having driven past every
        near_cluster house at the start.

        The standard fix: tell the solver the return edge costs zero. Then the
        closed-loop optimum is identical to the open-path optimum because the
        return is "free" and never affects the objective.

    Args:
        matrix: N×N cost matrix (seconds or meters). Will be deep-copied.
        depot: Index of the start node. The column `[i][depot]` is zeroed for
            all i != depot, leaving the diagonal alone.

    Returns:
        A new N×N matrix with the same shape and same row/col semantics, but
        with `result[i][depot] = 0` for `i != depot`. The original matrix is
        left untouched (callers can still report distances from it).
    """
    n = len(matrix)
    if n == 0:
        return []
    # Use list comprehension over per-row slice to keep the original immutable
    out = [list(row) for row in matrix]
    if 0 <= depot < n:
        for i in range(n):
            if i != depot:
                out[i][depot] = 0
    return out


def vroom_tsp_solve(
    duration_matrix: List[List[int]],
    depot: int = 0,
    exploration_level: int = 5,
) -> List[int]:
    """Solve open-path TSP using VROOM (pyvroom).

    Args:
        duration_matrix: NxN integer seconds matrix.
        depot: Starting node index.
        exploration_level: VROOM search depth (1-5, higher = better but slower).

    Returns:
        Ordered list of stop indices (excluding depot if it appears at start).
    """
    if not VROOM_AVAILABLE:
        raise RuntimeError(f"pyvroom not available: {VROOM_IMPORT_ERROR}")

    n = len(duration_matrix)
    if n <= 1:
        return list(range(n))

    problem = vroom.Input()

    # Set the pre-computed duration matrix (accepts list-of-lists directly)
    problem.set_durations_matrix(profile="car", matrix_input=duration_matrix)

    # Single vehicle starting at depot (open-path: no explicit end)
    problem.add_vehicle(vroom.Vehicle(id=0, start=depot, profile="car"))

    # All non-depot stops as jobs
    jobs = []
    for i in range(n):
        if i != depot:
            jobs.append(vroom.Job(id=i, location=i))
    problem.add_job(jobs)

    # Solve
    solution = problem.solve(exploration_level=exploration_level, nb_threads=4)

    # Extract route order from solution.
    # pyvroom returns solution.routes as a pandas DataFrame with columns:
    # vehicle_id, type, arrival, duration, setup, service, waiting_time, location_index, id, description
    route_indices = [depot]
    routes_df = solution.routes
    if routes_df is not None and len(routes_df) > 0:
        job_rows = routes_df[routes_df["type"] == "job"]
        for _, row in job_rows.iterrows():
            job_id = int(row["id"])
            if job_id != depot:
                route_indices.append(job_id)

    # Add any stops missed by VROOM (shouldn't happen, but defensive)
    visited = set(route_indices)
    for i in range(n):
        if i not in visited:
            route_indices.append(i)

    return route_indices


def pyvrp_tsp_solve(
    duration_matrix: List[List[int]],
    depot: int = 0,
    time_limit_seconds: float = 2.0,
    seed: int = 0,
    coordinates: Optional[List[Tuple[float, float]]] = None,
) -> List[int]:
    """Solve open-path TSP using PyVRP's Hybrid Genetic Search.

    Thin adapter over `PyVRPTspSolver` that matches the shape of the other
    native-solver wrappers (`vroom_tsp_solve`, `lkh_tsp_solve`, …): take a
    duration matrix whose row/col 0 is the depot, return an index list that
    starts at `depot` and visits every other node exactly once.

    Args:
        duration_matrix: N×N integer seconds matrix.
        depot: Index of the starting node inside `duration_matrix`.
        time_limit_seconds: HGS search budget (1-2s is plenty for pure TSP).
        seed: Deterministic seed for reproducible test runs.
        coordinates: Optional list of `(longitude, latitude)` per matrix row
            (length must equal `len(duration_matrix)`). When supplied,
            stops sharing identical `(lon, lat)` are collapsed into a single
            PyVRP super-node and re-expanded in input order — this stops the
            HGS solver from randomly shuffling stops at the same address
            (apartments/units in one building) which would otherwise produce
            visible zig-zags on the map.

    Returns:
        Ordered list of node indices beginning with `depot`.
    """
    if not PYVRP_AVAILABLE:
        raise RuntimeError(f"pyvrp not available: {PYVRP_IMPORT_ERROR}")

    n = len(duration_matrix)
    if n <= 1:
        return list(range(n))

    if coordinates is not None and len(coordinates) != n:
        raise ValueError(
            f"coordinates length {len(coordinates)} does not match matrix "
            f"size {n}"
        )

    # ── Open-path TSP via free return edge ────────────────────────────────
    # PyVRP's HGS is a closed-loop solver (vehicle.end_depot = depot is a
    # required field). For delivery routes the driver does NOT return to
    # depot, so we patch the return-to-depot column to 0 BEFORE handing the
    # matrix to PyVRP. The closed-loop optimum on the patched matrix equals
    # the open-path optimum on the original. Without this, PyVRP routinely
    # picked routes like `[0, 37, 38, ..., 1, 2, 3]` — efficient if you'd
    # return to the start, but pessimal for one-way delivery (driver passed
    # stop 1 at the start and had to come back at the end).
    duration_matrix = _open_path_matrix(duration_matrix, depot)

    # PyVRP expects numpy + integer seconds. Build the matrix so row/col 0
    # correspond to the depot regardless of what `depot` the caller passed.
    import numpy as _np  # local import — matches the pattern used elsewhere
    matrix = _np.asarray(duration_matrix, dtype=_np.int64)
    if depot != 0:
        order = [depot] + [i for i in range(n) if i != depot]
        matrix = matrix[_np.ix_(order, order)]
    else:
        order = list(range(n))

    # Build per-stop DeliveryStop including coords (if any) so PyVRPTspSolver
    # can collapse identical-coordinate clusters into super-nodes.
    if coordinates is not None:
        stops = [
            DeliveryStop(
                stop_id=original_idx,
                service_duration=0,
                x=float(coordinates[original_idx][0]),
                y=float(coordinates[original_idx][1]),
            )
            for original_idx in order[1:]
        ]
        depot_lon, depot_lat = coordinates[depot]
        depot_stop = DeliveryStop(
            stop_id=depot,
            service_duration=0,
            x=float(depot_lon),
            y=float(depot_lat),
        )
    else:
        stops = [
            DeliveryStop(stop_id=original_idx, service_duration=0)
            for original_idx in order[1:]
        ]
        depot_stop = DeliveryStop(stop_id=depot, service_duration=0)

    solver = PyVRPTspSolver(
        max_runtime_seconds=time_limit_seconds,
        seed=seed,
        display=False,
    )
    sequence = solver.solve(
        depot=depot_stop,
        stops=stops,
        time_matrix=matrix,
    )

    # `sequence` is already a list of ORIGINAL node indices (we stuffed the
    # original index into `stop_id`), so just prepend the depot to match the
    # convention used by `vroom_tsp_solve` and `lkh_tsp_solve`.
    visited = [depot] + [int(sid) for sid in sequence]

    # Defensive: if PyVRP ever drops a node, append it so callers never lose
    # a stop. Mirrors the guard at the bottom of `vroom_tsp_solve`.
    seen = set(visited)
    for i in range(n):
        if i not in seen:
            visited.append(i)
    return visited


def lkh_tsp_solve(
    duration_matrix: List[List[int]],
    depot: int = 0,
    runs: int = 5,
    time_limit_seconds: int = 10,
) -> List[int]:
    """Solve ATSP using LKH-3 (Lin-Kernighan-Helsgaun), the gold-standard TSP heuristic.

    Args:
        duration_matrix: NxN integer cost matrix (seconds).
        depot: Starting node index (fixed as tour start).
        runs: Number of LKH trial runs (more = better quality, slower).
        time_limit_seconds: Max wall-clock time for the solver.

    Returns:
        Ordered list of 0-indexed stop indices starting from depot.
    """
    global LKH_AVAILABLE, LKH_IMPORT_ERROR
    if not LKH_AVAILABLE:
        raise RuntimeError("LKH-3 binary not available")

    n = len(duration_matrix)
    if n <= 2:
        return list(range(n))

    # ── Matrix sanitisation ──────────────────────────────────────────────
    # OSRM occasionally returns `null`/negative cells for un-snappable coords;
    # passed verbatim to LKH those become "free" or "negative-cost" edges and
    # the solver gladly exploits them, producing visibly absurd tours. Force
    # `null/NaN/<0 → PENALTY_SECONDS` and the diagonal to 0 BEFORE the
    # open-path patch so the depot column zero-out is preserved.
    from solvers.pyvrp_tsp_solver import sanitize_osrm_matrix
    clean = sanitize_osrm_matrix(duration_matrix).tolist()

    # ── Open-path TSP via free return edge ────────────────────────────────
    # LKH solves a closed Hamiltonian cycle. For delivery routes we DO NOT
    # return to the depot — the driver finishes wherever the last stop is.
    # Zeroing the return-to-depot column makes the closed-loop optimum equal
    # to the open-path optimum because the return leg becomes free and drops
    # out of the objective. Without this, LKH produced routes that started
    # `depot → far_cluster → ...` because returning past near_cluster was
    # cheap in the cycle, even though the driver never actually returns.
    open_path_matrix = _open_path_matrix(clean, depot)

    # LKH uses ATSP format with FULL_MATRIX edge weights.
    problem = lkh.LKHProblem(
        type='ATSP',
        dimension=n,
        edge_weight_type='EXPLICIT',
        edge_weight_format='FULL_MATRIX',
        edge_weights=open_path_matrix,
    )

    # Scale runs and time with problem size
    actual_runs = max(runs, min(10, n // 20))
    actual_time = max(time_limit_seconds, min(30, n // 10))

    try:
        result = lkh.solve(
            solver=LKH_SOLVER_PATH,
            problem=problem,
            runs=actual_runs,
            time_limit=actual_time,
        )
    except OSError as exec_err:
        # ── Architecture mismatch self-disable ────────────────────────────
        # `[Errno 8] Exec format error` fires when the cached LKH binary at
        # LKH_SOLVER_PATH was compiled for a CPU arch that doesn't match the
        # current container (e.g. x86_64 binary on aarch64). Without this
        # guard every Optimize call re-tries LKH, re-throws OSError, and
        # spams the production log via the caller's `logger.warning`.
        # Flip `LKH_AVAILABLE=False` so the top-of-function guard short-
        # circuits future calls (and the caller-level `if LKH_AVAILABLE:`
        # blocks skip LKH cleanly). VROOM/3-opt fallback already exists.
        if exec_err.errno in (errno.ENOEXEC, 8):
            if LKH_AVAILABLE:
                LKH_AVAILABLE = False
                LKH_IMPORT_ERROR = (
                    f"LKH binary incompatible with current arch ({exec_err})"
                )
                logger.info(
                    "[lkh] Disabling LKH for this process — binary at %s is "
                    "incompatible with current CPU arch (Errno 8). Falling "
                    "back to VROOM+3-opt.",
                    LKH_SOLVER_PATH,
                )
        raise RuntimeError(f"LKH-3 binary not runnable: {exec_err}") from exec_err

    if not result or not result[0]:
        raise RuntimeError("LKH returned empty solution")

    # LKH returns 1-indexed tour. Convert to 0-indexed.
    tour_1indexed = result[0]
    tour = [x - 1 for x in tour_1indexed]

    # Rotate tour so depot is first
    if depot in tour:
        depot_pos = tour.index(depot)
        tour = tour[depot_pos:] + tour[:depot_pos]

    # Defensive: add any missing nodes
    visited = set(tour)
    for i in range(n):
        if i not in visited:
            tour.append(i)

    return tour


def elkai_tsp_solve(
    duration_matrix: List[List[int]],
    depot: int = 0,
) -> List[int]:
    """Solve ATSP using elkai (bundled LKH C backend - no external binary needed).
    
    elkai is recommended for production due to its native C backend and
    simple installation (pip install elkai).
    
    Args:
        duration_matrix: NxN integer cost matrix (seconds).
        depot: Starting node index (fixed as tour start).
    
    Returns:
        Ordered list of 0-indexed stop indices starting from depot.
    """
    if not ELKAI_AVAILABLE:
        raise RuntimeError(f"elkai not available: {ELKAI_IMPORT_ERROR}")
    
    n = len(duration_matrix)
    if n <= 2:
        return list(range(n))
    
    # Sanitise matrix (handle null/NaN/negative values)
    from solvers.pyvrp_tsp_solver import sanitize_osrm_matrix
    clean = sanitize_osrm_matrix(duration_matrix).tolist()
    
    # Open-path TSP via free return edge
    open_path_matrix = _open_path_matrix(clean, depot)
    
    # elkai expects a flat list for the distance matrix
    # It solves symmetric TSP, so we need to handle ATSP by converting
    # or use the asymmetric version if available
    try:
        # elkai.solve_float_matrix expects List[List[float]]
        tour = elkai.solve_float_matrix(open_path_matrix)
    except AttributeError:
        # Older elkai versions use different API
        # Flatten matrix for elkai.solve_int_matrix
        flat_matrix = [int(cell) for row in open_path_matrix for cell in row]
        tour = elkai.solve_int_matrix(flat_matrix, n)
    
    # Rotate tour so depot is first
    if depot in tour:
        depot_pos = tour.index(depot)
        tour = tour[depot_pos:] + tour[:depot_pos]
    
    # Defensive: add any missing nodes
    visited = set(tour)
    for i in range(n):
        if i not in visited:
            tour.append(i)
    
    return tour


async def calculate_duration_matrix(stops: List[dict]) -> List[List[int]]:
    """Build NxN driving-duration matrix (integer seconds) using Mapbox.

    Used as FALLBACK when OSRM is unavailable.
    - N <= 25: single Mapbox Matrix API call.
    - N > 25: haversine estimate (use OSRM for larger routes).
    """
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
        # For >25 stops without OSRM, haversine is the best we can do via Mapbox
        # (cross-batch queries would require too many API calls)
        logger.info("Duration matrix: %dx%d haversine estimate (Mapbox limit exceeded)", n, n)
        return fallback

    except Exception as exc:
        logger.warning("Duration matrix build failed, using haversine estimate: %s", exc)
        return fallback


async def calculate_road_distance_matrix(stops: List[dict]) -> List[List[float]]:
    """Build distance matrix using OSRM road distances (primary) or Mapbox (fallback).

    Strategy:
    - Try OSRM Table API first (local, no rate limit, handles any N).
    - Fallback to Mapbox Matrix API if OSRM unavailable.
    - Final fallback to haversine if both APIs fail.
    """
    n = len(stops)
    haversine_matrix = calculate_distance_matrix(stops)

    if n <= 1:
        return haversine_matrix

    # --- Primary: OSRM ---
    osrm_dist = await _osrm_distance_matrix(stops)
    if osrm_dist:
        logger.info("Road distance matrix: full %dx%d from OSRM", n, n)
        return osrm_dist

    # --- Fallback: Mapbox ---
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

        # Geographic sort: group nearby stops into clusters.
        sorted_indices = sorted(range(n), key=lambda i: (
            round(stops[i]['latitude'] * 100),
            stops[i]['longitude'],
        ))

        clusters = []
        for i in range(0, n, CLUSTER_SIZE):
            clusters.append(sorted_indices[i:i + CLUSTER_SIZE])

        # Deep copy haversine baseline
        matrix = [row[:] for row in haversine_matrix]

        # Overwrite intra-cluster cells with Mapbox road distances
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


# ==================== FULL ROAD DISTANCE MATRIX (CROSS-BATCH) ====================

async def _mapbox_cross_batch_query(
    client: httpx.AsyncClient,
    stops: List[dict],
    src_global: List[int],
    dst_global: List[int],
    sem: asyncio.Semaphore,
) -> Optional[tuple]:
    """Single Mapbox Matrix API call for a (source_batch, dest_batch) pair.
    
    Combines source and destination coordinates into one request (≤25 coords),
    using the sources/destinations parameters to get the sub-matrix.
    Returns (sub_matrix_km, src_global_indices, dst_global_indices) or None.
    """
    async with sem:
        # Build deduplicated combined coordinate list preserving order
        combined_global = list(src_global)
        dst_only = [i for i in dst_global if i not in set(src_global)]
        combined_global.extend(dst_only)

        if len(combined_global) > 25:
            return None

        # Map global indices to local positions in the combined list
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

            # distances shape: len(src) x len(dst), values in meters
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
    """Build FULL NxN road distance matrix using OSRM (primary) or Mapbox cross-batch (fallback).

    OSRM handles any N natively. Falls back to Mapbox cross-batch queries
    if OSRM is unavailable, then haversine as last resort.
    """
    n = len(stops)
    haversine_matrix = calculate_distance_matrix(stops)

    if n <= 1:
        return haversine_matrix

    # --- Primary: OSRM ---
    osrm_dist = await _osrm_distance_matrix(stops)
    if osrm_dist:
        logger.info("Full road distance matrix: %dx%d from OSRM", n, n)
        return osrm_dist

    # --- Fallback: Mapbox ---
    if not MAPBOX_TOKEN:
        return haversine_matrix

    if n <= 25:
        road = await _mapbox_matrix_batch(stops)
        if road:
            return road
        return haversine_matrix

    try:
        BATCH_SIZE = 12  # 12 src + 12 dst = 24 coords ≤ 25

        # Create batches of global stop indices
        batches = []
        for i in range(0, n, BATCH_SIZE):
            batches.append(list(range(i, min(i + BATCH_SIZE, n))))

        # Deep copy haversine as baseline
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


# ==================== CLUSTER-FIRST ROUTE-SECOND OPTIMIZATION ====================

def _geographic_dbscan(stops: List[dict], eps_km: float = 0.8, min_samples: int = 2) -> List[int]:
    """DBSCAN clustering on geographic coordinates using haversine distance.
    Returns list of cluster labels per stop. -1 = noise (unassigned)."""
    n = len(stops)
    if n == 0:
        return []

    labels = [-1] * n
    cluster_id = 0
    visited = [False] * n

    for i in range(n):
        if visited[i]:
            continue
        visited[i] = True

        # Find all neighbors within eps
        neighbors = []
        for j in range(n):
            if i != j:
                d = haversine(
                    (stops[i]["latitude"], stops[i]["longitude"]),
                    (stops[j]["latitude"], stops[j]["longitude"]),
                    unit=Unit.KILOMETERS,
                )
                if d <= eps_km:
                    neighbors.append(j)

        if len(neighbors) < min_samples - 1:
            continue  # noise, will be assigned in post-processing

        # Start new cluster
        labels[i] = cluster_id
        seed_set = list(neighbors)
        idx = 0

        while idx < len(seed_set):
            j = seed_set[idx]
            idx += 1

            if not visited[j]:
                visited[j] = True
                j_neighbors = []
                for k in range(n):
                    if k != j:
                        d = haversine(
                            (stops[j]["latitude"], stops[j]["longitude"]),
                            (stops[k]["latitude"], stops[k]["longitude"]),
                            unit=Unit.KILOMETERS,
                        )
                        if d <= eps_km:
                            j_neighbors.append(k)

                if len(j_neighbors) >= min_samples - 1:
                    for k in j_neighbors:
                        if labels[k] == -1:
                            seed_set.append(k)

            if labels[j] == -1:
                labels[j] = cluster_id

        cluster_id += 1

    return labels


def _adaptive_eps(stops: List[dict]) -> float:
    """Compute adaptive DBSCAN eps based on stop density.
    Uses k-nearest-neighbor heuristic (k=4) to find natural cluster radius."""
    n = len(stops)
    if n <= 2:
        return 1.0

    # For each stop, find distance to 4th nearest neighbor
    k = min(4, n - 1)
    k_distances = []

    for i in range(n):
        dists = []
        for j in range(n):
            if i != j:
                d = haversine(
                    (stops[i]["latitude"], stops[i]["longitude"]),
                    (stops[j]["latitude"], stops[j]["longitude"]),
                    unit=Unit.KILOMETERS,
                )
                dists.append(d)
        dists.sort()
        k_distances.append(dists[k - 1] if len(dists) >= k else dists[-1])

    # Sort k-distances and find the "elbow" — we use the median as a robust estimate
    k_distances.sort()
    # Use the 60th percentile as eps (captures most natural clusters)
    eps = k_distances[int(n * 0.6)]
    # Clamp to reasonable delivery neighborhood sizes
    return max(0.3, min(2.5, eps))


def _postprocess_clusters(
    labels: List[int],
    stops: List[dict],
    max_cluster_size: int = 23,
    min_cluster_size: int = 2,
) -> List[List[int]]:
    """Post-process DBSCAN clusters:
    - Assign noise points to nearest cluster
    - Split oversized clusters (>max_cluster_size) for Mapbox API compliance
    - Merge tiny clusters into nearest neighbor
    Returns list of lists of global stop indices."""
    from collections import defaultdict

    clusters_map = defaultdict(list)
    noise = []

    for i, label in enumerate(labels):
        if label == -1:
            noise.append(i)
        else:
            clusters_map[label].append(i)

    cluster_list = list(clusters_map.values())

    # If no clusters found, treat everything as one cluster
    if not cluster_list:
        cluster_list = [list(range(len(stops)))]
        noise = []

    # Assign noise points to nearest cluster
    for ni in noise:
        best_ci = 0
        best_dist = float("inf")
        for ci, cluster in enumerate(cluster_list):
            for si in cluster:
                d = haversine(
                    (stops[ni]["latitude"], stops[ni]["longitude"]),
                    (stops[si]["latitude"], stops[si]["longitude"]),
                    unit=Unit.KILOMETERS,
                )
                if d < best_dist:
                    best_dist = d
                    best_ci = ci
        cluster_list[best_ci].append(ni)

    # Split oversized clusters using geographic k-means for spatially compact subclusters
    split_clusters = []
    for cluster in cluster_list:
        if len(cluster) <= max_cluster_size:
            split_clusters.append(cluster)
        else:
            # k-means split: divide into ceil(n/max_cluster_size) spatially compact groups
            import math as _math
            k = _math.ceil(len(cluster) / max_cluster_size)
            coords = [(stops[i]["latitude"], stops[i]["longitude"]) for i in cluster]

            # Initialize centroids using evenly spaced indices from sorted points
            sorted_by_lat = sorted(range(len(cluster)), key=lambda x: coords[x])
            centroids = [coords[sorted_by_lat[int(j * len(cluster) / k)]] for j in range(k)]

            for _ in range(15):  # k-means iterations
                buckets = [[] for _ in range(k)]
                for ci_local, idx in enumerate(cluster):
                    lat, lng = coords[ci_local]
                    best_k = 0
                    best_d = float("inf")
                    for ki in range(k):
                        d = (lat - centroids[ki][0]) ** 2 + (lng - centroids[ki][1]) ** 2
                        if d < best_d:
                            best_d = d
                            best_k = ki
                    buckets[best_k].append(idx)

                # Recompute centroids
                new_centroids = []
                for ki in range(k):
                    if buckets[ki]:
                        avg_lat = sum(stops[i]["latitude"] for i in buckets[ki]) / len(buckets[ki])
                        avg_lng = sum(stops[i]["longitude"] for i in buckets[ki]) / len(buckets[ki])
                        new_centroids.append((avg_lat, avg_lng))
                    else:
                        new_centroids.append(centroids[ki])

                if new_centroids == centroids:
                    break
                centroids = new_centroids

            for bucket in buckets:
                if bucket:
                    split_clusters.append(bucket)

    # Merge tiny clusters into nearest larger cluster (if it won't exceed max)
    final = []
    tiny = []
    for c in split_clusters:
        if len(c) < min_cluster_size:
            tiny.append(c)
        else:
            final.append(c)

    for tc in tiny:
        if not final:
            final.append(tc)
            continue
        tc_lat = sum(stops[i]["latitude"] for i in tc) / len(tc)
        tc_lng = sum(stops[i]["longitude"] for i in tc) / len(tc)

        best_ci = 0
        best_dist = float("inf")
        for ci, c in enumerate(final):
            if len(c) + len(tc) > max_cluster_size:
                continue
            c_lat = sum(stops[i]["latitude"] for i in c) / len(c)
            c_lng = sum(stops[i]["longitude"] for i in c) / len(c)
            d = haversine((tc_lat, tc_lng), (c_lat, c_lng), unit=Unit.KILOMETERS)
            if d < best_dist:
                best_dist = d
                best_ci = ci
        final[best_ci].extend(tc)

    return final if final else [list(range(len(stops)))]


def _order_clusters_tsp(
    clusters: List[List[int]],
    stops: List[dict],
    start_stop_index: int = 0,
) -> List[int]:
    """Order clusters using centroid nearest-neighbor + 2-opt.
    Returns list of cluster indices in visit order."""
    nc = len(clusters)
    if nc <= 1:
        return list(range(nc))

    # Compute centroids
    centroids = []
    for cluster in clusters:
        avg_lat = sum(stops[i]["latitude"] for i in cluster) / len(cluster)
        avg_lng = sum(stops[i]["longitude"] for i in cluster) / len(cluster)
        centroids.append((avg_lat, avg_lng))

    # Find the cluster that contains (or is nearest to) the start stop
    start_ci = 0
    for ci, cluster in enumerate(clusters):
        if start_stop_index in cluster:
            start_ci = ci
            break

    # Nearest-neighbor TSP on centroids
    visited = [False] * nc
    order = [start_ci]
    visited[start_ci] = True

    for _ in range(nc - 1):
        current = order[-1]
        best = -1
        best_dist = float("inf")
        for j in range(nc):
            if not visited[j]:
                d = haversine(centroids[current], centroids[j], unit=Unit.KILOMETERS)
                if d < best_dist:
                    best_dist = d
                    best = j
        if best != -1:
            order.append(best)
            visited[best] = True

    # 2-opt improvement on the cluster order
    improved = True
    while improved:
        improved = False
        for i in range(1, len(order) - 1):
            for j in range(i + 1, len(order)):
                # Calculate distance change if we reverse order[i:j+1]
                pi, pj = order[i - 1], order[i]
                qi, qj = order[j], order[(j + 1) % len(order)] if j + 1 < len(order) else order[0]

                old_d = (
                    haversine(centroids[pi], centroids[pj], unit=Unit.KILOMETERS)
                    + (haversine(centroids[qi], centroids[qj], unit=Unit.KILOMETERS) if j + 1 < len(order) else 0)
                )
                new_d = (
                    haversine(centroids[pi], centroids[qi], unit=Unit.KILOMETERS)
                    + (haversine(centroids[pj], centroids[qj], unit=Unit.KILOMETERS) if j + 1 < len(order) else 0)
                )
                if new_d < old_d - 0.001:
                    order[i : j + 1] = reversed(order[i : j + 1])
                    improved = True

    return order


def _convex_hull(points: List[tuple]) -> List[tuple]:
    """Compute convex hull of 2D points using Andrew's monotone chain.
    Points are (lng, lat) tuples. Returns hull vertices in CCW order."""
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _padded_polygon(hull: List[tuple], pad_deg: float = 0.0002) -> List[List[float]]:
    """Expand a convex hull outward by pad_deg (~20m at equator).
    Returns GeoJSON-compatible closed ring [[lng,lat], ...]."""
    import math

    if len(hull) < 2:
        # Single point → small octagon
        if hull:
            cx, cy = hull[0]
            return [
                [cx + pad_deg * math.cos(a), cy + pad_deg * math.sin(a)]
                for a in [i * math.pi / 4 for i in range(8)]
            ] + [[cx + pad_deg, cy]]
        return []

    if len(hull) == 2:
        # Line segment → diamond
        ax, ay = hull[0]
        bx, by = hull[1]
        dx, dy = bx - ax, by - ay
        length = math.sqrt(dx * dx + dy * dy) or 1e-8
        nx, ny = -dy / length * pad_deg, dx / length * pad_deg
        return [
            [ax - dx * 0.1 + nx, ay - dy * 0.1 + ny],
            [bx + dx * 0.1 + nx, by + dy * 0.1 + ny],
            [bx + dx * 0.1 - nx, by + dy * 0.1 - ny],
            [ax - dx * 0.1 - nx, ay - dy * 0.1 - ny],
            [ax - dx * 0.1 + nx, ay - dy * 0.1 + ny],
        ]

    # Compute centroid
    cx = sum(p[0] for p in hull) / len(hull)
    cy = sum(p[1] for p in hull) / len(hull)

    # Push each vertex outward from centroid
    padded = []
    for px, py in hull:
        dx, dy = px - cx, py - cy
        dist = math.sqrt(dx * dx + dy * dy) or 1e-8
        padded.append([px + dx / dist * pad_deg, py + dy / dist * pad_deg])
    padded.append(padded[0])  # close the ring
    return padded


# 15 distinct cluster colors — semi-transparent fills with solid borders
CLUSTER_COLORS = [
    {"fill": "rgba(59, 130, 246, 0.25)", "border": "rgba(59, 130, 246, 0.8)"},   # blue
    {"fill": "rgba(239, 68, 68, 0.25)", "border": "rgba(239, 68, 68, 0.8)"},     # red
    {"fill": "rgba(16, 185, 129, 0.25)", "border": "rgba(16, 185, 129, 0.8)"},   # emerald
    {"fill": "rgba(245, 158, 11, 0.25)", "border": "rgba(245, 158, 11, 0.8)"},   # amber
    {"fill": "rgba(168, 85, 247, 0.25)", "border": "rgba(168, 85, 247, 0.8)"},   # purple
    {"fill": "rgba(236, 72, 153, 0.25)", "border": "rgba(236, 72, 153, 0.8)"},   # pink
    {"fill": "rgba(20, 184, 166, 0.25)", "border": "rgba(20, 184, 166, 0.8)"},   # teal
    {"fill": "rgba(251, 146, 60, 0.25)", "border": "rgba(251, 146, 60, 0.8)"},   # orange
    {"fill": "rgba(99, 102, 241, 0.25)", "border": "rgba(99, 102, 241, 0.8)"},   # indigo
    {"fill": "rgba(34, 197, 94, 0.25)", "border": "rgba(34, 197, 94, 0.8)"},     # green
    {"fill": "rgba(244, 63, 94, 0.25)", "border": "rgba(244, 63, 94, 0.8)"},     # rose
    {"fill": "rgba(6, 182, 212, 0.25)", "border": "rgba(6, 182, 212, 0.8)"},     # cyan
    {"fill": "rgba(234, 179, 8, 0.25)", "border": "rgba(234, 179, 8, 0.8)"},     # yellow
    {"fill": "rgba(139, 92, 246, 0.25)", "border": "rgba(139, 92, 246, 0.8)"},   # violet
    {"fill": "rgba(14, 165, 233, 0.25)", "border": "rgba(14, 165, 233, 0.8)"},   # sky
]


def _or_opt_1_improve(indices: List[int], matrix: List[List[float]]) -> List[int]:
    """Or-opt-1: Relocate single stops to better positions using the road distance matrix.
    Catches cases where stops on the same street get split by stops on adjacent streets."""
    n = len(indices)
    if n <= 3:
        return indices

    best = indices[:]

    def route_cost(r):
        return sum(matrix[r[i]][r[i + 1]] for i in range(len(r) - 1))

    current_cost = route_cost(best)
    improved = True
    iterations = 0

    while improved and iterations < 5:
        improved = False
        iterations += 1
        for i in range(1, len(best)):  # Skip index 0 (start point)
            stop = best[i]
            # Remove stop from current position
            remaining = best[:i] + best[i + 1:]
            # Cost without this stop
            remove_save = (
                matrix[best[i - 1]][best[i]]
                + (matrix[best[i]][best[i + 1]] if i + 1 < len(best) else 0)
                - (matrix[best[i - 1]][best[i + 1]] if i + 1 < len(best) else 0)
            )

            best_j = -1
            best_insert_cost = float("inf")

            for j in range(len(remaining)):
                # Try inserting after position j in remaining
                if j + 1 < len(remaining):
                    insert_cost = (
                        matrix[remaining[j]][stop]
                        + matrix[stop][remaining[j + 1]]
                        - matrix[remaining[j]][remaining[j + 1]]
                    )
                else:
                    insert_cost = matrix[remaining[j]][stop]

                if insert_cost < best_insert_cost:
                    best_insert_cost = insert_cost
                    best_j = j

            # Check if relocating improves total cost
            if best_j >= 0 and best_insert_cost < remove_save - 0.001:
                new_route = remaining[:best_j + 1] + [stop] + remaining[best_j + 1:]
                new_cost = route_cost(new_route)
                if new_cost < current_cost - 0.001:
                    best = new_route
                    current_cost = new_cost
                    improved = True
                    break  # Restart from beginning after improvement

    return best


def _build_cluster_info(
    ordered_clusters: List[List[int]],
    stops: List[dict],
) -> List[dict]:
    """Build GeoJSON-ready cluster visualization data with convex hull polygons."""
    cluster_info = []
    for visit_order, cluster_indices in enumerate(ordered_clusters):
        points = [(stops[i]["longitude"], stops[i]["latitude"]) for i in cluster_indices]
        hull = _convex_hull(points)
        polygon = _padded_polygon(hull)

        centroid_lat = sum(stops[i]["latitude"] for i in cluster_indices) / len(cluster_indices)
        centroid_lng = sum(stops[i]["longitude"] for i in cluster_indices) / len(cluster_indices)
        color = CLUSTER_COLORS[visit_order % len(CLUSTER_COLORS)]

        cluster_info.append({
            "id": visit_order,
            "visit_order": visit_order,
            "stop_count": len(cluster_indices),
            "centroid": {"latitude": round(centroid_lat, 6), "longitude": round(centroid_lng, 6)},
            "polygon": polygon,
            "fill_color": color["fill"],
            "border_color": color["border"],
            "label": f"Zone {visit_order + 1}",
        })
    return cluster_info


def _run_inner_algorithm(
    stops: List[dict],
    matrix: List[List[float]],
    start_index: int,
    time_limit: int,
    algorithm: str,
) -> List[dict]:
    """Run a specific optimization algorithm on a subset of stops.
    Used within cluster_first to apply the user's preferred algorithm per cluster.
    Applies post-optimization 2-opt + or-opt using the road distance matrix
    to catch local swaps the main solver may have missed (e.g., grouping same-street stops)."""
    result = None
    try:
        if algorithm == "ortools" and ORTOOLS_AVAILABLE and pywrapcp:
            # Use ortools_tsp_solve directly — the matrix passed in is already the
            # correct type (duration seconds when cluster_first uses OR-Tools inner)
            time_limit_ms = max(1000, time_limit * 1000)
            indices = ortools_tsp_solve(matrix, depot=start_index, time_limit_ms=time_limit_ms)
            result = [stops[i] for i in indices]
        elif algorithm == "pyvrp" and PYVRP_AVAILABLE:
            pyvrp_seconds = max(1.0, min(2.0, len(stops) * 0.05))
            indices = pyvrp_tsp_solve(matrix, depot=start_index, time_limit_seconds=pyvrp_seconds)
            result = [stops[i] for i in indices]
        elif algorithm == "alns":
            try:
                result = alns_hybrid_optimize(stops, matrix, start_index=start_index, time_limit_seconds=time_limit)
            except NameError:
                logger.warning("ALNS not available, falling back to OR-Tools")
                if ORTOOLS_AVAILABLE and pywrapcp:
                    result = ortools_optimize(stops, matrix, start_index, time_limit)
        elif algorithm == "simulated_annealing":
            result = simulated_annealing_optimize(stops, matrix, start_index)
        elif algorithm == "genetic":
            result = genetic_algorithm_optimize(stops, matrix, start_index)
        elif algorithm == "clarke_wright":
            result = clarke_wright_savings(stops, matrix, start_index)
    except Exception as exc:
        logger.warning("Inner algorithm '%s' failed, falling back to NN+2-opt: %s", algorithm, exc)

    if result is None:
        nn = nearest_neighbor_optimize(stops, matrix, start_index)
        ri = _indices_by_identity(stops, nn)
        result = [stops[i] for i in two_opt_improve(ri, matrix)]

    # Post-optimization: apply road-distance or-opt-1 then 2-opt to catch missed local swaps
    # This fixes cases where stops on the same street get split by stops on adjacent streets
    if len(result) > 3:
        indices = _indices_by_identity(stops, result)
        indices = _or_opt_1_improve(indices, matrix)
        indices = two_opt_improve(indices, matrix)
        result = [stops[i] for i in indices]

    return result


def _global_two_opt_pass(optimized: List[dict], max_iterations: int = 3) -> List[dict]:
    """Apply or-opt-1 + 2-opt on the full stitched route using haversine distances.
    Fixes cross-cluster boundary inefficiencies:
    - or-opt-1 relocates single stops to better positions (e.g., moving stop 46 from
      between 45→47 to after 48, avoiding an unnecessary south→north detour)
    - 2-opt reverses segments to uncross route lines
    - 3-opt (large routes only): non-reversing segment swap to escape 2-opt
      local optima on routes ≥150 stops where boundary stitching tends to
      leave a few residual cross-cluster zig-zags that 2-opt can't fix.
    """
    n = len(optimized)
    if n <= 3:
        return optimized

    # Large routes (≥150 stops) get more aggressive polishing: doubling the
    # iteration budget (3 → 6) gives or-opt and 2-opt enough runway to chase
    # cross-cluster relocations to convergence on long routes, where each
    # iteration only nudges a handful of stops at a time.
    if n >= 150:
        max_iterations = max(max_iterations, 6)

    # Build haversine matrix for the stitched route
    matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            d = haversine(
                (optimized[i]["latitude"], optimized[i]["longitude"]),
                (optimized[j]["latitude"], optimized[j]["longitude"]),
                unit=Unit.KILOMETERS,
            )
            matrix[i][j] = d
            matrix[j][i] = d

    indices = list(range(n))
    best_dist = sum(matrix[indices[i]][indices[i + 1]] for i in range(n - 1))

    # Phase 1: Global or-opt-1 — relocate individual stops across cluster boundaries
    for _iter in range(max_iterations):
        improved = False
        for i in range(1, len(indices)):
            stop_idx = indices[i]
            # Cost of edges touching this stop
            prev_idx = indices[i - 1]
            next_idx = indices[i + 1] if i + 1 < len(indices) else None

            edge_before = matrix[prev_idx][stop_idx]
            edge_after = matrix[stop_idx][next_idx] if next_idx is not None else 0
            edge_skip = matrix[prev_idx][next_idx] if next_idx is not None else 0
            remove_save = edge_before + edge_after - edge_skip

            if remove_save < 0.02:  # Not worth relocating if removal doesn't save much
                continue

            best_j = -1
            best_delta = 0

            # Try inserting this stop at every other position (limited window for speed)
            remaining = indices[:i] + indices[i + 1:]
            for j in range(max(0, i - 40), min(len(remaining), i + 40)):
                a = remaining[j]
                b = remaining[j + 1] if j + 1 < len(remaining) else None
                old_edge = matrix[a][b] if b is not None else 0
                new_edge = matrix[a][stop_idx] + (matrix[stop_idx][b] if b is not None else 0)
                insert_cost = new_edge - old_edge
                delta = remove_save - insert_cost
                if delta > best_delta + 0.01:
                    best_delta = delta
                    best_j = j

            if best_j >= 0:
                # Perform the relocation
                indices.pop(i)
                actual_j = best_j if best_j < i else best_j
                indices.insert(actual_j + 1, stop_idx)
                improved = True
                break  # Restart scan after improvement

        if not improved:
            break

    # Phase 2: Global 2-opt — reverse segments to uncross route lines
    for _ in range(max_iterations):
        improved = False
        for i in range(1, n - 1):
            for j in range(i + 1, min(i + 60, n)):
                d_old = matrix[indices[i - 1]][indices[i]] + (matrix[indices[j]][indices[j + 1]] if j + 1 < n else 0)
                d_new = matrix[indices[i - 1]][indices[j]] + (matrix[indices[i]][indices[j + 1]] if j + 1 < n else 0)
                if d_new < d_old - 0.01:
                    indices[i:j + 1] = reversed(indices[i:j + 1])
                    improved = True
        if not improved:
            break

    # Phase 3: 3-opt polish (large routes only). On routes ≥150 stops the
    # 2-opt pass above usually plateaus with a few residual cross-cluster
    # zig-zags that the reversal-only neighbourhood can't escape.
    # `three_opt_improve` swaps non-adjacent segments without reversing
    # them, which is asymmetric-safe and exact on this haversine matrix.
    # We deliberately keep 3-opt off for smaller routes — the 2-opt window
    # above already converges, and 3-opt's O(n³) inner loop would dominate
    # the per-request budget without measurable quality gain.
    if n >= 150:
        polished = three_opt_improve(indices, matrix, max_iterations=3)
        polished_dist = sum(matrix[polished[i]][polished[i + 1]] for i in range(n - 1))
        current_dist = sum(matrix[indices[i]][indices[i + 1]] for i in range(n - 1))
        if polished_dist < current_dist - 0.01:
            indices = polished
            logger.info(
                "Global 3-opt polish improved route: %.2f km → %.2f km",
                current_dist, polished_dist,
            )

    new_dist = sum(matrix[indices[i]][indices[i + 1]] for i in range(n - 1))
    if new_dist < best_dist:
        logger.info("Global or-opt+2-opt improved route: %.2f km → %.2f km (saved %.2f km)", best_dist, new_dist, best_dist - new_dist)
        return [optimized[i] for i in indices]

    return optimized


async def cluster_first_optimize(
    stops: List[dict],
    distance_matrix: List[List[float]],
    start_index: int = 0,
    time_limit_seconds: int = 30,
    inner_algorithm: str = "ortools",
) -> tuple:
    """Cluster-first route-second optimization.

    Guarantees spatially coherent routing by:
    1. DBSCAN geographic clustering into natural neighborhoods
    2. Inter-cluster ordering via centroid TSP with 2-opt
    3. Intra-cluster optimization with Mapbox road distances + user's preferred algorithm
    4. Smart entry/exit stitching between adjacent clusters
    5. Global 2-opt pass to fix cross-boundary inefficiencies

    Args:
        inner_algorithm: Algorithm to use within each cluster (ortools, alns, etc.)

    Returns (optimized_stops, cluster_info) tuple.
    """
    n = len(stops)
    if n <= 25:
        # Small enough for a single pass — no cluster visualization
        result = _run_inner_algorithm(stops, distance_matrix, start_index, time_limit_seconds, inner_algorithm)
        return result, []

    # Step 1: Geographic clustering
    eps = _adaptive_eps(stops)
    labels = _geographic_dbscan(stops, eps_km=eps, min_samples=2)
    clusters = _postprocess_clusters(labels, stops, max_cluster_size=23, min_cluster_size=2)
    logger.info(
        "Cluster-first (%s): %d clusters from %d stops (eps=%.2f km, sizes=%s)",
        inner_algorithm, len(clusters), n, eps,
        [len(c) for c in clusters],
    )

    # Step 2: Order clusters using centroid TSP
    cluster_order = _order_clusters_tsp(clusters, stops, start_stop_index=start_index)
    ordered_clusters = [clusters[i] for i in cluster_order]

    # Build cluster visualization data
    cluster_info = _build_cluster_info(ordered_clusters, stops)

    # Step 3 & 4: Optimize within each cluster and stitch
    all_optimized: List[dict] = []
    previous_exit_global = start_index
    # per_cluster_time kept as reference for future time-budgeted cluster solves.
    _ = max(5, time_limit_seconds // max(1, len(ordered_clusters)))

    for ci, cluster_indices in enumerate(ordered_clusters):
        cluster_stops = [stops[gi] for gi in cluster_indices]

        if len(cluster_stops) == 1:
            all_optimized.extend(cluster_stops)
            previous_exit_global = cluster_indices[0]
            continue

        # Scale per-cluster OR-Tools time based on cluster size
        # Small clusters are trivially solved — 1 second is plenty
        # OR-Tools GUIDED_LOCAL_SEARCH uses the FULL time limit regardless of problem size
        if len(cluster_stops) <= 5:
            cluster_time = 1
        elif len(cluster_stops) <= 12:
            cluster_time = 2
        elif len(cluster_stops) <= 18:
            cluster_time = 3
        else:
            cluster_time = 5

        # Get cluster cost matrix.
        # OR-Tools inner algorithm uses DURATION (seconds) for time-optimal routing.
        # Other algorithms use road DISTANCE (km).
        if inner_algorithm == "ortools":
            # Try OSRM duration matrix first — same primary source as the main
            # `/optimize` pipeline (server.py line 5120). `calculate_duration_matrix`
            # below is the Mapbox/haversine FALLBACK path; calling it directly
            # silently degraded routing quality whenever a cluster had >25 stops
            # (Mapbox Matrix API limit → haversine straight-line distances), or
            # whenever Mapbox was rate-limited. Now cluster_first gets the same
            # road-aware OSRM data as VROOM/OR-Tools/LKH on the top-level path.
            cluster_matrix = await _osrm_duration_matrix(cluster_stops)
            if not cluster_matrix:
                cluster_matrix = await calculate_duration_matrix(cluster_stops)
        else:
            # `calculate_road_distance_matrix` already tries OSRM first internally
            # (see server.py line 2812), so non-ortools inner algorithms have
            # always had the OSRM-first path. No change needed here.
            cluster_matrix = await calculate_road_distance_matrix(cluster_stops)

        # Determine entry point: closest stop to previous cluster's exit
        entry_local = 0
        if ci == 0:
            # First cluster: find the start stop
            for li, gi in enumerate(cluster_indices):
                if gi == start_index:
                    entry_local = li
                    break
        else:
            prev_stop = stops[previous_exit_global]
            min_d = float("inf")
            for li, gi in enumerate(cluster_indices):
                d = haversine(
                    (prev_stop["latitude"], prev_stop["longitude"]),
                    (stops[gi]["latitude"], stops[gi]["longitude"]),
                    unit=Unit.KILOMETERS,
                )
                if d < min_d:
                    min_d = d
                    entry_local = li

        # Optimize within this cluster using the user's preferred algorithm
        optimized = _run_inner_algorithm(
            cluster_stops, cluster_matrix,
            start_index=entry_local,
            time_limit=cluster_time,
            algorithm=inner_algorithm,
        )

        all_optimized.extend(optimized)

        # Track exit point (last stop in this cluster) for stitching to next cluster
        last_stop = optimized[-1]
        for gi in range(n):
            if stops[gi] is last_stop:
                previous_exit_global = gi
                break

    # Step 5: Global 2-opt pass to fix cross-cluster boundary inefficiencies
    all_optimized = _global_two_opt_pass(all_optimized, max_iterations=3)

    return all_optimized, cluster_info


def build_time_matrix_from_distance(distance_matrix: List[List[float]], avg_speed_kmh: float = 38.0) -> List[List[int]]:
    """Approximate travel-time matrix (seconds) from distance matrix (km)."""
    if avg_speed_kmh <= 0:
        avg_speed_kmh = 38.0

    n = len(distance_matrix)
    time_matrix = [[0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            # seconds = (km / kmh) * 3600
            time_matrix[i][j] = max(1, int((distance_matrix[i][j] / avg_speed_kmh) * 3600))
    return time_matrix


def ortools_optimize(
    stops: List[dict],
    distance_matrix: List[List[float]],
    start_index: int = 0,
    time_limit_seconds: int = 10,
) -> List[dict]:
    """Legacy wrapper — calls ortools_tsp_solve and maps indices back to stops."""
    if len(stops) <= 1:
        return stops
    indices = ortools_tsp_solve(distance_matrix, depot=start_index, time_limit_ms=time_limit_seconds * 1000)
    return [stops[i] for i in indices]


def ortools_tsp_solve(
    matrix: List[List[float]],
    depot: int = 0,
    time_limit_ms: int = 2000,
    initial_indices: List[int] = None,
    locked_order: List[int] = None,
) -> List[int]:
    """
    Solve the Travelling Salesman Problem using Google OR-Tools.

    This is the single, industry-standard solver for route optimization.
    It accepts a Distance/Duration Matrix and returns the optimal visit order.

    ── How it works ──
    1. An (N+1)-node model is created: N real stops + 1 dummy "end" node.
       The dummy end node has zero cost from every real node, giving OR-Tools
       freedom to terminate the route at whichever real stop is cheapest.
       This produces an OPEN-PATH route (start at depot, end anywhere).

    2. First solution: PATH_CHEAPEST_ARC greedily extends the cheapest arc.
    3. Metaheuristic: GUIDED_LOCAL_SEARCH escapes local minima by penalising
       frequently-used arcs, untangling crossed paths and producing routes
       similar to commercial apps like Circuit/Routific.
    4. The solver runs for `time_limit_ms` milliseconds, returning the best
       solution found within that budget.

    ── Mapping matrix indices to front-end stops ──
    1. Build your stops array:
         stops = [current_location] + delivery_stops
       Index 0 = current location (depot), 1..N = delivery stops.
    2. Query Mapbox Matrix API with the coordinates of all stops.
       The returned matrix[i][j] = driving time/distance from stop i to stop j.
       Use duration (seconds) for time-optimal routing.
    3. Call: ordered = ortools_tsp_solve(matrix, depot=0)
    4. Map back: route = [stops[i] for i in ordered]

    Args:
        matrix:        NxN matrix of costs (driving seconds or meters).
                       matrix[i][j] = cost to travel from node i to node j.
                       Populated by the Mapbox Matrix API.
        depot:         Index of the starting node (typically 0 = current location).
        time_limit_ms: Solver time budget in milliseconds (default 2000).
                       2000ms is enough for ≤50 stops. Scale up for larger routes.

    Returns:
        Ordered list of node indices (0..N-1) representing the visit sequence.
        The depot is always first. The route ends at whichever stop minimises
        total cost (open-path TSP).

    Raises:
        RuntimeError: If OR-Tools is not installed.
        ValueError:   If no solution is found.
    """
    if not ORTOOLS_AVAILABLE or pywrapcp is None or routing_enums_pb2 is None:
        raise RuntimeError(f"OR-Tools not available: {ORTOOLS_IMPORT_ERROR or 'import failed'}")

    n = len(matrix)
    if n <= 1:
        return list(range(n))
    if n == 2:
        return [depot, 1 - depot]

    safe_depot = depot if 0 <= depot < n else 0

    # ── Build (N+1)-node model with dummy end node for open-path TSP ──
    #
    # Node indices 0..n-1 are real stops.
    # Node n is a dummy "end" node: cost FROM any real node TO dummy = 0,
    # cost FROM dummy TO any real node = very large (never used as source).
    # The vehicle starts at `safe_depot` and ends at node `n` (the dummy).
    # Since travelling to the dummy is free, OR-Tools ends at whichever
    # real stop produces the shortest total route.
    N = n + 1  # total nodes including dummy
    DUMMY = n
    LARGE = 10**9  # prohibitive cost — dummy is never a real origin

    # Scale matrix values to integers (OR-Tools requires int callbacks).
    # If the matrix contains floats (km), multiply by 1000 to preserve
    # three decimal places. If already in seconds (int), use as-is.
    scale = 1000 if any(isinstance(matrix[i][j], float) for i in range(min(2, n)) for j in range(min(2, n))) else 1

    # Build the raw n×n integer matrix first (vectorised via NumPy).
    import numpy as _np
    int_nxn = _np.asarray(matrix, dtype=_np.float64) * scale
    _np.clip(int_nxn, 0, None, out=int_nxn)
    int_nxn = int_nxn.astype(_np.int64, copy=False)

    # ── Matrix sparsification (single-driver, large-N only) ──
    # For large routes, clamp geographically absurd arcs to a large penalty so
    # OR-Tools never routes through them. Keeps all nodes reachable via the
    # depot (sparsify_matrix preserves depot row + col), preserves optimality
    # on real-world delivery data, and shrinks the effective search space.
    # Skipped when `locked_order` is set: forcing the locked sequence may
    # legitimately require an arc that sparsification would have pruned, so we
    # keep the full matrix to guarantee precedence feasibility.
    if n >= 20 and not locked_order:
        try:
            from vrp_solver import sparsify_matrix
            nonzero = int_nxn[int_nxn > 0]
            if nonzero.size > 0:
                threshold = int(3 * _np.median(nonzero))
                int_nxn, _n_pruned = sparsify_matrix(
                    int_nxn, prune_threshold_s=threshold, keep_depot=safe_depot
                )
        except Exception as _e:
            logger.warning(f"Matrix sparsification skipped (non-fatal): {_e}")

    # ── Expand to (N+1)×(N+1) with dummy-end-node scaffolding ──
    int_matrix = [[0] * N for _ in range(N)]
    for i in range(n):
        row = int_nxn[i]
        for j in range(n):
            int_matrix[i][j] = int(row[j])
        int_matrix[i][DUMMY] = 0      # free to end the route here
    for j in range(N):
        int_matrix[DUMMY][j] = LARGE   # dummy is never a real origin
    int_matrix[DUMMY][DUMMY] = 0

    # ── OR-Tools model ──
    manager = pywrapcp.RoutingIndexManager(N, 1, [safe_depot], [DUMMY])
    routing = pywrapcp.RoutingModel(manager)

    def cost_callback(from_index: int, to_index: int) -> int:
        return int_matrix[manager.IndexToNode(from_index)][manager.IndexToNode(to_index)]

    transit_idx = routing.RegisterTransitCallback(cost_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_idx)

    # Add cumulative dimension to track total cost (for diagnostics / constraints)
    routing.AddDimension(transit_idx, 0, LARGE, True, "Cost")

    # ── Late Freight precedence constraints ──
    # `locked_order` is a list of REAL node indices that MUST be visited in
    # this exact relative order (their immutable Sharpie `original_sequence`).
    # We add a unary "Position" dimension (cost 1 per arc) so each node's
    # CumulVar equals its 0-based visit position, then constrain
    # position(locked[k]) <= position(locked[k+1]) for every consecutive
    # locked pair. Unlocked "late freight" nodes carry no such constraint, so
    # PATH_CHEAPEST_ARC + GUIDED_LOCAL_SEARCH slot them into the cheapest gaps
    # freely. This never mutates any stop's `original_sequence` value.
    if locked_order and len(locked_order) >= 2:
        def _unit_callback(from_index: int, to_index: int) -> int:
            return 1
        unit_idx = routing.RegisterTransitCallback(_unit_callback)
        routing.AddDimension(unit_idx, 0, N, True, "Position")
        position_dim = routing.GetDimensionOrDie("Position")
        solver = routing.solver()
        for a, b in zip(locked_order, locked_order[1:]):
            if 0 <= a < n and 0 <= b < n:
                solver.Add(
                    position_dim.CumulVar(manager.NodeToIndex(a))
                    <= position_dim.CumulVar(manager.NodeToIndex(b))
                )

    # ── Search strategy ──
    search_params = pywrapcp.DefaultRoutingSearchParameters()
    search_params.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    search_params.time_limit.FromMilliseconds(max(500, int(time_limit_ms)))

    # ── Warm-start: inject VROOM initial solution if provided ──
    solution = None
    if initial_indices and len(initial_indices) >= 2:
        try:
            # Strip depot from head — OR-Tools expects only the intermediate nodes
            warm_route = [i for i in initial_indices if i != safe_depot]
            initial_assignment = routing.ReadAssignmentFromRoutes([warm_route], True)
            if initial_assignment:
                # With a warm-start, skip greedy construction — jump straight to GLS
                search_params.first_solution_strategy = (
                    routing_enums_pb2.FirstSolutionStrategy.FIRST_UNBOUND_MIN_VALUE
                )
                solution = routing.SolveFromAssignmentWithParameters(
                    initial_assignment, search_params
                )
        except Exception:
            pass  # Fall through to cold-start below

    if not solution:
        # Cold-start: PATH_CHEAPEST_ARC greedy seed, then GLS improvement
        search_params.first_solution_strategy = (
            routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
        )
        solution = routing.SolveWithParameters(search_params)
    if not solution:
        raise ValueError("OR-Tools could not find a route solution")

    # ── Extract ordered real-node indices (exclude dummy end) ──
    ordered: List[int] = []
    index = routing.Start(0)
    while not routing.IsEnd(index):
        node = manager.IndexToNode(index)
        if node != DUMMY:
            ordered.append(node)
        index = solution.Value(routing.NextVar(index))

    # Safety: ensure every real node appears exactly once
    seen = set(ordered)
    for i in range(n):
        if i not in seen:
            ordered.append(i)

    return ordered

def _smart_insertion_fallback(
    stops: List[dict],
    matrix: List[List[float]],
    start_index: int,
    locked_order: List[int],
) -> List[dict]:
    """Deterministic late-freight insertion when the OR-Tools solver fails.

    Builds the base route from the depot followed by the locked stops in
    their immutable `original_sequence` order, then cheapest-inserts each
    unlocked "late freight" stop into the gap that adds the least travel
    cost (open-path, so appending at the end costs only the inbound leg).
    Never mutates `original_sequence` values.
    """
    n = len(stops)
    locked_set = set(locked_order)
    base: List[int] = []
    if start_index not in locked_set:
        base.append(start_index)
    base.extend(locked_order)
    late = [i for i in range(n) if i != start_index and i not in locked_set]
    for node in late:
        best_pos, best_delta = len(base), float("inf")
        for pos in range(1, len(base) + 1):
            prev = base[pos - 1]
            if pos < len(base):
                nxt = base[pos]
                delta = matrix[prev][node] + matrix[node][nxt] - matrix[prev][nxt]
            else:
                delta = matrix[prev][node]  # append at end (open path)
            if delta < best_delta:
                best_delta, best_pos = delta, pos
        base.insert(best_pos, node)
    return [stops[i] for i in base]


def nearest_neighbor_optimize(stops: List[dict], distance_matrix: List[List[float]], start_index: int = 0) -> List[dict]:
    """Basic nearest neighbor optimization - greedy approach"""
    if len(stops) <= 1:
        return stops
    
    n = len(stops)
    visited = [False] * n
    route = [start_index]
    visited[start_index] = True
    
    for _ in range(n - 1):
        current = route[-1]
        nearest = -1
        nearest_dist = float('inf')
        
        for j in range(n):
            if not visited[j] and distance_matrix[current][j] < nearest_dist:
                nearest = j
                nearest_dist = distance_matrix[current][j]
        
        if nearest != -1:
            route.append(nearest)
            visited[nearest] = True
    
    return [stops[i] for i in route]

def calculate_route_distance(route: List[int], matrix: List[List[float]]) -> float:
    """Sum of edge costs along a route (list of indices into the cost matrix)."""
    return sum(matrix[route[i]][route[i + 1]] for i in range(len(route) - 1))


# ─── Greedy fallback (Nearest Neighbor with super-node clustering) ──────
def _nearest_neighbor_indices(
    matrix: Sequence[Sequence[float]],
    depot: int = 0,
    **_kwargs: object,
) -> List[int]:
    """Pure index-space NN. Picks the `min` outgoing edge from the current
    node, ignoring already-visited indices. O(n²) — no warm-starts, no
    randomness, fully deterministic.

    `**_kwargs` swallows extra args so this can be passed straight to
    ``cluster_aware_solve`` (which forwards solver kwargs verbatim)."""
    n = len(matrix)
    if n == 0:
        return []
    if n == 1:
        return [depot]
    visited = [False] * n
    route = [depot]
    visited[depot] = True
    for _ in range(n - 1):
        current = route[-1]
        best_idx = -1
        best_cost = float("inf")
        row = matrix[current]
        for j in range(n):
            if visited[j]:
                continue
            c = row[j]
            if c < best_cost:
                best_cost = c
                best_idx = j
        if best_idx < 0:
            break
        route.append(best_idx)
        visited[best_idx] = True
    return route


def solve_nearest_neighbor(
    distance_matrix: Sequence[Sequence[float]],
    stops: List[dict],
    start_index: int = 0,
) -> List[dict]:
    """Bulletproof greedy fallback for the routing pipeline.

    Pipeline:
      1. Wrap the index-space NN in ``cluster_aware_solve`` so identical-
         coordinate "super nodes" (multi-parcel doorsteps) are collapsed
         before the solver runs and re-expanded sequentially after — same
         protection PyVRP gets internally. Prevents the "Zero-Cost
         Interleaving" bug where the greedy picks A1 → B → A2 because
         the inter-parcel edge cost is 0.
      2. If the matrix degenerates (empty, no stops, identical depot) the
         function falls back to returning the input list unchanged.

    Why a wrapper around the existing ``nearest_neighbor_optimize``:
        ``nearest_neighbor_optimize`` works in stop-dict space and can't
        be passed to ``cluster_aware_solve`` directly. ``_nearest_neighbor_indices``
        is the index-space twin that integrates with the cluster pipeline.
        Returning ``List[dict]`` here matches every other top-level solver
        in this file (PyVRP, ALNS, OR-Tools, etc.) so the call sites are
        drop-in-compatible.

    Args:
        distance_matrix: square matrix in seconds (or any cost). Driver-
            provided OSRM/Mapbox `duration_matrix` is the right input.
        stops: list of stop dicts with `latitude`/`longitude`.
        start_index: depot index (driver location), default 0.
    """
    if not stops or len(stops) == 1:
        return list(stops)
    indices = cluster_aware_solve(
        _nearest_neighbor_indices,
        distance_matrix,
        start_index,
        stops,
    )
    return [stops[i] for i in indices]


def _indices_by_identity(source_list: List[dict], ordered: List[dict]) -> List[int]:
    """Map each dict in `ordered` back to its position in `source_list` using
    Python object identity (`id()`), not equality.

    Why: every pre-existing call site used ``[source_list.index(s) for s in ordered]``,
    which returns the FIRST equal dict. For users with duplicate-address stops
    (same lat/lng, different stop ids) that silently collapses two different
    stops onto the same index → the optimizer output loses a real stop.

    Since every solver in this file returns the same dict *references* that
    were passed in (see e.g. ``nearest_neighbor_optimize``: ``return [stops[i] for i in route]``),
    `id()` identifies each dict uniquely regardless of duplicate values.
    """
    id_map = {id(item): idx for idx, item in enumerate(source_list)}
    return [id_map[id(item)] for item in ordered]


def two_opt_improve(route_indices: List[int], distance_matrix: List[List[float]]) -> List[int]:
    """2-Opt improvement — asymmetric-matrix-safe.

    Standard 2-opt reverses the segment between two cut edges. On a symmetric
    matrix every internal edge has the same cost in both directions so the
    boundary-only delta formula (d1+d2 vs d3+d4) is correct. On an asymmetric
    matrix (OSRM one-way streets, turn restrictions) reversing a segment flips
    every internal edge's direction, changing its cost — the boundary-only
    formula accepts moves that only *look* cheaper and can produce longer routes.

    Fix: measure the full cost of the affected path slice before and after
    reversal (O(segment) per evaluation). Only accept if the total genuinely
    decreases. Identical to the fix already applied to three_opt_improve.
    """
    improved = True
    best = route_indices[:]
    n = len(best)

    while improved:
        improved = False
        for i in range(1, n - 1):
            for j in range(i + 1, n):
                # ── Cost of current path slice: best[i-1] → … → best[j] ──
                cur = distance_matrix[best[i - 1]][best[i]]
                for k in range(i, j - 1):
                    cur += distance_matrix[best[k]][best[k + 1]]
                if j < n:
                    cur += distance_matrix[best[j - 1]][best[j]]

                # ── Cost after reversing best[i:j] ──
                # New path: best[i-1] → best[j-1] → best[j-2] → … → best[i] → best[j]
                rev = distance_matrix[best[i - 1]][best[j - 1]]
                for k in range(j - 1, i, -1):
                    rev += distance_matrix[best[k]][best[k - 1]]
                if j < n:
                    rev += distance_matrix[best[i]][best[j]]

                if cur > rev:
                    best[i:j] = reversed(best[i:j])
                    improved = True

    return best

def iterated_local_search(
    stops: List[dict],
    cost_matrix: List[List[float]],
    start_index: int = 0,
    time_limit_seconds: float = 10.0,
) -> List[dict]:
    """Iterated Local Search with double-bridge perturbation.

    Significantly outperforms SA/GA because:
    - Uses structured double-bridge kicks (not random swaps) to escape local minima
    - Applies Or-Opt + 2-opt after every perturbation (deep local search)
    - Accepts only improving moves (no random acceptance) → always moves toward better solutions

    Time complexity: O(n^2) per local search pass × number of restarts in time budget.
    """
    import time
    import random

    n = len(stops)
    if n <= 3:
        return stops

    def _local_search(route: List[int]) -> List[int]:
        """2-opt + Or-Opt pass until no improvement."""
        r = two_opt_improve(route, cost_matrix)
        r = or_opt_improve(r, cost_matrix)
        return r

    def _double_bridge(route: List[int]) -> List[int]:
        """Double-bridge 4-opt move: split into A|B|C|D → A|C|B|D.
        Keeps depot fixed at position 0. Creates crossings that 2-opt cannot undo,
        enabling escape from deep local minima."""
        if len(route) < 6:
            # Not enough nodes for a meaningful double-bridge — do a segment reversal instead
            i, j = sorted(random.sample(range(1, len(route)), 2))
            r = route[:]
            r[i:j] = reversed(r[i:j])
            return r
        # Pick 3 cut points inside the route (after the fixed depot at index 0)
        positions = sorted(random.sample(range(1, len(route)), 3))
        a, b, c = positions
        seg_A = route[:a]
        seg_B = route[a:b]
        seg_C = route[b:c]
        seg_D = route[c:]
        return seg_A + seg_C + seg_B + seg_D

    # Seed: nearest-neighbour → local search
    nn_result = nearest_neighbor_optimize(stops, cost_matrix, start_index)
    current = _local_search(_indices_by_identity(stops, nn_result))
    best = current[:]
    best_cost = calculate_route_distance(best, cost_matrix)

    deadline = time.monotonic() + time_limit_seconds
    restarts = 0
    while time.monotonic() < deadline:
        candidate = _local_search(_double_bridge(current[:]))
        candidate_cost = calculate_route_distance(candidate, cost_matrix)
        # Always accept improvements; keep best ever seen
        if candidate_cost < calculate_route_distance(current, cost_matrix):
            current = candidate
        if candidate_cost < best_cost:
            best = candidate[:]
            best_cost = candidate_cost
        restarts += 1

    return [stops[i] for i in best]


def three_opt_improve(route_indices: List[int], cost_matrix: List[List[float]], max_iterations: int = 5) -> List[int]:
    """3-Opt improvement — non-reversal segment swap (asymmetric-safe).

    On an open-path tour we want to escape 2-opt local optima without breaking
    on directed-graph cost matrices. The classic textbook 3-opt enumerates 7
    reconnections, six of which REVERSE one or both inner segments
    (`A + B[::-1] + C + D`, `A + C[::-1] + B + D`, etc.). When the cost
    matrix is asymmetric (OSRM's one-way streets, turn restrictions) reversing
    a segment changes every internal edge cost — but the textbook delta-cost
    formula only re-prices the 3 boundary edges and assumes internal costs
    are unchanged. The result: 3-opt accepts moves that LOOK cheaper than
    they actually are, occasionally producing worse tours than its input
    (the symptom: zig-zags and "doubling back" past a stop the route already
    passed). We saw this in production with stops 11→12→13→14 doubling back.

    Fix: keep only the ONE 3-opt candidate that doesn't reverse any segment:
    `A + C + B + D` (swap segments B and C, preserving their internal
    direction). Its boundary-delta cost is correct on any matrix, symmetric
    or not. We lose some search power (no reversal escapes) but every move
    we DO accept is guaranteed to be a real improvement.

    The first node (depot) is held fixed.
    """
    best = route_indices[:]
    n = len(best)
    if n < 5:
        return best

    for _ in range(max_iterations):
        improved = False
        for i in range(1, n - 3):
            for j in range(i + 1, n - 2):
                for k in range(j + 1, n - 1):
                    # Segments: A = best[:i], B = best[i:j], C = best[j:k], D = best[k:]
                    A_last = best[i - 1]
                    B_first, B_last = best[i], best[j - 1]
                    C_first, C_last = best[j], best[k - 1]
                    D_first = best[k]

                    # Old boundary edges removed by the move.
                    d0 = (cost_matrix[A_last][B_first]
                          + cost_matrix[B_last][C_first]
                          + cost_matrix[C_last][D_first])

                    # Non-reversing swap: tour becomes A + C + B + D, with
                    # internal edges of B and C unchanged. Delta is exact
                    # on any (a)symmetric matrix because no edge inside B
                    # or C is altered — only the 3 join edges change.
                    d_new = (cost_matrix[A_last][C_first]
                             + cost_matrix[C_last][B_first]
                             + cost_matrix[B_last][D_first])

                    if d_new < d0:
                        best = best[:i] + best[j:k] + best[i:j] + best[k:]
                        improved = True

        if not improved:
            break

    return best


def or_opt_improve(route_indices: List[int], cost_matrix: List[List[float]], max_iterations: int = 10) -> List[int]:
    """Or-opt improvement — relocate sequences of 1, 2, or 3 consecutive stops.

    For each segment size (3, 2, 1), tries removing the segment from its
    current position and re-inserting it at every other position in the route.
    Accepts the move if total cost decreases. Repeats until no improvement
    found or max_iterations reached.

    Catches "misplaced cluster" improvements that 3-opt and LKH may miss.
    Runs in O(n^2) per pass per segment size. Keeps first node (depot) fixed.
    """
    best = route_indices[:]
    n = len(best)
    if n < 4:
        return best

    def _total_cost(route):
        return sum(cost_matrix[route[k]][route[k + 1]] for k in range(len(route) - 1))

    for _ in range(max_iterations):
        improved = False
        # Try segment sizes 3, 2, 1 (larger segments first for bigger wins)
        for seg_len in (3, 2, 1):
            if n < seg_len + 2:
                continue
            for i in range(1, n - seg_len):  # skip depot at index 0
                # Extract the segment
                segment = best[i:i + seg_len]
                # Build route without the segment
                rest = best[:i] + best[i + seg_len:]

                # Cost of current route around the removal point
                # Edges removed: (i-1 -> i), (i+seg_len-1 -> i+seg_len)
                # Edge added:    (i-1 -> i+seg_len)
                old_removal_cost = (
                    cost_matrix[best[i - 1]][best[i]] +
                    cost_matrix[best[i + seg_len - 1]][best[i + seg_len]] if (i + seg_len) < n else
                    cost_matrix[best[i - 1]][best[i]]
                )
                new_removal_cost = (
                    cost_matrix[best[i - 1]][best[i + seg_len]] if (i + seg_len) < n else 0
                )
                removal_delta = new_removal_cost - old_removal_cost

                # Try inserting the segment at every valid position in `rest`
                best_delta = 0
                best_insert_pos = -1
                for j in range(1, len(rest)):  # skip inserting before depot
                    # Edge being broken: rest[j-1] -> rest[j]
                    # Edges being added: rest[j-1] -> segment[0], segment[-1] -> rest[j]
                    old_insert_cost = cost_matrix[rest[j - 1]][rest[j]]
                    new_insert_cost = (
                        cost_matrix[rest[j - 1]][segment[0]] +
                        cost_matrix[segment[-1]][rest[j]]
                    )
                    # Internal segment cost stays the same, so only edge changes matter
                    insert_delta = new_insert_cost - old_insert_cost
                    total_delta = removal_delta + insert_delta

                    if total_delta < best_delta - 1e-9:
                        best_delta = total_delta
                        best_insert_pos = j

                if best_insert_pos >= 0:
                    # Apply the best move
                    best = rest[:best_insert_pos] + segment + rest[best_insert_pos:]
                    improved = True
                    break  # restart from scratch after each improvement
            if improved:
                break  # restart outer loop

        if not improved:
            break

    return best

def simulated_annealing_optimize(stops: List[dict], distance_matrix: List[List[float]], 
                                  start_index: int = 0, iterations: int = 10000) -> List[dict]:
    """Simulated Annealing optimization - probabilistic meta-heuristic"""
    import random
    import math
    
    n = len(stops)
    if n <= 2:
        return stops
    
    # Start with nearest neighbor solution
    current = list(range(n))
    if start_index != 0:
        current.remove(start_index)
        current = [start_index] + current
    
    current_dist = calculate_route_distance(current, distance_matrix)
    best = current[:]
    best_dist = current_dist
    
    temperature = 100.0
    cooling_rate = 0.9995
    
    for _ in range(iterations):
        # Generate neighbor by swapping two random positions (keep start fixed)
        i, j = random.sample(range(1, n), 2)
        neighbor = current[:]
        neighbor[i], neighbor[j] = neighbor[j], neighbor[i]
        
        neighbor_dist = calculate_route_distance(neighbor, distance_matrix)
        delta = neighbor_dist - current_dist
        
        # Accept better solutions or worse with probability
        if delta < 0 or random.random() < math.exp(-delta / temperature):
            current = neighbor
            current_dist = neighbor_dist
            
            if current_dist < best_dist:
                best = current[:]
                best_dist = current_dist
        
        temperature *= cooling_rate
    
    return [stops[i] for i in best]

def genetic_algorithm_optimize(stops: List[dict], distance_matrix: List[List[float]], 
                               start_index: int = 0, generations: int = 200, 
                               population_size: int = 50) -> List[dict]:
    """Genetic Algorithm optimization - evolutionary meta-heuristic"""
    import random
    
    n = len(stops)
    if n <= 2:
        return stops
    
    def create_individual():
        """Create a random route keeping start_index first"""
        route = list(range(n))
        route.remove(start_index)
        random.shuffle(route)
        return [start_index] + route
    
    def fitness(individual):
        """Lower distance = higher fitness"""
        return 1.0 / (1.0 + calculate_route_distance(individual, distance_matrix))
    
    def crossover(parent1, parent2):
        """Order crossover (OX)"""
        size = len(parent1)
        start, end = sorted(random.sample(range(1, size), 2))
        
        child = [None] * size
        child[0] = start_index
        child[start:end] = parent1[start:end]
        
        remaining = [x for x in parent2 if x not in child]
        idx = 0
        for i in range(size):
            if child[i] is None:
                child[i] = remaining[idx]
                idx += 1
        
        return child
    
    def mutate(individual, rate=0.1):
        """Swap mutation"""
        if random.random() < rate and len(individual) > 2:
            i, j = random.sample(range(1, len(individual)), 2)
            individual[i], individual[j] = individual[j], individual[i]
        return individual
    
    # Initialize population
    population = [create_individual() for _ in range(population_size)]
    
    for _ in range(generations):
        # Selection (tournament)
        new_population = []
        
        # Elitism - keep best
        population.sort(key=fitness, reverse=True)
        new_population.append(population[0][:])
        
        while len(new_population) < population_size:
            # Tournament selection
            tournament = random.sample(population, 5)
            parent1 = max(tournament, key=fitness)
            tournament = random.sample(population, 5)
            parent2 = max(tournament, key=fitness)
            
            child = crossover(parent1, parent2)
            child = mutate(child)
            new_population.append(child)
        
        population = new_population
    
    # Return best individual
    best = max(population, key=fitness)
    return [stops[i] for i in best]

def clarke_wright_savings(stops: List[dict], distance_matrix: List[List[float]], 
                          depot_index: int = 0) -> List[dict]:
    """Clarke-Wright Savings Algorithm - classic VRP algorithm
    Treats first stop as depot and builds routes from there"""
    n = len(stops)
    if n <= 2:
        return stops
    
    # Calculate savings for each pair of customers
    savings = []
    for i in range(n):
        if i == depot_index:
            continue
        for j in range(i + 1, n):
            if j == depot_index:
                continue
            # Saving = distance(depot,i) + distance(depot,j) - distance(i,j)
            s = distance_matrix[depot_index][i] + distance_matrix[depot_index][j] - distance_matrix[i][j]
            savings.append((s, i, j))
    
    # Sort by savings (descending)
    savings.sort(reverse=True)
    
    # Build routes
    routes = [[i] for i in range(n) if i != depot_index]
    customer_route = {i: i - (1 if i > depot_index else 0) for i in range(n) if i != depot_index}
    
    for s, i, j in savings:
        route_i = customer_route.get(i)
        route_j = customer_route.get(j)
        
        if route_i is None or route_j is None or route_i == route_j:
            continue
        
        # Check if i and j are at the ends of their routes
        ri = routes[route_i]
        rj = routes[route_j]
        
        if (ri[0] == i or ri[-1] == i) and (rj[0] == j or rj[-1] == j):
            # Merge routes
            if ri[-1] == i and rj[0] == j:
                new_route = ri + rj
            elif ri[0] == i and rj[-1] == j:
                new_route = rj + ri
            elif ri[-1] == i and rj[-1] == j:
                new_route = ri + rj[::-1]
            else:
                new_route = ri[::-1] + rj
            
            # Update routes
            routes[route_i] = new_route
            routes[route_j] = []
            
            # Update customer_route mapping
            for c in new_route:
                customer_route[c] = route_i
    
    # Combine all non-empty routes
    final_route = [depot_index]
    for route in routes:
        if route:
            final_route.extend(route)
    
    return [stops[i] for i in final_route]

async def mapbox_optimize(stops: List[dict], current_latitude: float = None, current_longitude: float = None) -> List[dict]:
    """Use Mapbox Optimization API for route optimization
    
    Mapbox Optimization API handles up to 12 coordinates per request.
    For larger routes, we'll batch them.
    """
    if not MAPBOX_TOKEN:
        raise ValueError("Mapbox token not configured")
    
    if len(stops) < 2:
        return stops
    
    # Build coordinates string - Mapbox wants lon,lat format
    all_coords = []
    
    # Add current location as first point if provided
    if current_latitude and current_longitude:
        all_coords.append(f"{current_longitude},{current_latitude}")
    
    for stop in stops:
        all_coords.append(f"{stop['longitude']},{stop['latitude']}")
    
    # Mapbox Optimization API has a limit of 12 coordinates
    # For larger routes, we batch optimize
    if len(all_coords) <= 12:
        coordinates = ";".join(all_coords)
        
        # Source index determined by current_latitude presence — reserved for
        # future Mapbox calls that explicitly pin a non-first origin.
        _ = 0 if current_latitude else "any"

        params = {
            "access_token": MAPBOX_TOKEN,
            "source": "first",
            "destination": "last",
            "roundtrip": "false",
            "geometries": "geojson",
            "overview": "full"
        }
        
        url = f"https://api.mapbox.com/optimized-trips/v1/mapbox/driving/{coordinates}"
        
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                
                if data.get("code") == "Ok" and data.get("waypoints"):
                    # Extract optimized order from waypoints
                    waypoints = data["waypoints"]
                    
                    # Build reordered stops based on waypoint_index
                    ordered_waypoints = sorted(waypoints, key=lambda w: w["waypoint_index"])
                    
                    # Map back to original stops (skip current location if added)
                    offset = 1 if current_latitude else 0
                    optimized_stops = []
                    
                    for wp in ordered_waypoints:
                        original_idx = wp["waypoint_index"] - offset
                        if original_idx >= 0 and original_idx < len(stops):
                            optimized_stops.append(stops[original_idx])
                    
                    return optimized_stops
            
            # If Mapbox fails, fall back to nearest neighbor
            logger.warning("Mapbox Optimization API error: %s - %s", response.status_code, response.text[:200])
    
    else:
        # For routes with more than 12 stops, batch optimize
        # Split into chunks of 10 (leaving room for start/end)
        chunk_size = 10
        optimized_chunks = []
        
        for i in range(0, len(stops), chunk_size):
            chunk = stops[i:i + chunk_size]
            
            if len(chunk) >= 2:
                coords = ";".join([f"{s['longitude']},{s['latitude']}" for s in chunk])
                
                params = {
                    "access_token": MAPBOX_TOKEN,
                    "roundtrip": "false",
                    "geometries": "geojson"
                }
                
                url = f"https://api.mapbox.com/optimized-trips/v1/mapbox/driving/{coords}"
                
                async with httpx.AsyncClient() as client:
                    response = await client.get(url, params=params, timeout=30)
                    
                    if response.status_code == 200:
                        data = response.json()
                        
                        if data.get("code") == "Ok" and data.get("waypoints"):
                            waypoints = data["waypoints"]
                            ordered_waypoints = sorted(waypoints, key=lambda w: w["waypoint_index"])
                            
                            chunk_optimized = []
                            for wp in ordered_waypoints:
                                if wp["waypoint_index"] < len(chunk):
                                    chunk_optimized.append(chunk[wp["waypoint_index"]])
                            
                            optimized_chunks.extend(chunk_optimized)
                        else:
                            optimized_chunks.extend(chunk)
                    else:
                        optimized_chunks.extend(chunk)
            else:
                optimized_chunks.extend(chunk)
        
        return optimized_chunks
    
    # Fallback: return original order
    return stops


async def generoute_optimize(stops: List[dict], current_latitude: float = None, current_longitude: float = None) -> List[dict]:
    """
    Use Generoute API for route optimization.
    
    Generoute provides simple, fast route optimization via https://api.generoute.io/v1/trip
    
    Args:
        stops: List of stop dictionaries with latitude, longitude, id, address
        current_latitude: Optional starting latitude
        current_longitude: Optional starting longitude
    
    Returns:
        Optimized list of stops
    """
    if not GENEROUTE_API_KEY:
        raise ValueError("Generoute API key not configured")
    
    if len(stops) < 2:
        return stops
    
    # Generoute Free plan limit is 100 locations
    MAX_LOCATIONS = 99  # Leave room for current location
    
    try:
        # If too many stops, chunk them and optimize each chunk
        if len(stops) > MAX_LOCATIONS:
            logger.info(f"Chunking {len(stops)} stops for Generoute (max {MAX_LOCATIONS} per request)")
            
            # Split stops into chunks
            chunks = []
            for i in range(0, len(stops), MAX_LOCATIONS):
                chunks.append(stops[i:i + MAX_LOCATIONS])
            
            # Optimize each chunk
            all_optimized = []
            for chunk_idx, chunk in enumerate(chunks):
                # For subsequent chunks, use last stop of previous chunk as starting point
                chunk_start_lat = None
                chunk_start_lng = None
                if chunk_idx == 0 and current_latitude and current_longitude:
                    chunk_start_lat = current_latitude
                    chunk_start_lng = current_longitude
                elif all_optimized:
                    last_stop = all_optimized[-1]
                    chunk_start_lat = last_stop['latitude']
                    chunk_start_lng = last_stop['longitude']
                
                try:
                    # Recursively call with smaller chunk
                    optimized_chunk = await generoute_optimize(chunk, chunk_start_lat, chunk_start_lng)
                    all_optimized.extend(optimized_chunk)
                except Exception as e:
                    logger.warning(f"Chunk {chunk_idx} optimization failed: {e}, using original order")
                    all_optimized.extend(chunk)
            
            return all_optimized
        
        # Build locations array for Generoute API
        locations = []
        
        # Add current location as first point if provided
        if current_latitude and current_longitude:
            locations.append({
                "coordinates": [current_longitude, current_latitude],
                "title": "Current Location",
                "data": {"id": "current_location"}
            })
        
        # Add all stops
        for stop in stops:
            locations.append({
                "coordinates": [stop['longitude'], stop['latitude']],
                "title": stop.get('address', stop.get('name', '')),
                "data": {"id": stop.get('id', str(uuid.uuid4()))}
            })
        
        # Make API request to Generoute
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.generoute.io/v1/trip",
                headers={
                    "Authorization": f"Bearer {GENEROUTE_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "region": "AU",  # Australia - adjust based on your region
                    "locations": locations
                },
                timeout=30.0
            )
            
            if response.status_code != 200:
                logger.error(f"Generoute API error: {response.status_code} - {response.text}")
                raise ValueError(f"Generoute API error: {response.status_code}")
            
            result = response.json()
            
            # Extract optimized order from response - structure is trips[0].waypoints
            trips = result.get('trips', [])
            if not trips or len(trips) == 0:
                logger.warning("Generoute returned no trips, using original order")
                return stops
            
            optimized_waypoints = trips[0].get('waypoints', [])
            
            if not optimized_waypoints:
                logger.warning("Generoute returned no optimized waypoints, using original order")
                return stops
            
            # Sort by waypoint_order to ensure correct sequence
            optimized_waypoints.sort(key=lambda w: w.get('waypoint_order', 0))
            
            # Reorder stops based on Generoute's optimized sequence
            id_to_stop = {stop.get('id'): stop for stop in stops}
            optimized_stops = []
            
            for opt_wp in optimized_waypoints:
                loc_id = opt_wp.get('data', {}).get('id')
                
                # Skip current location entry
                if loc_id == "current_location":
                    continue
                
                if loc_id and loc_id in id_to_stop:
                    optimized_stops.append(id_to_stop[loc_id])
                else:
                    # Try to match by coordinates
                    opt_coords = opt_wp.get('coordinates', opt_wp.get('waypoint_location', []))
                    if len(opt_coords) == 2:
                        for stop in stops:
                            if stop not in optimized_stops:
                                if abs(stop['longitude'] - opt_coords[0]) < 0.0001 and \
                                   abs(stop['latitude'] - opt_coords[1]) < 0.0001:
                                    optimized_stops.append(stop)
                                    break
            
            # Add any stops that weren't matched
            for stop in stops:
                if stop not in optimized_stops:
                    optimized_stops.append(stop)
            
            logger.info(f"Generoute optimization succeeded: {len(optimized_stops)} stops optimized")
            return optimized_stops
            
    except httpx.TimeoutException:
        logger.error("Generoute API timeout")
        raise ValueError("Generoute API timeout - try again later")
    except Exception as e:
        logger.error(f"Generoute optimization error: {e}")
        raise ValueError(f"Generoute optimization failed: {str(e)}")


# Local delivery constraints — Sugar Bag Rd waypoint injection only.
# (school-zone penalty removed 2026-05-13 per user request; helpers
# `school_penalty_factor` / `apply_school_zone_penalty` / `is_in_school_zone`
# remain available in routes/_route_constraints.py if we want to re-enable.)
from routes._route_constraints import (
    parse_start_time,
    inject_sugar_bag_waypoints,
    needs_sugar_bag_injection,
)


def _traffic_multiplier(hour: int) -> float:
    """Return a duration multiplier based on time-of-day traffic patterns.

    Based on typical Australian urban traffic patterns:
    - AM peak (7-9): 1.35x
    - PM peak (16-18): 1.40x
    - School run (15-16): 1.20x
    - Midday (10-14): 1.05x
    - Early morning (5-7): 1.10x
    - Night (20-5): 1.00x (free flow)
    """
    if 7 <= hour < 9:
        return 1.35
    elif 16 <= hour < 18:
        return 1.40
    elif 15 <= hour < 16:
        return 1.20
    elif 9 <= hour < 10:
        return 1.15
    elif 10 <= hour < 15:
        return 1.05
    elif 5 <= hour < 7:
        return 1.10
    elif 18 <= hour < 20:
        return 1.15
    else:
        return 1.00


def apply_traffic_multiplier(matrix: List[List[int]], hour: int) -> List[List[int]]:
    """Apply time-of-day traffic multiplier to a duration matrix.

    Returns a new matrix with all durations scaled by the traffic factor.
    """
    m = _traffic_multiplier(hour)
    if m == 1.0:
        return matrix
    return [
        [max(1, int(round(cell * m))) for cell in row]
        for row in matrix
    ]


def assign_stops_to_hub_segments(stops: List[dict], hubs: List[dict], current_location: dict = None) -> List[List[dict]]:
    """
    Assign each stop to the nearest hub segment.
    
    The route is divided into segments:
    - Segment 0: From start (current location or first stop) to Hub 1
    - Segment 1: From Hub 1 to Hub 2
    - ...
    - Segment N: From Hub N to end (remaining stops)
    
    Each stop is assigned to the segment whose hub endpoints it's closest to.
    """
    if not hubs:
        return [stops]
    
    # Sort hubs by their order
    sorted_hubs = sorted(hubs, key=lambda h: h['order'])
    
    # Segment boundaries (start_point, end_point) tuples were previously
    # collected here; kept as docstring-only intent since downstream uses
    # the full `waypoints` list instead.
    
    # Build waypoints list: [start] + hubs
    waypoints = []
    if current_location:
        waypoints.append({
            'latitude': current_location['latitude'],
            'longitude': current_location['longitude'],
            'is_hub': False
        })
    
    for hub in sorted_hubs:
        waypoints.append({
            'latitude': hub['latitude'],
            'longitude': hub['longitude'],
            'is_hub': True,
            'hub_id': hub['id']
        })
    
    # Create segments (N hubs = N+1 segments if we have start location, else N segments)
    num_segments = len(sorted_hubs) + (1 if current_location else 0)
    segments = [[] for _ in range(num_segments)]
    
    # Assign each stop to the best segment based on proximity to segment endpoints
    for stop in stops:
        stop_coord = (stop['latitude'], stop['longitude'])
        
        best_segment = 0
        best_score = float('inf')
        
        for seg_idx in range(num_segments):
            # Calculate which segment this stop fits best
            # Use distance to the segment's "center" or endpoints
            
            if seg_idx < len(waypoints):
                # Distance to the segment start waypoint
                start_wp = waypoints[seg_idx]
                start_coord = (start_wp['latitude'], start_wp['longitude'])
                dist_to_start = haversine(stop_coord, start_coord, unit=Unit.KILOMETERS)
                
                # For segments with a next waypoint, also consider distance to end
                if seg_idx + 1 < len(waypoints):
                    end_wp = waypoints[seg_idx + 1]
                    end_coord = (end_wp['latitude'], end_wp['longitude'])
                    dist_to_end = haversine(stop_coord, end_coord, unit=Unit.KILOMETERS)
                    score = min(dist_to_start, dist_to_end)
                else:
                    # Last segment - just use distance to the last hub
                    score = dist_to_start
            else:
                # Fallback for edge case
                score = float('inf')
            
            if score < best_score:
                best_score = score
                best_segment = seg_idx
        
        segments[best_segment].append(stop)
    
    return segments


def optimize_segment(stops: List[dict], algorithm: str, start_point: dict = None, end_point: dict = None) -> List[dict]:
    """
    Optimize a single segment of stops.
    
    Args:
        stops: List of stops in this segment
        algorithm: Optimization algorithm to use
        start_point: Optional fixed start point (hub or current location)
        end_point: Optional fixed end point (next hub)
    
    Returns:
        Optimized list of stops for this segment
    """
    if len(stops) <= 1:
        return stops
    
    # Build the list with optional start/end anchors
    working_stops = []
    start_idx = 0
    
    if start_point:
        anchor_start = {
            'id': f"anchor_start_{start_point.get('id', 'loc')}",
            'latitude': start_point['latitude'],
            'longitude': start_point['longitude'],
            'is_anchor': True
        }
        working_stops.append(anchor_start)
        start_idx = 0
    
    working_stops.extend(stops)
    
    if end_point:
        anchor_end = {
            'id': f"anchor_end_{end_point.get('id', 'loc')}",
            'latitude': end_point['latitude'],
            'longitude': end_point['longitude'],
            'is_anchor': True
        }
        working_stops.append(anchor_end)
    
    # Calculate distance matrix for this segment
    distance_matrix = calculate_distance_matrix(working_stops)
    
    # Apply optimization algorithm
    if algorithm == 'alns':
        try:
            optimized = alns_hybrid_optimize(
                working_stops,
                distance_matrix,
                start_index=start_idx,
                time_limit_seconds=6,
            )
        except Exception as exc:
            logger.warning("ALNS segment optimization failed, using 2-Opt fallback: %s", exc)
            nn_result = nearest_neighbor_optimize(working_stops, distance_matrix, start_idx)
            route_indices = _indices_by_identity(working_stops, nn_result)
            improved_indices = two_opt_improve(route_indices, distance_matrix)
            optimized = [working_stops[i] for i in improved_indices]
    elif algorithm == 'ortools':
        try:
            optimized = ortools_optimize(
                working_stops,
                distance_matrix,
                start_index=start_idx,
                time_limit_seconds=8,
            )
        except Exception as exc:
            logger.warning("OR-Tools segment optimization failed, using 2-Opt fallback: %s", exc)
            nn_result = nearest_neighbor_optimize(working_stops, distance_matrix, start_idx)
            route_indices = _indices_by_identity(working_stops, nn_result)
            improved_indices = two_opt_improve(route_indices, distance_matrix)
            optimized = [working_stops[i] for i in improved_indices]
    elif algorithm in ['two_opt', 'auto'] or len(working_stops) <= 10:
        nn_result = nearest_neighbor_optimize(working_stops, distance_matrix, start_idx)
        route_indices = _indices_by_identity(working_stops, nn_result)
        improved_indices = two_opt_improve(route_indices, distance_matrix)
        optimized = [working_stops[i] for i in improved_indices]
    elif algorithm == 'simulated_annealing':
        optimized = simulated_annealing_optimize(working_stops, distance_matrix, start_idx)
    elif algorithm == 'genetic':
        optimized = genetic_algorithm_optimize(working_stops, distance_matrix, start_idx)
    else:
        optimized = nearest_neighbor_optimize(working_stops, distance_matrix, start_idx)
    
    # Remove anchor points from result, return only actual stops
    result = [s for s in optimized if not s.get('is_anchor')]
    return result


# ── POST /api/optimize moved to routes/optimize.py ───────────────────────
# The synchronous optimizer endpoint (optimize_route + _optimize_route_inner)
# lives there now, together with the async job runner, the cluster
# tighteners, /optimize/algorithms and /generoute/status. Wired via
# api_router.include_router(optimize_router) near the other router includes.


# Haversine tighten helpers (_two_opt_pass, _relocate_stop_haversine,
# _iterative_haversine_tighten, _osrm_verify_relocation, …) moved to
# routes/optimize.py and re-imported below for backward-compatible
# `from server import` access.


# Async optimize job runner (/optimize/jobs*, /optimize/diagnostics) moved
# to routes/optimize.py. `_ensure_optimize_jobs_indexes` is re-imported below
# for the startup hook.


# Meta / diagnostics / analytics endpoints (/_meta/build, /_meta/telemetry-rollup,
# /_meta/ml/*) live in routes/meta.py — wired via api_router.include_router(meta_router).

# /optimize/tighten-cluster + /optimize/tighten-clusters moved to
# routes/optimize.py.


# Stops XLSX export endpoint (/stops/export/xlsx) lives in routes/export.py
# — wired via api_router.include_router(export_router).

# /optimize/algorithms moved to routes/optimize.py.

# Algorithm recommendation (/optimize/recommend) lives in routes/optimize_tools.py
# — wired via api_router.include_router(optimize_tools_router).



# ===================== Benchmark & Shadow-Testing =====================
# POST /benchmark + compute_route_quality_metrics/_run_algorithm_benchmark
# moved to routes/benchmark.py — wired via
# api_router.include_router(benchmark_router).


# ===================== Generoute API Endpoints =====================

# /generoute/status moved to routes/optimize.py — wired via
# api_router.include_router(optimize_router).

# Mapbox Proxy Endpoints (/geocode, /directions, /navigation, /mapbox-token)
# live in routes/routing.py — wired via api_router.include_router(routing_router).


# TTS (Text-to-Speech) endpoint (/tts) lives in routes/tts.py — wired via
# api_router.include_router(tts_router) near the other router includes.

# ===================== Health Check =====================

@api_router.get("/")
async def root():
    return {"message": "Circuit Route Optimizer API", "status": "healthy"}

# Map Alerts endpoints (/alerts/*) live in routes/alerts.py — wired in via
# api_router.include_router(alerts_router) near the other router includes.

@api_router.get("/traffic/info")
async def traffic_info():
    """Return current traffic multiplier and schedule."""
    from datetime import datetime, timezone
    now_hour = datetime.now(timezone.utc).hour
    return {
        "current_hour_utc": now_hour,
        "current_multiplier": _traffic_multiplier(now_hour),
        "schedule": {
            "night_free_flow": {"hours": "20:00-05:00", "multiplier": 1.00},
            "early_morning": {"hours": "05:00-07:00", "multiplier": 1.10},
            "am_peak": {"hours": "07:00-09:00", "multiplier": 1.35},
            "post_am_peak": {"hours": "09:00-10:00", "multiplier": 1.15},
            "midday": {"hours": "10:00-15:00", "multiplier": 1.05},
            "school_run": {"hours": "15:00-16:00", "multiplier": 1.20},
            "pm_peak": {"hours": "16:00-18:00", "multiplier": 1.40},
            "post_pm_peak": {"hours": "18:00-20:00", "multiplier": 1.15},
        }
    }

@api_router.get("/cache/stats")
async def cache_stats():
    """Return hit/miss stats for in-memory caches."""
    return {
        "osrm_matrix": _osrm_matrix_cache.stats(),
        "osrm_distance": _osrm_distance_cache.stats(),
        "directions": _directions_cache.stats(),
    }


# Van Layout endpoints (/van-layout) live in routes/van_layout.py — wired via
# api_router.include_router(van_layout_router). ALLOWED_VAN_SHAPES is
# re-imported below for backward-compatible `from server import` access.


@api_router.get("/auth/debug")
async def auth_debug(request: Request):
    """Debug endpoint to check auth configuration and token status."""
    auth_header = request.headers.get("Authorization", "")
    has_token = auth_header.startswith("Bearer ") and len(auth_header) > 10
    
    result = {
        "supabase_configured": bool(SUPABASE_JWT_SECRET),
        "supabase_jwt_secret_length": len(SUPABASE_JWT_SECRET) if SUPABASE_JWT_SECRET else 0,
        "has_auth_header": has_token,
    }
    
    if has_token:
        token = auth_header.split(" ")[1]
        result["token_prefix"] = token[:20] + "..." if len(token) > 20 else token
        result["token_length"] = len(token)
        
        # Try to decode
        payload = _decode_supabase_jwt(token)
        if payload:
            result["jwt_valid"] = True
            result["jwt_email"] = payload.get("email")
            result["jwt_sub"] = payload.get("sub")
        else:
            result["jwt_valid"] = False
            result["jwt_error"] = "Failed to decode - check SUPABASE_JWT_SECRET"
    
    return result

# /api/health, /api/healthz and /api/healthz/version moved to
# routes/health.py — wired via api_router.include_router(health_router).
# Root-level probes (/health, /ready, /live) stay below on `app`.


# Self-hosted building tile endpoints (/api/tiles/buildings/*) live in
# routes/building_tiles.py — wired via api_router.include_router(building_tiles_router).

# ── Parcels + Address tiles moved to routes/tiles.py ───────────────────────
# The /api/tiles/parcels/* and /api/tiles/addresses/* endpoints live in
# routes/tiles.py now. Include them on the shared api_router here so the
# /api prefix is preserved without touching clients.
from routes.tiles import router as tiles_router
api_router.include_router(tiles_router)

from routes.ms_buildings import router as ms_buildings_router
api_router.include_router(ms_buildings_router)

# ── House-number endpoints moved to routes/housenumbers.py ───────────────
# Handles /api/housenumbers and /api/housenumbers/prewarm. Own caches +
# circuit breakers — no shared state with server.py.
from routes.housenumbers import router as housenumbers_router
api_router.include_router(housenumbers_router)

# ── Auth endpoints moved to routes/auth.py ───────────────────────────────
# /api/auth/session, /api/auth/me, /api/auth/logout. The whitelist +
# SIGNUPS_DISABLED flags live in that module; auth.py lazily imports
# db/User/get_current_user/get_session_from_request from this file to avoid
# a circular load.
from routes.auth import router as auth_router
api_router.include_router(auth_router)

# ── Stops CRUD moved to routes/stops.py ─────────────────────────────────
# Covers /api/stops, /api/stops/{id}, /api/stops/clear, /api/stops/reorder,
# /api/stops/{id}/complete, /api/stops/{id}/uncomplete, /api/debug/stops-coords.
# Heavy siblings (regeocode, refresh-suburbs, /car/*, stops/export/xlsx) stay
# in server.py for now. Endpoints lazy-import shared helpers from server.
from routes.stops import router as stops_router
api_router.include_router(stops_router)

# ── No-Go Zones ──────────────────────────────────────────────────────────
# /api/nogo-zones CRUD + matrix penalty integration. Polygons drawn by
# the user (or POSTed via curl for now) get treated as impassable: any
# (A, B) leg whose great-circle line crosses a zone is penalised
# +1e9 seconds in the OSRM duration matrix, so the optimiser will
# never pick a zone-crossing leg unless it has no alternative.
from routes.nogo_zones import router as nogo_zones_router
api_router.include_router(nogo_zones_router)

# ── Map-asset proxy moved to routes/map_assets.py ────────────────────────
# /api/map/style, /api/map/sprites/*, /api/map/fonts/*. Self-hosts the
# Liberty sprite + glyph fetches on our origin so MapLibre can reuse the
# warm HTTP/2 connection instead of a cold TLS handshake to openfreemap.org.
# Backed by the shared disk cache — first request is upstream, every
# subsequent one is a local SQLite read.
from routes.map_assets import router as map_assets_router
api_router.include_router(map_assets_router)

# ── Demo scenario for hackathon judges ───────────────────────────────────
# Public, no-auth GET /api/demo/scenario returning a baked 50-stop
# Sunshine Coast route with full OSRM polyline + headline stats. Lets the
# login screen launch a cinematic flythrough without forcing the judge
# through Google sign-in.
from routes.demo import router as demo_router
api_router.include_router(demo_router)

# ── Stripe billing / Pro paywall ─────────────────────────────────────────
# /api/billing/{status,checkout,portal,webhook}. Owns the subscription
# lifecycle and exports `make_require_pro()` — the dependency that other
# endpoints use to gate Pro features. Indexes are created in the startup
# hook below alongside the rest of the Mongo bootstrap.
from routes.billing import (
    router as billing_router,
    _ensure_indexes as _ensure_billing_indexes,
    make_require_pro,
)
api_router.include_router(billing_router)

# ── Waitlist API for Phase 2 rollout gating ──────────────────────────
# /api/waitlist/{join,status,entries,stats,approve,reject,{id}}. Public
# join + status endpoints, admin CRUD. When SIGNUPS_DISABLED=true in
# routes/auth.py, new users are auto-gated through the waitlist.
from routes.waitlist import (
    router as waitlist_router,
    _ensure_waitlist_indexes,
)
api_router.include_router(waitlist_router)

# ── Late Freight Zipper ───────────────────────────────────────────────
# /api/route/zipper — inserts mid-route parcels into a locked Sharpie run
# without re-ordering the stops the driver has already numbered.
from routes.zipper import router as zipper_router
api_router.include_router(zipper_router)

# ── Map Alerts ────────────────────────────────────────────────────────
# /api/alerts/* — community-reported hazards, cameras, police.
from routes.alerts import router as alerts_router
api_router.include_router(alerts_router)

# ── Route History + Learning ──────────────────────────────────────────
# /api/routes/archive, /api/routes/history/*, /api/routes/stats,
# /api/learn/*, /api/route/preferred-polyline
from routes.route_history import router as route_history_router
api_router.include_router(route_history_router)

# ── XLS / CSV Import ─────────────────────────────────────────────────
# /api/import/preview, /api/import/process, /api/import/jobs/{id}
from routes.import_stops import router as import_stops_router
api_router.include_router(import_stops_router)

# ── Directions / Navigation / Geocode ────────────────────────────────
# /api/geocode, /api/directions, /api/navigation, /api/mapbox-token
# Re-export _extract_steps so routes/route_history.py can lazy-import it
# from the server namespace (used by the preferred-polyline endpoint).
from routes.routing import router as routing_router, _extract_steps, _maneuver_instruction  # noqa: F401,E402
api_router.include_router(routing_router)

# ── TTS (Text-to-Speech) ─────────────────────────────────────────────
# /api/tts — navigation instruction audio via OpenAI TTS.
from routes.tts import router as tts_router
api_router.include_router(tts_router)

# ── Van Layout ───────────────────────────────────────────────────────
# /api/van-layout — per-driver bin-grid configuration.
# Re-export ALLOWED_VAN_SHAPES so `from server import ALLOWED_VAN_SHAPES`
# keeps working (used by tests and any legacy callers).
from routes.van_layout import router as van_layout_router, ALLOWED_VAN_SHAPES  # noqa: F401,E402
api_router.include_router(van_layout_router)

# ── Self-Hosted Building Tiles ───────────────────────────────────────
# /api/tiles/buildings/{z}/{x}/{y}.json, /api/tiles/buildings/metadata
from routes.building_tiles import router as building_tiles_router
api_router.include_router(building_tiles_router)

# ── Stops XLSX Export ────────────────────────────────────────────────
# /api/stops/export/xlsx — styled workbook of all stops, route-ordered.
from routes.export import router as export_router
api_router.include_router(export_router)

# ── Meta / Diagnostics / Analytics ───────────────────────────────────
# /api/_meta/build, /api/_meta/telemetry-rollup, /api/_meta/ml/*
from routes.meta import router as meta_router
api_router.include_router(meta_router)

# ── Optimize-adjacent tools ──────────────────────────────────────────
# /api/optimize/recommend — algorithm recommendation (reads core helpers
# read-only via lazy import-back; no solver-cascade code lives here).
from routes.optimize_tools import router as optimize_tools_router
api_router.include_router(optimize_tools_router)

# ── Geocoding endpoints moved to routes/geocoding.py ─────────────────────
# /stops/{id}/regeocode + /stops/refresh-suburbs.
from routes.geocoding import router as geocoding_router
api_router.include_router(geocoding_router)

# ── Android Auto endpoints moved to routes/car.py ────────────────────────
# /car/stop-action + /car/next-stops.
from routes.car import router as car_router
api_router.include_router(car_router)

# ── Health/readiness endpoints moved to routes/health.py ─────────────────
# /api/health, /api/healthz, /api/healthz/version. Root-level probes
# (/health, /ready, /live) stay in this file — they're bound to `app`.
from routes.health import router as health_router
api_router.include_router(health_router)

# ── Optimization endpoints moved to routes/optimize.py ───────────────────
# /optimize (sync), /optimize/jobs*, /optimize/diagnostics,
# /optimize/tighten-cluster(s), /optimize/algorithms, /generoute/status.
# The helpers are re-imported so existing `from server import X` call sites
# (tests, startup hook) keep working.
from routes.optimize import (  # noqa: F401,E402
    router as optimize_router,
    optimize_route,
    _optimize_route_inner,
    _two_opt_pass,
    _filter_actionable_warnings,
    _haversine_path_km,
    _relocate_stop_haversine,
    _iterative_haversine_tighten,
    _persist_pending_order,
    _osrm_verify_relocation,
    _ensure_optimize_jobs_indexes,
)
api_router.include_router(optimize_router)

# ── Benchmark endpoint moved to routes/benchmark.py ──────────────────────
# POST /benchmark + route-quality metric helpers (re-exported for
# backward-compatible `from server import` access).
from routes.benchmark import (  # noqa: F401,E402
    router as benchmark_router,
    compute_route_quality_metrics,
    _run_algorithm_benchmark,
)
api_router.include_router(benchmark_router)

# Include router
app.include_router(api_router)


# ─────────────────────────────────────────────────────────────────────────
# Public legal pages moved to routes/legal.py (NO /api prefix — included on
# `app` directly so /privacy, /privacy-policy*, /terms stay un-prefixed).
# ─────────────────────────────────────────────────────────────────────────
from routes.legal import router as legal_router
app.include_router(legal_router)


# Build an explicit origin allowlist from the ALLOWED_ORIGINS env var.
# Accepting credentials alongside a wildcard origin is forbidden by the
# CORS spec and exposes cookie-authenticated sessions to any site. The
# allowlist must name every trusted frontend origin exactly.
#
# Format: comma-separated URLs, e.g.
#   ALLOWED_ORIGINS=https://app.getrouted.xyz,https://api.getrouted.xyz
_raw_allowed_origins = os.environ.get("ALLOWED_ORIGINS", "https://api.getrouted.xyz")
_CORS_ALLOWED_ORIGINS: list[str] = [
    o.strip() for o in _raw_allowed_origins.split(",") if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=_CORS_ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── GZip compression ─────────────────────────────────────────────────
# Compress responses > 500 bytes. The big win: optimize results for
# 190 stops (~3 MB JSON) compress to ~300 KB. Drivers on weak 4G
# get results 8-10× faster, eliminating "Network request failed"
# timeouts on the final poll.
from starlette.middleware.gzip import GZipMiddleware
app.add_middleware(GZipMiddleware, minimum_size=500)

@app.on_event("startup")
async def startup_db_client():
    """Verify MongoDB connection and create indexes on startup"""
    max_retries = 8
    retry_delay = 3
    
    for attempt in range(max_retries):
        try:
            await db.command('ping')
            logger.info("MongoDB connection established successfully")
            logger.info(f"Connected to database: {os.environ['DB_NAME']}")
            break
        except Exception as e:
            logger.warning(f"MongoDB connection attempt {attempt + 1}/{max_retries} failed: {str(e)}")
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)
            else:
                logger.error(
                    "Failed to connect to MongoDB after all retries. "
                    "Continuing startup in degraded mode so deployment can complete; "
                    "database-backed endpoints will recover automatically once MongoDB is reachable."
                )
                return
    
    # Create indexes (separate from connection retry loop)
    try:
        # Drop stale unique index if it exists, then create non-unique
        try:
            await db.user_sessions.drop_index("session_token_1")
        except Exception:
            pass
        await db.stops.create_index([("user_id", 1), ("order", 1)], background=True)
        await db.stops.create_index("id", background=True)
        await db.stops.create_index([("user_id", 1), ("completed", 1)], background=True)
        await db.user_sessions.create_index("session_token", background=True)
        await db.user_sessions.create_index("user_id", background=True)
        await db.geocode_cache.create_index("address_query", background=True)
        await db.optimization_hubs.create_index("user_id", background=True)
        await db.map_alerts.create_index("expires_at", background=True)
        await db.route_history.create_index([("user_id", 1), ("archived_at", -1)], background=True)
        await db.route_history.create_index("id", background=True)
        # Async optimize-job store: TTL-purges 10 min after creation; unique
        # job_id keeps `update_one`/`find_one` accurate. Wired here (not on
        # the kickoff hot path) so the user-facing POST is pure-insert.
        await _ensure_optimize_jobs_indexes()
        # Billing: subscriptions + processed_webhook_events collections.
        # Idempotent; safe to re-run on every cold start.
        await _ensure_billing_indexes(db)
        # Waitlist: unique email index + status/created_at for admin queries.
        await _ensure_waitlist_indexes(db)
        logger.info("MongoDB indexes created/verified")
    except Exception as idx_err:
        logger.warning(f"Index creation warning (non-fatal): {idx_err}")

    # Spawn the tile-cache maintenance loop (hourly wal_checkpoint + optimize,
    # daily VACUUM). Imported locally so the circular-import-safe lazy wiring
    # used by routes/auth + stops also applies here.
    try:
        from routes import _tile_cache as _tc
        _tc.start_background_tasks()
        logger.info("tile_cache background maintenance started")
    except Exception as e:
        logger.warning(f"tile_cache maintenance start failed (non-fatal): {e}")

@app.on_event("shutdown")
async def shutdown_db_client():
    """Close MongoDB connection and shared HTTP client on shutdown"""
    try:
        # Close shared HTTP client
        global _shared_http_client
        if _shared_http_client and not _shared_http_client.is_closed:
            await _shared_http_client.aclose()
            logger.info("Shared HTTP client closed")
        client.close()
        logger.info("MongoDB connection closed successfully")
    except Exception as e:
        logger.error(f"Error during shutdown: {str(e)}")

# Root-level probe endpoint for platforms that check GET / directly.
@app.get("/api/test-clusters")
async def test_clusters_page():
    import os
    html_path = os.path.join(os.path.dirname(__file__), "static", "test_clusters.html")
    with open(html_path) as f:
        return HTMLResponse(content=f.read())

@app.get("/api/map-test")
async def map_test_page():
    """Standalone MapLibre camera follow test — verifies jumpTo works outside RN WebView."""
    import os
    html_path = os.path.join(os.path.dirname(__file__), "map-test.html")
    with open(html_path) as f:
        return HTMLResponse(content=f.read())


@app.get("/")
async def root_probe():
    """Lightweight root probe that never depends on MongoDB."""
    return {"status": "ok", "service": "route-optimizer", "probe": "root"}

# Root-level health check for Kubernetes probes (without /api prefix)
@app.get("/health")
@app.head("/health")
async def root_health_check():
    """Root health check endpoint for Kubernetes liveness/readiness probes.
    Supports both GET and HEAD methods for uptime monitoring services."""
    try:
        # Quick ping to verify MongoDB connection
        await db.command('ping')
        return {"status": "ok", "service": "route-optimizer"}
    except Exception as e:
        logger.error(f"Root health check failed: {str(e)}")
        # Return 200 but indicate unhealthy to prevent pod restarts during transient issues
        return {"status": "degraded", "service": "route-optimizer", "error": "database_unavailable"}

@app.get("/ready")
@app.get("/api/ready")
async def readiness_check():
    """Kubernetes readiness probe - checks if app is ready to serve traffic"""
    uptime_seconds = (datetime.now(timezone.utc) - APP_START_TIME).total_seconds()
    try:
        # Verify database is accessible
        await db.command('ping')
        return {"ready": True, "database": "connected"}
    except Exception as e:
        logger.error(f"Readiness check failed: {str(e)}")

        # During initial warm-up window, return 200 degraded to avoid deployment flapping
        if uptime_seconds < DB_READY_GRACE_SECONDS:
            return {
                "ready": False,
                "database": "connecting",
                "status": "warming_up",
                "grace_seconds_remaining": int(DB_READY_GRACE_SECONDS - uptime_seconds),
            }

        return JSONResponse(
            status_code=503,
            content={"ready": False, "database": "disconnected", "error": str(e)}
        )

@app.get("/live")
@app.get("/api/live")
async def liveness_check():
    """Kubernetes liveness probe - checks if app is alive (less strict than readiness)"""
    # Simple check - if the app can respond, it's alive
    return {"alive": True}

# ── Authenticated temporary file download ────────────────────────────
@app.get("/api/download/{token}")
async def download_temp_file(token: str, current_user: User = Depends(get_current_user)):
    """Authenticated download for exported files. Requires a valid session."""
    import os
    from fastapi.responses import FileResponse
    safe_token = "".join(c for c in token if c.isalnum())
    filepath = os.path.join(os.path.dirname(__file__), f"stops_export_{safe_token}.xlsx")
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="File not found or link expired")
    return FileResponse(filepath, filename="stops_export.xlsx",
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

