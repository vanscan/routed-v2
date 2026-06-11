from fastapi import FastAPI, APIRouter, HTTPException, Depends, Request, Response, UploadFile, File, Form, Query
from fastapi.responses import JSONResponse, HTMLResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import errno
import logging
import time as _time
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

# ===================== Caches & Matrix Helpers =====================
# TTLCache class, cache instances, OSRM circuit breaker, and all matrix
# builder functions live in utils/matrices.py.  Re-exported here so
# `from server import TTLCache / _directions_cache / …` keeps working.
from utils.matrices import (  # noqa: E402  (after load_dotenv)
    TTLCache,
    _directions_cache,
    _osrm_matrix_cache,
    _osrm_distance_cache,
)

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

# ── OSRM circuit breaker (re-exported from utils.matrices) ────────────
from utils.matrices import (  # noqa: E402
    _osrm_consecutive_failures,
    _osrm_suppress_until,
    _OSRM_FAIL_THRESHOLD,
    _OSRM_SUPPRESS_SECONDS,
    _osrm_enabled,
    _osrm_log_failure,
    _osrm_note_success,
)

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
# All auth logic lives in middleware/auth.py.  Re-exported here so
# `from server import get_current_user / SUPABASE_JWT_SECRET / …` works.
from middleware.auth import (  # noqa: E402
    SUPABASE_JWT_SECRET,
    SUPABASE_URL,
    SUPABASE_ANON_KEY,
    GOOGLE_WEB_CLIENT_ID,
    GOOGLE_ANDROID_CLIENT_ID,
    GOOGLE_IOS_CLIENT_ID,
    _GOOGLE_CLIENT_IDS,
    DEV_MODE,
    DEV_USER,
    _fetch_supabase_jwks,
    _get_supabase_signing_key,
    _decode_google_id_token,
    _get_supabase_jwk_client,
    _decode_supabase_jwt,
    _get_or_create_user_from_supabase,
    get_session_from_request,
    get_current_user,
    get_optional_user,
)

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
# Matrix builders (calculate_road_distance_km, calculate_distance_matrix,
# _mapbox_*_batch, _haversine_duration_matrix, _osrm_cache_key,
# detect_cluster_spikes, _osrm_duration_matrix, _osrm_duration_matrix_for_url,
# _osrm_distance_matrix) live in utils/matrices.py.  Re-exported below.
from utils.matrices import (  # noqa: E402
    calculate_road_distance_km,
    calculate_distance_matrix,
    _mapbox_matrix_batch,
    _mapbox_duration_matrix_batch,
    _haversine_duration_matrix,
    _osrm_cache_key,
    detect_cluster_spikes,
    _osrm_duration_matrix,
    _osrm_duration_matrix_for_url,
    _osrm_distance_matrix,
)


# TSP engine wrappers (VROOM / PyVRP / LKH-3 / elkai) and the shared
# open-path matrix transform moved to solvers/. The wrappers stay
# always-importable (they late-bind the guarded solver libs through this
# module), so these names keep working even when a solver lib is absent.
from solvers.open_path import _open_path_matrix  # noqa: F401,E402
from solvers.vroom import vroom_tsp_solve  # noqa: F401,E402
from solvers.pyvrp_adapter import pyvrp_tsp_solve  # noqa: F401,E402
from solvers.lkh import lkh_tsp_solve  # noqa: F401,E402
from solvers.elkai import elkai_tsp_solve  # noqa: F401,E402


# calculate_duration_matrix, calculate_road_distance_matrix,
# _mapbox_cross_batch_query, and calculate_full_road_distance_matrix
# also live in utils/matrices.py — imported here for backward compat.
from utils.matrices import (  # noqa: E402
    calculate_duration_matrix,
    calculate_road_distance_matrix,
    _mapbox_cross_batch_query,
    calculate_full_road_distance_matrix,
)


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

# Pipeline functions (mapbox_optimize, generoute_optimize, _traffic_multiplier,
# apply_traffic_multiplier, assign_stops_to_hub_segments, optimize_segment)
# live in solvers/pipeline.py.  Re-exported here for backward compat.
from solvers.pipeline import (  # noqa: E402
    mapbox_optimize,
    generoute_optimize,
    _traffic_multiplier,
    apply_traffic_multiplier,
    assign_stops_to_hub_segments,
    optimize_segment,
)


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
            content={"ready": False, "database": "disconnected", "error": "database_unavailable"}
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
    base_dir = os.path.realpath(os.path.dirname(__file__))
    safe_token = "".join(c for c in token if c.isalnum())
    filepath = os.path.realpath(os.path.join(base_dir, f"stops_export_{safe_token}.xlsx"))
    # Containment check — isalnum filtering already prevents traversal, but
    # the realpath prefix check makes that provable (CodeQL py/path-injection).
    if not filepath.startswith(base_dir + os.sep) or not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="File not found or link expired")
    return FileResponse(filepath, filename="stops_export.xlsx",
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

