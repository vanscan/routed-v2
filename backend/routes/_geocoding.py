"""Shared Mapbox geocoding helpers used by multiple route modules.

Exports:
  extract_suburb_from_address()   — parse suburb from address text
  reverse_geocode_suburb()        — reverse-geocode lat/lng → suburb name
  get_user_geocoding_context()    — user-specific proximity/country bias
  normalize_address()             — split compound road names
  geocode_address_async()         — full geocode with cache + proximity bias
  _call_mapbox_geocode()          — raw Mapbox API call
  _extract_rich_feature()         — parse rich metadata from Mapbox feature
  _extract_access_navigation_point() — extract routable point from feature
  _encode_plus_code()             — encode coordinates as Plus Code
  _build_stop_geocode_metadata()  — strip coordinates from geocode result
  _cache_geocode_result()         — write result to geocode_cache collection

All helpers that need `db` or `MAPBOX_TOKEN` import them lazily from
`server` inside each function — same deferred-import pattern used by
other route modules — so this module loads before `server.py` finishes.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger("server")


def extract_suburb_from_address(address: str) -> Optional[str]:
    """
    Extract suburb from address string.
    Common formats:
    - "123 Main St, Paddington, QLD 4064, Australia"
    - "123 Main Street Paddington QLD"
    - "Paddington, Brisbane"
    """
    if not address:
        return None

    # Split by comma and analyze parts
    parts = [p.strip() for p in address.split(',')]

    # Australian format: usually suburb is 2nd or 3rd part
    # e.g., "123 Main St, Paddington, QLD 4064" -> Paddington
    if len(parts) >= 2:
        # Check if second part looks like a suburb (not a state or country)
        candidate = parts[1].strip()
        # Remove any postcode that might be attached
        candidate = re.sub(r'\s+\d{4}$', '', candidate)
        # Skip if it's a state abbreviation or country
        states = ['NSW', 'VIC', 'QLD', 'SA', 'WA', 'TAS', 'NT', 'ACT', 'Australia', 'AU']
        if candidate and candidate.upper() not in states and len(candidate) > 2:
            return candidate

    # Try third part if available
    if len(parts) >= 3:
        candidate = parts[2].strip()
        candidate = re.sub(r'\s+\d{4}$', '', candidate)
        states = ['NSW', 'VIC', 'QLD', 'SA', 'WA', 'TAS', 'NT', 'ACT', 'Australia', 'AU']
        if candidate and candidate.upper() not in states and len(candidate) > 2:
            return candidate

    return None


async def reverse_geocode_suburb(latitude: float, longitude: float) -> Optional[str]:
    """Use Mapbox reverse geocoding to get suburb/locality from coordinates"""
    from server import MAPBOX_TOKEN  # noqa: WPS433
    if not MAPBOX_TOKEN:
        return None

    try:
        async with httpx.AsyncClient() as client:
            url = f"https://api.mapbox.com/geocoding/v5/mapbox.places/{longitude},{latitude}.json"
            params = {
                "access_token": MAPBOX_TOKEN,
                "types": "locality,neighborhood,place",
                "limit": 1
            }
            response = await client.get(url, params=params)
            if response.status_code == 200:
                data = response.json()
                if data.get("features"):
                    for feature in data["features"]:
                        # Get the locality or neighborhood
                        place_type = feature.get("place_type", [])
                        if "locality" in place_type or "neighborhood" in place_type:
                            return feature.get("text")
                        # Fallback to place name
                        if "place" in place_type:
                            return feature.get("text")
    except Exception as e:
        logger.error(f"Reverse geocoding error: {e}")

    return None


async def get_user_geocoding_context(user_id: str) -> Dict[str, any]:
    """Get proximity centroid, bbox, and country from user's existing stops for geocoding bias"""
    from server import db, MAPBOX_TOKEN  # noqa: WPS433
    try:
        pipeline = [
            {"$match": {"user_id": user_id}},
            {"$group": {
                "_id": None,
                "avg_lat": {"$avg": "$latitude"},
                "avg_lng": {"$avg": "$longitude"},
                "min_lat": {"$min": "$latitude"},
                "min_lng": {"$min": "$longitude"},
                "max_lat": {"$max": "$latitude"},
                "max_lng": {"$max": "$longitude"},
                "count": {"$sum": 1}
            }}
        ]
        result = await db.stops.aggregate(pipeline).to_list(1)
        if result and result[0]["count"] > 0:
            r = result[0]
            avg_lng = r["avg_lng"]
            avg_lat = r["avg_lat"]
            # Build bbox with ~50km padding (0.5 degrees) to restrict results
            padding = 0.5
            bbox = f"{r['min_lng'] - padding},{r['min_lat'] - padding},{r['max_lng'] + padding},{r['max_lat'] + padding}"
            # Detect country via reverse geocoding of centroid (cached)
            country = None
            cache_key = f"country_{round(avg_lat, 2)}_{round(avg_lng, 2)}"
            cached_country = await db.geocode_cache.find_one({"address_query": cache_key})
            if cached_country:
                country = cached_country.get("country_code")
            else:
                try:
                    async with httpx.AsyncClient() as client:
                        resp = await client.get(
                            f"https://api.mapbox.com/geocoding/v5/mapbox.places/{avg_lng},{avg_lat}.json",
                            params={"access_token": MAPBOX_TOKEN, "types": "country", "limit": 1},
                            timeout=5.0
                        )
                        if resp.status_code == 200:
                            features = resp.json().get("features", [])
                            if features:
                                country = features[0].get("properties", {}).get("short_code", "").lower()
                                await db.geocode_cache.insert_one({
                                    "address_query": cache_key,
                                    "country_code": country,
                                    "created_at": datetime.now(timezone.utc),
                                    "hit_count": 1
                                })
                except Exception as e:
                    logger.error(f"Country detection error: {e}")
            return {"proximity": f"{avg_lng},{avg_lat}", "country": country, "bbox": bbox}
    except Exception as e:
        logger.error(f"Geocoding context error: {e}")
    return {}


def normalize_address(address: str) -> str:
    """Normalize compound road names that Mapbox doesn't fuzzy-match"""
    # Split known compound words: sugarbag -> sugar bag, etc.
    compounds = {
        'sugarbag': 'sugar bag',
        'stringybark': 'stringy bark',
        'ironbark': 'iron bark',
        'blackbutt': 'black butt',
        'tallowwood': 'tallow wood',
        'bloodwood': 'blood wood',
        'paperbark': 'paper bark',
        'teatree': 'tea tree',
        'redgum': 'red gum',
    }
    result = address
    for compound, split in compounds.items():
        result = re.sub(compound, split, result, flags=re.IGNORECASE)
    return result


async def geocode_address_async(address: str, user_id: str = None) -> Optional[Dict[str, Any]]:
    """Geocode a single address using Mapbox API with database caching, proximity and country bias"""
    from server import db, MAPBOX_TOKEN  # noqa: WPS433
    if not MAPBOX_TOKEN:
        return None

    # Normalize address for consistent cache lookup
    normalized_address = normalize_address(address).strip().lower()

    # Check cache first
    try:
        cached = await db.geocode_cache.find_one({"address_query": normalized_address})
        if cached:
            # Update hit count
            await db.geocode_cache.update_one(
                {"_id": cached["_id"]},
                {"$inc": {"hit_count": 1}}
            )
            logger.info(f"Geocode cache HIT for: {address[:50]}...")
            metadata = cached.get("metadata")
            if isinstance(metadata, dict) and metadata:
                centroid_lat = cached.get("latitude")
                centroid_lng = cached.get("longitude")
                centroid_plus_code = _encode_plus_code(centroid_lat, centroid_lng)
                return {
                    "latitude": centroid_lat,
                    "longitude": centroid_lng,
                    "rooftop_centroid": {
                        "latitude": centroid_lat,
                        "longitude": centroid_lng,
                    },
                    "map_pinpoint": metadata.get("map_pinpoint") or {
                        "latitude": centroid_lat,
                        "longitude": centroid_lng,
                        "source": "rooftop_centroid",
                    },
                    "access_navigation_point": metadata.get("access_navigation_point") or {
                        "latitude": centroid_lat,
                        "longitude": centroid_lng,
                        "source": "centroid_fallback",
                    },
                    "centroid_plus_code": metadata.get("centroid_plus_code") or centroid_plus_code,
                    "access_plus_code": metadata.get("access_plus_code") or centroid_plus_code,
                    "plus_code": metadata.get("plus_code") or metadata.get("access_plus_code") or centroid_plus_code,
                    "interpolation_status": metadata.get("interpolation_status") or metadata.get("location_type") or cached.get("location_type", "unknown"),
                    **metadata,
                }

            centroid_lat = cached.get("latitude")
            centroid_lng = cached.get("longitude")
            centroid_plus_code = _encode_plus_code(centroid_lat, centroid_lng)
            return {
                "latitude": centroid_lat,
                "longitude": centroid_lng,
                "rooftop_centroid": {
                    "latitude": centroid_lat,
                    "longitude": centroid_lng,
                },
                "map_pinpoint": {
                    "latitude": centroid_lat,
                    "longitude": centroid_lng,
                    "source": "rooftop_centroid",
                },
                "access_navigation_point": {
                    "latitude": centroid_lat,
                    "longitude": centroid_lng,
                    "source": "centroid_fallback",
                },
                "centroid_plus_code": centroid_plus_code,
                "access_plus_code": centroid_plus_code,
                "plus_code": centroid_plus_code,
                "interpolation_status": cached.get("location_type", "unknown"),
                "place_name": cached["place_name"],
                "formatted_address": cached.get("place_name", ""),
                "business_name": cached.get("place_name", ""),
                "brand": "",
                "is_business": False,
                "poi_category": "",
                "feature_type": "",
                "place_id": cached.get("place_id", ""),
                "location_type": cached.get("location_type", ""),
                "suburb": cached.get("suburb", ""),
                "lga": cached.get("lga", ""),
                "region": cached.get("region", ""),
                "postcode": cached.get("postcode", ""),
                "country": cached.get("country", ""),
                "country_code": cached.get("country_code", ""),
                "admin_areas": {
                    "suburb": cached.get("suburb", ""),
                    "lga": cached.get("lga", ""),
                    "region": cached.get("region", ""),
                    "postcode": cached.get("postcode", ""),
                    "country": cached.get("country", ""),
                    "country_code": cached.get("country_code", ""),
                },
            }
    except Exception as e:
        logger.error(f"Cache lookup error: {e}")

    # Get proximity and country context from user's existing stops
    geo_context = {}
    if user_id:
        geo_context = await get_user_geocoding_context(user_id)

    # Try geocoding with the normalized address
    result = await _call_mapbox_geocode(normalize_address(address), geo_context)

    if result:
        # Cache the result
        await _cache_geocode_result(normalized_address, address, result)
        return result

    return None


async def _call_mapbox_geocode(address: str, geo_context: dict) -> Optional[Dict]:
    """Call Mapbox geocoding API and return rich result with full metadata"""
    from server import MAPBOX_TOKEN  # noqa: WPS433
    try:
        params = {
            "q": address,
            "access_token": MAPBOX_TOKEN,
            "limit": 1,
            "types": "address,street,place",
            "routing": "true",
        }
        if geo_context.get("proximity"):
            params["proximity"] = geo_context["proximity"]
        if geo_context.get("country"):
            params["country"] = geo_context["country"]
        if geo_context.get("bbox"):
            params["bbox"] = geo_context["bbox"]

        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.mapbox.com/search/geocode/v6/forward",
                params=params,
                timeout=10.0
            )

            if response.status_code == 200:
                data = response.json()
                if data.get("features") and len(data["features"]) > 0:
                    feature = data["features"][0]
                    return _extract_rich_feature(feature)
    except Exception as e:
        logger.error(f"Geocoding error for '{address}': {e}")
    return None


def _encode_plus_code(lat: Optional[float], lng: Optional[float]) -> str:
    try:
        if lat is None or lng is None:
            return ""
        from openlocationcode import openlocationcode as olc  # noqa: WPS433
        return olc.encode(float(lat), float(lng), codeLength=11)
    except Exception:
        return ""


def _extract_access_navigation_point(feature: dict, props: dict, centroid_lat: Optional[float], centroid_lng: Optional[float]) -> Dict[str, Any]:
    coordinates_meta = props.get("coordinates") if isinstance(props.get("coordinates"), dict) else {}

    routable_points = []
    if isinstance(coordinates_meta.get("routable_points"), list):
        routable_points = coordinates_meta.get("routable_points")
    elif isinstance(props.get("routable_points"), list):
        routable_points = props.get("routable_points")
    elif isinstance(feature.get("routable_points"), list):
        routable_points = feature.get("routable_points")

    if routable_points:
        rp = routable_points[0]
        if isinstance(rp, dict):
            rp_lat = rp.get("latitude")
            rp_lng = rp.get("longitude")
            if rp_lat is not None and rp_lng is not None:
                return {
                    "latitude": rp_lat,
                    "longitude": rp_lng,
                    "source": "routable_point",
                }

    return {
        "latitude": centroid_lat,
        "longitude": centroid_lng,
        "source": "centroid_fallback",
    }


def _extract_rich_feature(feature: dict) -> Dict:
    """Extract all available metadata from a Mapbox geocoding feature"""
    props = feature.get("properties", {})
    context = feature.get("context", [])

    # Parse administrative levels from v5 context array or v6 context object
    admin = {}
    if isinstance(context, list):
        for ctx in context:
            if not isinstance(ctx, dict):
                continue
            ctx_id = ctx.get("id", "")
            prefix = ctx_id.split(".")[0] if "." in ctx_id else ctx_id
            admin[prefix] = {
                "id": ctx_id,
                "text": ctx.get("text", ""),
                "short_code": ctx.get("short_code"),
                "wikidata": ctx.get("wikidata"),
            }
    elif isinstance(context, dict):
        for prefix, ctx in context.items():
            if not isinstance(ctx, dict):
                continue
            admin[prefix] = {
                "id": ctx.get("mapbox_id") or ctx.get("id", ""),
                "text": ctx.get("name") or ctx.get("text", ""),
                "short_code": ctx.get("short_code"),
                "wikidata": ctx.get("wikidata") or ctx.get("wikidata_id"),
            }

    place_name = (
        feature.get("place_name")
        or props.get("full_address")
        or props.get("name_preferred")
        or props.get("name")
        or ""
    )
    feature_type = props.get("feature_type", "")
    raw_name = props.get("name_preferred") or props.get("name") or ""
    if not raw_name and place_name:
        raw_name = place_name.split(",")[0].strip()
    brand_value = props.get("brand", "")
    category_value = props.get("category", "")
    looks_like_street_address = bool(re.match(r"^\s*\d+", raw_name))
    business_like_feature = feature_type in {"poi", "business"}
    business_name = raw_name if (business_like_feature or brand_value or category_value) and not looks_like_street_address else ""
    is_business = bool(
        business_like_feature
        or brand_value
        or category_value
        or props.get("maki")
        or props.get("landmark")
    )
    categories = [c.strip() for c in category_value.split(",") if c.strip()] if category_value else []

    center = feature.get("center") or (feature.get("geometry", {}) or {}).get("coordinates", [None, None])
    centroid_lng = center[0] if isinstance(center, list) and len(center) > 1 else None
    centroid_lat = center[1] if isinstance(center, list) and len(center) > 1 else None

    access_point = _extract_access_navigation_point(feature, props, centroid_lat, centroid_lng)
    interpolation_status = (
        props.get("accuracy")
        or ((props.get("coordinates") or {}).get("accuracy") if isinstance(props.get("coordinates"), dict) else None)
        or "unknown"
    )

    centroid_plus_code = _encode_plus_code(centroid_lat, centroid_lng)
    access_plus_code = _encode_plus_code(access_point.get("latitude"), access_point.get("longitude"))

    return {
        # Core coordinates
        "latitude": centroid_lat,
        "longitude": centroid_lng,
        "rooftop_centroid": {
            "latitude": centroid_lat,
            "longitude": centroid_lng,
        },
        "map_pinpoint": {
            "latitude": centroid_lat,
            "longitude": centroid_lng,
            "source": "rooftop_centroid",
        },
        "access_navigation_point": access_point,
        "centroid_plus_code": centroid_plus_code,
        "access_plus_code": access_plus_code,
        "plus_code": access_plus_code or centroid_plus_code,
        "interpolation_status": interpolation_status,

        # Formatted address
        "place_name": place_name,
        "formatted_address": place_name,
        "text": feature.get("text", props.get("name", "")),             # Street/place name only
        "address_number": props.get("address", ""),   # House number
        "business_name": business_name,
        "brand": brand_value,
        "is_business": is_business,
        "poi_category": category_value,
        "feature_type": feature_type,

        # Identifiers
        "id": feature.get("id", props.get("mapbox_id", "")),
        "place_id": feature.get("id", props.get("mapbox_id", "")),
        "place_type": feature.get("place_type", [feature_type] if feature_type else []),
        "relevance": feature.get("relevance", 1.0),

        # Location accuracy / type (ROOFTOP, INTERPOLATED, APPROXIMATE, etc.)
        "location_type": interpolation_status,

        # OSM-like tags & categories
        "category": category_value,                      # e.g. "shop", "restaurant"
        "categories": categories,
        "maki": props.get("maki", ""),                 # POI icon category
        "landmark": props.get("landmark", False),
        "wikidata": props.get("wikidata", ""),
        "foursquare": props.get("foursquare", ""),
        "osm_tags": {
            "category": category_value,
            "maki": props.get("maki", ""),
            "wikidata": props.get("wikidata", ""),
            "foursquare": props.get("foursquare", ""),
            "landmark": props.get("landmark", False),
        },

        # Administrative area levels
        "neighborhood": admin.get("neighborhood", {}).get("text", ""),
        "suburb": admin.get("locality", {}).get("text", "") or admin.get("place", {}).get("text", ""),
        "locality": admin.get("locality", {}).get("text", ""),
        "lga": admin.get("district", {}).get("text", ""),          # Local Government Area
        "city": admin.get("place", {}).get("text", ""),
        "region": admin.get("region", {}).get("text", ""),         # State
        "region_code": admin.get("region", {}).get("short_code", ""),
        "postcode": admin.get("postcode", {}).get("text", ""),
        "country": admin.get("country", {}).get("text", ""),
        "country_code": admin.get("country", {}).get("short_code", ""),
        "admin_areas": {
            "neighborhood": admin.get("neighborhood", {}).get("text", ""),
            "suburb": admin.get("locality", {}).get("text", "") or admin.get("place", {}).get("text", ""),
            "locality": admin.get("locality", {}).get("text", ""),
            "lga": admin.get("district", {}).get("text", ""),
            "city": admin.get("place", {}).get("text", ""),
            "region": admin.get("region", {}).get("text", ""),
            "region_code": admin.get("region", {}).get("short_code", ""),
            "postcode": admin.get("postcode", {}).get("text", ""),
            "country": admin.get("country", {}).get("text", ""),
            "country_code": admin.get("country", {}).get("short_code", ""),
        },

        # Geometry & bounds
        "geometry": feature.get("geometry", {}),
        "bbox": feature.get("bbox"),

        # Raw context (for any fields we didn't explicitly extract)
        "context_raw": context,
    }


def _build_stop_geocode_metadata(source: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Prepare geocode metadata payload for stop storage (all metadata, excluding coordinates)."""
    if not source or not isinstance(source, dict):
        return None

    metadata = {k: v for k, v in source.items() if k not in {"latitude", "longitude"}}
    if "formatted_address" not in metadata and source.get("place_name"):
        metadata["formatted_address"] = source.get("place_name")
    return metadata or None


async def _cache_geocode_result(normalized_address: str, original_address: str, result: dict):
    """Save geocode result to cache (stores full rich metadata)"""
    from server import db  # noqa: WPS433
    try:
        cache_entry = {
            "id": str(uuid.uuid4()),
            "address_query": normalized_address,
            "original_address": original_address,
            "latitude": result["latitude"],
            "longitude": result["longitude"],
            "place_name": result.get("place_name", ""),
            "metadata": _build_stop_geocode_metadata(result),
            "place_id": result.get("place_id", ""),
            "location_type": result.get("location_type", ""),
            "suburb": result.get("suburb", ""),
            "lga": result.get("lga", ""),
            "region": result.get("region", ""),
            "postcode": result.get("postcode", ""),
            "country": result.get("country", ""),
            "country_code": result.get("country_code", ""),
            "created_at": datetime.now(timezone.utc),
            "hit_count": 1
        }
        await db.geocode_cache.insert_one(cache_entry)
        logger.info(f"Geocode cached: {original_address[:50]}...")
    except Exception as e:
        logger.error(f"Cache save error: {e}")
