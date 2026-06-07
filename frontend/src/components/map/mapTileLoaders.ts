/**
 * mapTileLoaders.ts — Phase 2 overlay data loaders for the NATIVE MapLibre map.
 *
 * The legacy WebView map (`DeliveryMap.native.tsx`) fetched building / parcel /
 * address tiles INSIDE the injected WebView JS and house numbers from the RN
 * wrapper. The native `@maplibre/maplibre-react-native` map has no WebView, so
 * ALL tile fetching must happen in React-Native JS. This module ports that
 * logic 1:1 (same endpoints, same tile math, same FIFO caches, same
 * stop-tagging radius) so the native overlays match the WebView pixel-for-pixel.
 *
 * ⚠️ Pure JS — NO `@maplibre/maplibre-react-native` import. Safe to evaluate on
 * web (it never renders), keeping the web bundle free of the native module.
 *
 * Each loader is debounced by an internal "view key" (the sorted list of
 * visible tile keys). When the visible tile set is unchanged from the previous
 * call it returns `null`, signalling the caller to skip a needless `setData`
 * (mirrors the WebView's flicker-avoidance short-circuit).
 */

import { BACKEND_URL } from '../../utils/config';

const BACKEND = BACKEND_URL.replace(/\/$/, '');

/** [west, south, east, north] — matches maplibre-react-native `getBounds()`. */
export type Bounds = [west: number, south: number, east: number, north: number];

// ─── Slippy-tile math (identical to the WebView helpers) ─────────────────────
function lngToTileX(lng: number, z: number): number {
  return Math.floor(((lng + 180) / 360) * (1 << z));
}
function latToTileY(lat: number, z: number): number {
  const r = (Math.PI / 180) * lat;
  return Math.floor(
    ((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2) * (1 << z),
  );
}

// ─── Bounded FIFO tile cache (parity with WebView _cachePut) ────────────────
const TILE_CACHE_MAX = 256; // ≈ 25 MB worst case — larger cache means re-visited streets
                            // load instantly from memory instead of re-fetching
interface TileCache {
  map: Record<string, GeoJSON.Feature[]>;
  keys: string[];
}
function makeCache(): TileCache {
  return { map: {}, keys: [] };
}
function cachePut(cache: TileCache, key: string, value: GeoJSON.Feature[]): void {
  if (!(key in cache.map)) cache.keys.push(key);
  cache.map[key] = value;
  while (cache.keys.length > TILE_CACHE_MAX) {
    const evict = cache.keys.shift();
    if (evict && evict !== key) delete cache.map[evict];
  }
}

/** Enumerate the {z}/{x}/{y} tile keys covering `bounds` at zoom `z`. */
function tileKeysForBounds(bounds: Bounds, z: number): string[] {
  const [west, south, east, north] = bounds;
  const xMin = lngToTileX(west, z);
  const xMax = lngToTileX(east, z);
  const yMin = latToTileY(north, z); // note: north → smaller y
  const yMax = latToTileY(south, z);
  const keys: string[] = [];
  for (let x = xMin; x <= xMax; x++) {
    for (let y = yMin; y <= yMax; y++) {
      keys.push(`${z}/${x}/${y}`);
    }
  }
  return keys;
}

/**
 * Generic tile loader. Fetches `${BACKEND}/api/tiles/${kind}/{z}/{x}/{y}.json`
 * for every visible tile, caching results. Returns the merged feature array, or
 * `null` if the visible tile set is unchanged since the previous call.
 */
async function loadTiles(
  kind: 'buildings' | 'parcels' | 'addresses',
  bounds: Bounds,
  z: number,
  cache: TileCache,
  state: { lastViewKey: string },
): Promise<GeoJSON.Feature[] | null> {
  if (!BACKEND) return null;
  const keys = tileKeysForBounds(bounds, z);
  const viewKey = keys.join('|');

  // Fetch any uncached tiles.
  const pending = keys.filter((k) => !(k in cache.map));
  if (pending.length === 0) {
    if (viewKey === state.lastViewKey) return null; // unchanged → skip setData
    state.lastViewKey = viewKey;
    return keys.flatMap((k) => cache.map[k] || []);
  }

  await Promise.all(
    pending.map(async (key) => {
      try {
        const r = await fetch(`${BACKEND}/api/tiles/${kind}/${key}.json`);
        const fc = await r.json();
        cachePut(cache, key, (fc && fc.features) || []);
      } catch {
        cachePut(cache, key, []);
      }
    }),
  );

  state.lastViewKey = viewKey;
  return keys.flatMap((k) => cache.map[k] || []);
}

// ─── Buildings (self-hosted QLD cadastre, z14, always-on ≥ z13) ─────────────
const _buildingCache = makeCache();
const _buildingState = { lastViewKey: '' };
export async function loadBuildingTiles(
  bounds: Bounds,
  zoom: number,
): Promise<GeoJSON.Feature[] | null> {
  if (zoom < 13) return null;
  return loadTiles('buildings', bounds, 14, _buildingCache, _buildingState);
}

// ─── Parcels (cadastral boundaries, z16, only when toggled on ≥ z15) ────────
const _parcelCache = makeCache();
const _parcelState = { lastViewKey: '' };
export async function loadParcelTiles(
  bounds: Bounds,
  zoom: number,
): Promise<GeoJSON.Feature[] | null> {
  if (zoom < 15) return null;
  return loadTiles('parcels', bounds, 16, _parcelCache, _parcelState);
}

// ─── Addresses (street numbers, z16, only when parcels on ≥ z16) ────────────
const _addressCache = makeCache();
const _addressState = { lastViewKey: '' };
export async function loadAddressTiles(
  bounds: Bounds,
  zoom: number,
): Promise<GeoJSON.Feature[] | null> {
  if (zoom < 16) return null;
  return loadTiles('addresses', bounds, 16, _addressCache, _addressState);
}

/**
 * Tag each address feature with `isStop=true` when its point falls within ~25 m
 * of one of the driver's stops. Drives the `address-label-stops` layer (bolder,
 * red) vs the muted `address-label` neighbourhood context. Identical to the
 * WebView `tagAddressesWithStops`.
 */
export function tagAddressesWithStops(
  features: GeoJSON.Feature[],
  stopCoords: [number, number][],
): GeoJSON.Feature[] {
  if (!stopCoords.length || !features.length) return features;
  const RADIUS_DEG = 0.00022; // ≈ 25 m
  const R2 = RADIUS_DEG * RADIUS_DEG;
  return features.map((f) => {
    const geom = f.geometry as GeoJSON.Point | undefined;
    if (!geom || !geom.coordinates) return f;
    const [lng, lat] = geom.coordinates as [number, number];
    const cosLat = Math.cos((lat * Math.PI) / 180) || 1;
    let isStop = false;
    for (let i = 0; i < stopCoords.length; i++) {
      const dx = (stopCoords[i][0] - lng) * cosLat;
      const dy = stopCoords[i][1] - lat;
      if (dx * dx + dy * dy < R2) {
        isStop = true;
        break;
      }
    }
    return {
      type: 'Feature',
      geometry: f.geometry,
      properties: { ...(f.properties || {}), isStop },
    };
  });
}

// ─── House numbers (global OSM, bbox query, ≥ z17) ──────────────────────────
let _hnLastKey = '';
let _hnAbort: AbortController | null = null;
export async function fetchHouseNumbers(
  centerLng: number,
  centerLat: number,
  zoom: number,
): Promise<GeoJSON.FeatureCollection | null> {
  if (zoom < 17 || !BACKEND) return null;
  // Approximate bbox from center + zoom-based radius (1 deg lat ≈ 111 km).
  const radiusDeg = zoom >= 19 ? 0.0015 : zoom >= 18 ? 0.0025 : 0.004;
  const bbox = [
    centerLng - radiusDeg,
    centerLat - radiusDeg,
    centerLng + radiusDeg,
    centerLat + radiusDeg,
  ]
    .map((v) => v.toFixed(4))
    .join(',');
  if (bbox === _hnLastKey) return null;
  _hnLastKey = bbox;

  _hnAbort?.abort();
  const ctrl = new AbortController();
  _hnAbort = ctrl;
  try {
    const resp = await fetch(`${BACKEND}/api/housenumbers?bbox=${bbox}&limit=400`, {
      signal: ctrl.signal,
    });
    if (!resp.ok) return null;
    const data = await resp.json();
    if (!data || !data.features) return null;
    return data as GeoJSON.FeatureCollection;
  } catch {
    return null;
  }
}

/** Reset all debounce keys (e.g. when the map remounts). */
export function resetTileLoaderState(): void {
  _buildingState.lastViewKey = '';
  _parcelState.lastViewKey = '';
  _addressState.lastViewKey = '';
  _hnLastKey = '';
}
