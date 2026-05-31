/**
 * mapEditingHelpers.ts — Phase 3 editing-tool helpers for the NATIVE MapLibre
 * map (lasso, section polygons, no-go zones, driveway hints).
 *
 * Pure JS — NO `@maplibre/maplibre-react-native` import — so it is safe to
 * evaluate on web and unit-test in isolation. Mirrors the geometry helpers in
 * the legacy WebView map (`DeliveryMap.native.tsx`) 1:1 so editing UX is
 * identical across platforms.
 */

/** Ray-casting point-in-polygon. `poly` is a closed ring of [lng,lat]. */
export function pointInPoly(pt: [number, number], poly: number[][]): boolean {
  const x = pt[0];
  const y = pt[1];
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const xi = poly[i][0];
    const yi = poly[i][1];
    const xj = poly[j][0];
    const yj = poly[j][1];
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) {
      inside = !inside;
    }
  }
  return inside;
}

/** Average-vertex centroid of a ring (drops the closing duplicate vertex). */
export function ringCentroid(coords: number[][]): [number, number] {
  const n = Math.max(coords.length - 1, 1);
  let cx = 0;
  let cy = 0;
  for (let i = 0; i < n; i++) {
    cx += coords[i][0];
    cy += coords[i][1];
  }
  return [cx / n, cy / n];
}

export interface SectionPolygon {
  id: number;
  coords: number[][];
  color: string;
  label: string;
}

/**
 * One section → a FeatureCollection with the polygon (fill/line) plus a
 * centroid Point (label). Layers filter on geometry-type so the label only
 * paints on the Point and the fill/line only on the Polygon.
 */
export function sectionToFC(s: SectionPolygon): GeoJSON.FeatureCollection {
  const [cx, cy] = ringCentroid(s.coords);
  return {
    type: 'FeatureCollection',
    features: [
      {
        type: 'Feature',
        properties: { label: s.label || '' },
        geometry: { type: 'Polygon', coordinates: [s.coords] },
      },
      {
        type: 'Feature',
        properties: { label: s.label || '' },
        geometry: { type: 'Point', coordinates: [cx, cy] },
      },
    ],
  };
}

export interface NogoZone {
  id: string;
  name?: string;
  polygon: number[][];
}

/** No-go zones → a red translucent polygon FeatureCollection. */
export function nogoToFC(zones: NogoZone[]): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: (zones || []).map((z) => ({
      type: 'Feature',
      properties: { id: z.id, name: z.name || '' },
      geometry: { type: 'Polygon', coordinates: [z.polygon] },
    })),
  };
}

/**
 * Driveway hints → paired (LineString connector, Point access-dot) features for
 * every non-completed stop carrying numeric `access_lat`/`access_lng` that sit
 * > ~5 m from the pin centroid. Mirrors the WebView driveway-hints feed.
 *
 * @param stops    raw stop objects
 * @param centroid resolves a stop to its display [lng,lat] (ML-corrected pref).
 */
export function buildDrivewayFC(
  stops: any[],
  centroid: (s: any) => [number, number],
): GeoJSON.FeatureCollection {
  const features: GeoJSON.Feature[] = [];
  (stops || []).forEach((s) => {
    if (!s || s.completed) return;
    const alat = s.access_lat;
    const alng = s.access_lng;
    if (typeof alat !== 'number' || typeof alng !== 'number') return;
    const [clng, clat] = centroid(s);
    if (typeof clng !== 'number' || typeof clat !== 'number') return;
    // Skip degenerate hints where the access point ≈ the centroid (< 5 m).
    const dLat = (alat - clat) * 111000;
    const dLng = (alng - clng) * 111000 * Math.cos((clat * Math.PI) / 180);
    if (Math.sqrt(dLat * dLat + dLng * dLng) < 5) return;
    features.push({
      type: 'Feature',
      properties: { stopId: s.id, kind: 'connector' },
      geometry: { type: 'LineString', coordinates: [[clng, clat], [alng, alat]] },
    });
    features.push({
      type: 'Feature',
      properties: { stopId: s.id, kind: 'access' },
      geometry: { type: 'Point', coordinates: [alng, alat] },
    });
  });
  return { type: 'FeatureCollection', features };
}

/** Downsample a screen-point path to at most `max` points (keeps endpoints). */
export function downsamplePath<T>(pts: T[], max = 80): T[] {
  if (pts.length <= max) return pts;
  const step = pts.length / max;
  const out: T[] = [];
  for (let i = 0; i < max; i++) out.push(pts[Math.floor(i * step)]);
  out[out.length - 1] = pts[pts.length - 1];
  return out;
}
