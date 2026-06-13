"""XLS/XLSX/CSV import endpoints.

    POST /import/preview          → upload file, return columns + sample rows
    POST /import/process          → geocode rows → create stops (async for large files)
    GET  /import/jobs/{job_id}    → poll async import job status

`db`, `get_current_user`, `_OPTIMIZE_RUNNER_TASKS` are lazy-imported from
`server` inside each endpoint. Geocoding helpers are imported from
`routes._geocoding` to avoid duplicating the shared logic.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import uuid
import zipfile
from datetime import datetime, timezone
from typing import Any, Optional

import pandas as pd
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from models import FieldMapping, ImportPreviewResponse, ImportResult, Stop
from routes._geocoding import (
    geocode_address_async,
    extract_suburb_from_address,
    _build_stop_geocode_metadata,
)

logger = logging.getLogger("server")
router = APIRouter()

# Maximum raw upload size accepted for import endpoints (10 MB on the wire).
# A separate row-count guard below catches decompressed OOXML/zip-bomb expansion.
MAX_IMPORT_FILE_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_IMPORT_ROWS = 10_000
MAX_IMPORT_COLS = 100


async def _current_user(request: Request):
    """Dep wrapper — defers the `server` import until the first request."""
    from server import get_current_user  # noqa: WPS433
    return await get_current_user(request)


def parse_excel_file(file_content: bytes, filename: str) -> pd.DataFrame:
    """Parse Excel/CSV file and return DataFrame.

    Uses python-calamine for both .xls (BIFF/OLE2) and .xlsx (ZIP/OOXML)
    files — single engine that handles both formats robustly. Detects the
    actual file format from the magic bytes rather than the extension, so
    mis-named uploads (e.g. an .xlsx renamed to .xls before upload, or
    case-mismatch like .XLS) work correctly. Falls back to extension-based
    routing only when magic bytes are inconclusive (e.g. CSV).

    Hard limits (MAX_IMPORT_ROWS / MAX_IMPORT_COLS) are enforced after
    parsing to guard against decompressed OOXML/zip-bomb expansion."""
    try:
        head = file_content[:8]
        is_xlsx = head[:4] == b"PK\x03\x04"  # ZIP container = .xlsx/.ods/.xlsb
        is_xls = head == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # OLE2 = legacy .xls
        lower = filename.lower()

        if is_xlsx or is_xls:
            # Pre-parse ZIP/OOXML safety gate — runs before pandas decompresses
            # anything, so a zip-bomb payload is rejected before it can expand.
            if is_xlsx:
                _xlsx_max_uncompressed = 50 * 1024 * 1024  # 50 MB decompressed
                _xlsx_max_ratio = 100                       # compression ratio limit
                _xlsx_max_entries = 500                     # entry count limit
                try:
                    with zipfile.ZipFile(io.BytesIO(file_content)) as zf:
                        entries = zf.infolist()
                        if len(entries) > _xlsx_max_entries:
                            raise HTTPException(
                                status_code=400,
                                detail=f"File contains too many archive entries ({len(entries):,}). "
                                       "This file cannot be imported.",
                            )
                        total_uncompressed = sum(e.file_size for e in entries)
                        if total_uncompressed > _xlsx_max_uncompressed:
                            raise HTTPException(
                                status_code=400,
                                detail=f"File expands to {total_uncompressed // (1024*1024):,} MB when "
                                       f"decompressed, which exceeds the {_xlsx_max_uncompressed // (1024*1024)} MB limit.",
                            )
                        for entry in entries:
                            if entry.compress_size > 0:
                                ratio = entry.file_size / entry.compress_size
                                if ratio > _xlsx_max_ratio:
                                    raise HTTPException(
                                        status_code=400,
                                        detail="File has a suspiciously high compression ratio and cannot be imported.",
                                    )
                except HTTPException:
                    raise
                except zipfile.BadZipFile:
                    raise HTTPException(status_code=400, detail="File appears corrupt or is not a valid Excel file.")
                except Exception as zip_err:
                    logger.warning(f"ZIP inspection failed for import: {zip_err}")
            # calamine handles both formats from the in-memory bytes; the
            # extension is irrelevant once we know the magic bytes.
            df = pd.read_excel(io.BytesIO(file_content), engine='calamine')
        elif lower.endswith('.csv'):
            df = pd.read_csv(io.BytesIO(file_content))
        else:
            # Last resort: trust the extension when magic bytes don't match
            # any known Excel format. Surfaces a clearer error than the
            # generic "Unsupported file format" we used to throw.
            raise ValueError(
                f"File '{filename}' is not a recognised Excel or CSV file "
                f"(got header bytes {head[:4].hex()})."
            )

        df.columns = df.columns.str.strip()

        # Guard against decompressed expansion (zip-bomb / unusually wide sheets).
        if len(df) > MAX_IMPORT_ROWS:
            raise HTTPException(
                status_code=400,
                detail=f"File contains too many rows ({len(df):,}). Maximum allowed is {MAX_IMPORT_ROWS:,}.",
            )
        if len(df.columns) > MAX_IMPORT_COLS:
            raise HTTPException(
                status_code=400,
                detail=f"File contains too many columns ({len(df.columns):,}). Maximum allowed is {MAX_IMPORT_COLS:,}.",
            )

        return df
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error parsing file: {e}")
        raise HTTPException(status_code=400, detail=f"Error parsing file: {str(e)}")


@router.post("/import/preview", response_model=ImportPreviewResponse)
async def preview_import(
    file: UploadFile = File(...),
    current_user=Depends(_current_user)
):
    """Upload and preview XLS/XLSX/CSV file - returns columns and sample data"""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    # Check file extension
    allowed_extensions = ['.xls', '.xlsx', '.csv']
    file_ext = '.' + file.filename.split('.')[-1].lower() if '.' in file.filename else ''
    if file_ext not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file format. Allowed: {', '.join(allowed_extensions)}"
        )

    content = await file.read(MAX_IMPORT_FILE_BYTES + 1)
    if len(content) > MAX_IMPORT_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum upload size is {MAX_IMPORT_FILE_BYTES // (1024 * 1024)} MB.",
        )
    df = parse_excel_file(content, file.filename)

    # Get sample rows (first 5) - convert numpy types to native Python types
    sample_df = df.head(5).fillna('')
    sample_rows = []
    for _, row in sample_df.iterrows():
        sample_rows.append({k: str(v) for k, v in row.items()})

    # Server-side auto-mapping: prioritise patterns to avoid POD > Note conflicts
    def _find_col(cols_lower_map, patterns):
        # First pass: exact match wins (so column "Notes" beats "POD Notes"
        # when both contain the substring `notes`; otherwise the user loses
        # the actual driver instructions to the post-delivery POD field).
        for pat in patterns:
            for orig, low in cols_lower_map.items():
                if pat == low:
                    return orig
        # Second pass: substring fallback — picks up columns like
        # "Delivery Address" or "Customer Notes" that still uniquely match.
        for pat in patterns:
            for orig, low in cols_lower_map.items():
                if pat in low:
                    return orig
        return None

    cols_lower = {c: c.lower().replace('_', '').replace(' ', '').replace('-', '') for c in df.columns}
    suggested = {}
    addr = _find_col(cols_lower, ['address', 'location', 'destination', 'deliveryaddress', 'streetaddress', 'fulladdress', 'addr'])
    if addr:
        suggested['address'] = addr
    mob = _find_col(cols_lower, ['mobile', 'phone', 'cell', 'telephone', 'tel', 'phonenumber', 'mobilenumber', 'contact', 'customernumber'])
    if mob:
        suggested['mobile_number'] = mob
    notes = _find_col(cols_lower, ['notes', 'note', 'comments', 'comment', 'instructions', 'instruction', 'remarks', 'description', 'details', 'info', 'pod'])
    if notes:
        suggested['notes'] = notes
    wt = _find_col(cols_lower, ['weight', 'wt', 'kg', 'mass', 'parcelweight', 'packageweight'])
    if wt:
        suggested['weight'] = wt
    qty = _find_col(cols_lower, ['quantity', 'qty', 'count', 'amount', 'items', 'parcels', 'packages', 'units', 'pcs'])
    if qty:
        suggested['quantity'] = qty
    # `sourcereference` matches the user's actual CSV column "Source
    # Reference"; the rest cover the obvious carrier label variants
    # (Tracking, Tracking Number, Barcode, AWB, Consignment, Reference).
    # Lower-case keys here are pre-normalised (spaces/_/- stripped).
    track = _find_col(cols_lower, [
        'sourcereference', 'sourceref', 'tracking', 'trackingnumber', 'trackingno',
        'trackingid', 'barcode', 'awb', 'awbnumber', 'consignment',
        'consignmentnote', 'reference', 'refno', 'shipmentid', 'parcelid',
    ])
    if track:
        suggested['tracking_number'] = track

    return ImportPreviewResponse(
        columns=list(df.columns),
        sample_rows=sample_rows,
        total_rows=int(len(df)),
        suggested_mapping=suggested if suggested else None,
    )


@router.post("/import/process", response_model=ImportResult)
async def process_import(
    file: UploadFile = File(...),
    mapping: str = Form(...),  # JSON string of FieldMapping
    clear_existing: str = Form("false"),  # "true" to clear existing stops before import
    current_user=Depends(_current_user)
):
    """Process XLS import — async job pattern to avoid Cloudflare 520.

    Kicks off a background task that geocodes all rows, then writes stops
    to MongoDB. The frontend polls `/api/import/jobs/{job_id}` until
    `status` is `done` or `error`.

    Also supports the legacy synchronous flow: if the file has ≤20 rows,
    we skip the job pattern and return the result inline (fast enough to
    beat the 100s Cloudflare ceiling).
    """
    from server import db  # noqa: WPS433
    from routes.optimize_jobs import _OPTIMIZE_RUNNER_TASKS  # noqa: WPS433

    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    try:
        mapping_dict = json.loads(mapping)
        field_mapping = FieldMapping(**mapping_dict)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid mapping: {str(e)}")

    content = await file.read(MAX_IMPORT_FILE_BYTES + 1)
    if len(content) > MAX_IMPORT_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum upload size is {MAX_IMPORT_FILE_BYTES // (1024 * 1024)} MB.",
        )
    df = parse_excel_file(content, file.filename)

    if field_mapping.address not in df.columns:
        raise HTTPException(
            status_code=400,
            detail=f"Address column '{field_mapping.address}' not found in file",
        )

    # Small files (<= 20 rows): run synchronously (fast enough for Cloudflare)
    if len(df) <= 20:
        return await _run_import_inner(df, field_mapping, current_user)

    # Large files: async job pattern
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    await db.import_jobs.insert_one({
        "job_id": job_id,
        "user_id": current_user.user_id,
        "status": "running",
        "started_at": now,
        "total_rows": len(df),
        "result": None,
        "error": None,
    })
    logger.info("[import/jobs] kickoff job_id=%s user=%s rows=%d", job_id, current_user.user_id, len(df))

    async def _run_import_job():
        import traceback  # noqa: WPS433
        try:
            result = await _run_import_inner(df, field_mapping, current_user)
            result_dict = result.dict() if hasattr(result, "dict") else result
            # Strip the full stops list from the stored result (too large for Mongo doc)
            if isinstance(result_dict, dict):
                result_dict.pop("stops", None)
            await db.import_jobs.update_one(
                {"job_id": job_id},
                {"$set": {"status": "done", "result": result_dict,
                          "finished_at": datetime.now(timezone.utc)}},
            )
        except Exception as e:
            logger.error("[import/jobs] job %s crashed: %s", job_id, traceback.format_exc())
            await db.import_jobs.update_one(
                {"job_id": job_id},
                {"$set": {"status": "error", "error": str(e),
                          "finished_at": datetime.now(timezone.utc)}},
            )

    task = asyncio.create_task(_run_import_job())
    _OPTIMIZE_RUNNER_TASKS.add(task)
    task.add_done_callback(_OPTIMIZE_RUNNER_TASKS.discard)

    return JSONResponse(
        status_code=202,
        content={"job_id": job_id, "status": "running", "total_rows": len(df)},
    )


@router.get("/import/jobs/{job_id}")
async def get_import_job(job_id: str, current_user=Depends(_current_user)):
    """Poll an async import job."""
    from server import db  # noqa: WPS433
    j = await db.import_jobs.find_one(
        {"job_id": job_id, "user_id": current_user.user_id}, {"_id": 0}
    )
    if not j:
        raise HTTPException(status_code=404, detail="Import job not found")
    return {
        "job_id": job_id,
        "status": j.get("status"),
        "total_rows": j.get("total_rows"),
        "result": j.get("result") if j.get("status") == "done" else None,
        "error": j.get("error") if j.get("status") == "error" else None,
    }


async def _run_import_inner(
    df, field_mapping: FieldMapping, current_user
) -> ImportResult:
    """Core import logic — archives completed stops, geocodes, inserts."""
    from server import db  # noqa: WPS433

    # Always clear existing stops before import (import = new route).
    completed = await db.stops.find(
        {"user_id": current_user.user_id, "completed": True},
        {"_id": 0},
    ).to_list(length=None)
    auto_archived_count = 0
    if completed:
        # Compute the same summary block /api/routes/archive writes so
        # HistoryModal renders these auto-archives identically to
        # explicit user-triggered archives. Previously this writer used
        # a `stats: {stops_count, auto_archived_reason}` shape that the
        # modal couldn't render (it expected `summary.total_stops` etc.)
        # — the user saw an error on first tap of the History icon.
        # 2026-05-12: aligned to the canonical schema.
        delivered_count = sum(1 for s in completed if s.get("completed"))
        skipped_count = sum(1 for s in completed if s.get("delivery_status") == "skipped")
        failed_count = sum(1 for s in completed if s.get("delivery_status") == "failed")
        total_weight = sum(float(s.get("weight_kg") or 0) for s in completed)
        total_quantity = sum(int(s.get("quantity") or 0) for s in completed)
        archived_at_iso = datetime.now(timezone.utc).isoformat()
        # `started_at` heuristic: earliest non-null arrived_at on any
        # completed stop, falling back to earliest created_at, falling
        # back to archived_at. Stops written by different app/backend
        # versions store these as ISO strings OR datetimes (pymongo also
        # returns naive UTC datetimes), and min() over mixed types raises
        # TypeError — which used to 500 the whole import for any user
        # with completed stops. Coerce everything to aware datetimes.
        def _coerce_started_at(value):
            if hasattr(value, "isoformat"):
                dt = value
            else:
                try:
                    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    return None
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt

        candidate_starts = [s.get("arrived_at") for s in completed if s.get("arrived_at")]
        if not candidate_starts:
            candidate_starts = [s.get("created_at") for s in completed if s.get("created_at")]
        coerced_starts = [c for c in (_coerce_started_at(v) for v in candidate_starts) if c is not None]
        started_at_iso = min(coerced_starts).isoformat() if coerced_starts else archived_at_iso
        archive_doc = {
            "id": str(uuid.uuid4()),
            "user_id": current_user.user_id,
            "archived_at": archived_at_iso,
            "started_at": started_at_iso,
            "finished_at": archived_at_iso,
            "stops": completed,
            "summary": {
                "total_stops": len(completed),
                "delivered": delivered_count,
                "skipped": skipped_count,
                "failed": failed_count,
                "pending": 0,
                "total_weight_kg": round(total_weight, 2),
                "total_quantity": total_quantity,
                "algorithm": None,
                "total_distance_km": None,
                "total_duration_seconds": None,
                # Preserved so we can still tell auto-archives apart in
                # diagnostics — just moved off the rendered surface.
                "auto_archived_reason": "import_process_overwrite",
            },
        }
        await db.route_history.insert_one(archive_doc)
        auto_archived_count = len(completed)
        logger.info(
            f"[import_process] auto-archived {auto_archived_count} completed stops "
            f"for user={current_user.user_id} into route_history "
            f"id={archive_doc['id']} BEFORE wiping for new import",
        )

    await db.stops.delete_many({"user_id": current_user.user_id})

    # Always start from order 0 since we clear all stops
    max_order = -1

    success_count = 0
    failed_count = 0
    failed_addresses = []
    created_stops = []

    def _clean_import_address(value: Any) -> str:
        if value is None:
            return ""
        try:
            if pd.isna(value):
                return ""
        except Exception:
            pass
        text = str(value).replace("\n", " ").replace("\r", " ").strip()
        text = re.sub(r"\s+", " ", text)
        return text

    # --- Concurrent geocoding with semaphore (10 parallel for faster imports) ---
    sem = asyncio.Semaphore(10)

    async def geocode_row(idx, row):
        raw_address = _clean_import_address(row.get(field_mapping.address, ''))
        if not raw_address:
            return None
        async with sem:
            geo_result = await geocode_address_async(raw_address, user_id=current_user.user_id)
        if not geo_result:
            return {"failed": True, "address": raw_address}
        return {
            "failed": False,
            "geo_result": geo_result,
            "row": row,
            "idx": idx,
            "raw_address": raw_address,
        }

    tasks = [geocode_row(idx, row) for idx, row in df.iterrows()]
    results = await asyncio.gather(*tasks)

    # Collect all stops to insert in bulk (much faster than one-by-one)
    stops_to_insert = []

    # Process results in order
    for result in results:
        if result is None:
            continue
        if result["failed"]:
            failed_count += 1
            failed_addresses.append(result["address"])
            continue

        geo_result = result["geo_result"]
        row = result["row"]
        raw_address = result.get("raw_address") or _clean_import_address(row.get(field_mapping.address, ''))

        # Extract mapped fields
        name = None
        if field_mapping.name and field_mapping.name in df.columns:
            name_val = row.get(field_mapping.name)
            if pd.notna(name_val):
                name = str(name_val).strip()

        mobile_number = None
        if field_mapping.mobile_number and field_mapping.mobile_number in df.columns:
            mobile_val = row.get(field_mapping.mobile_number)
            if pd.notna(mobile_val):
                mobile_number = str(mobile_val).strip()

        notes = None
        if field_mapping.notes and field_mapping.notes in df.columns:
            notes_val = row.get(field_mapping.notes)
            if pd.notna(notes_val):
                notes = str(notes_val).strip()

        weight = None
        if field_mapping.weight and field_mapping.weight in df.columns:
            weight_val = row.get(field_mapping.weight)
            if pd.notna(weight_val):
                try:
                    weight = float(weight_val)
                except (ValueError, TypeError):
                    pass

        quantity = None
        if field_mapping.quantity and field_mapping.quantity in df.columns:
            qty_val = row.get(field_mapping.quantity)
            if pd.notna(qty_val):
                try:
                    quantity = int(float(qty_val))
                except (ValueError, TypeError):
                    pass

        # Carrier tracking / barcode column (e.g. "Source Reference") —
        # uppercased + stripped on read so the Van Loading Assistant
        # scanner can do an O(1) Map lookup against the normalised value
        # without having to retry case variants per scan.
        tracking_number = None
        if field_mapping.tracking_number and field_mapping.tracking_number in df.columns:
            tn_val = row.get(field_mapping.tracking_number)
            if pd.notna(tn_val):
                tn_clean = str(tn_val).strip().upper()
                if tn_clean:
                    tracking_number = tn_clean

        max_order += 1
        # Extract suburb from geocoded address first (fast), only reverse-geocode if missing
        suburb = extract_suburb_from_address(geo_result.get("place_name", raw_address))

        geocode_metadata = _build_stop_geocode_metadata(geo_result)
        geocode_metadata["import_original_address"] = raw_address
        geocode_metadata["geocoded_formatted_address"] = geo_result.get("place_name", "")

        stop = Stop(
            id=str(uuid.uuid4()),
            user_id=current_user.user_id,
            address=raw_address,
            name=name,
            mobile_number=mobile_number,
            suburb=suburb,  # May be None, we'll batch-fill later
            latitude=geo_result["latitude"],
            longitude=geo_result["longitude"],
            priority="medium",
            notes=notes,
            weight=weight,
            quantity=quantity,
            tracking_number=tracking_number,
            geocode_metadata=geocode_metadata,
            order=max_order
        )

        stops_to_insert.append(stop)
        created_stops.append(stop)
        success_count += 1

    # Bulk insert all stops at once (much faster than one-by-one)
    if stops_to_insert:
        await db.stops.insert_many([s.dict() for s in stops_to_insert])

    return ImportResult(
        success_count=success_count,
        failed_count=failed_count,
        failed_addresses=failed_addresses[:20],  # Limit to first 20 failed
        stops=created_stops,
        auto_archived_count=auto_archived_count,
    )
