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


# TSP engine wrappers (VROOM / PyVRP / LKH-3 / elkai) and the shared
# open-path matrix transform moved to solvers/. The wrappers stay
# always-importable (they late-bind the guarded solver libs through this
# module), so these names keep working even when a solver lib is absent.
from solvers.open_path import _open_path_matrix  # noqa: F401,E402
from solvers.vroom import vroom_tsp_solve  # noqa: F401,E402
from solvers.pyvrp_adapter import pyvrp_tsp_solve  # noqa: F401,E402
from solvers.lkh import lkh_tsp_solve  # noqa: F401,E402
from solvers.elkai import elkai_tsp_solve  # noqa: F401,E402



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
# Moved to solvers/clustering.py (geographic DBSCAN, per-cluster inner solve,
# global 2-opt stitch). Re-imported for the call sites below and tests.
from solvers.clustering import (  # noqa: F401,E402
    _adaptive_eps,
    _build_cluster_info,
    _convex_hull,
    _geographic_dbscan,
    _global_two_opt_pass,
    _or_opt_1_improve,
    _order_clusters_tsp,
    _padded_polygon,
    _postprocess_clusters,
    _run_inner_algorithm,
    cluster_first_optimize,
)



# OR-Tools wrapper + smart-insertion fallback moved to solvers/ortools.py.
from solvers.ortools import (  # noqa: F401,E402
    _smart_insertion_fallback,
    build_time_matrix_from_distance,
    ortools_optimize,
    ortools_tsp_solve,
)



# Construction heuristics, local-search operators and metaheuristics moved
# to solvers/heuristics.py, solvers/local_search.py and
# solvers/metaheuristics.py. Re-imported for the call sites below and for
# backward-compatible `from server import` access (tests, routes/*).
from solvers.heuristics import (  # noqa: F401,E402
    _indices_by_identity,
    _nearest_neighbor_indices,
    calculate_route_distance,
    clarke_wright_savings,
    nearest_neighbor_optimize,
    solve_nearest_neighbor,
)
from solvers.local_search import (  # noqa: F401,E402
    or_opt_improve,
    three_opt_improve,
    two_opt_improve,
)
from solvers.metaheuristics import (  # noqa: F401,E402
    genetic_algorithm_optimize,
    iterated_local_search,
    simulated_annealing_optimize,
)

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

