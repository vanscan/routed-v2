/**
 * DeliveryMapNative.tsx — NATIVE MapLibre implementation (Phase 0).
 *
 * Migration target for the WebView-based `DeliveryMap.native.tsx`. Built on
 * the native SDK `@maplibre/maplibre-react-native` (v11, New-Architecture
 * compatible) instead of MapLibre GL JS inside a WebView.
 *
 * Phase 0 scope (this file):
 *   - Bare native `Map` with the OpenFreeMap "Liberty" style (proxied via the
 *     backend `/api/map/style`, identical to the WebView path — no Mapbox /
 *     Google billing).
 *   - Delivery stops rendered as a native GeoJSON source + circle + label
 *     layers (numbered pins).
 *   - Optional route polyline.
 *   - Native `UserLocation` puck (device GPS, heading arrow).
 *   - Camera helpers (`flyTo` / `jumpTo` / `fitBounds`) wired to the native
 *     `Camera` ref so this component is a structural drop-in for the WebView
 *     map's `DeliveryMapRef`.
 *
 * Everything else from the WebView ref interface (lasso, no-go draw, parcels,
 * clusters, section polygons, …) is stubbed as a typed no-op here and will be
 * ported in later phases behind the `useNativeMap` flag. This keeps the file a
 * valid `DeliveryMapRef` so the parent can swap implementations without code
 * changes once parity lands.
 *
 * ⚠️ Native module — does NOT run in Expo Go or the web preview. Requires an
 * EAS development/production build to render.
 */
import React, {
  forwardRef,
  useImperativeHandle,
  useRef,
  useMemo,
  useEffect,
  useCallback,
  useState,
} from 'react';
import { View, StyleSheet, Dimensions, PanResponder } from 'react-native';
import Animated, {
  useSharedValue,
  useAnimatedStyle,
  withRepeat,
  withTiming,
  Easing,
} from 'react-native-reanimated';
import Svg, { Polyline as SvgPolyline, Polygon as SvgPolygon } from 'react-native-svg';
import {
  Map as MapLibreMap,
  Camera,
  GeoJSONSource,
  Layer,
  UserLocation,
  Images,
  LogManager,
  type MapRef,
  type CameraRef,
  type GeoJSONSourceRef,
} from '@maplibre/maplibre-react-native';
import { buildLateFreightLabels } from '../../utils/stopPinNumber';
import { ZipperRouteLayer } from '../../hooks/useLateFreightZipper';
import {
  loadBuildingTiles,
  loadMsBuildingTiles,
  loadParcelTiles,
  loadAddressTiles,
  fetchHouseNumbers,
  tagAddressesWithStops,
  type Bounds,
} from './mapTileLoaders';
import {
  pointInPoly,
  sectionToFC,
  nogoToFC,
  buildDrivewayFC,
  downsamplePath,
  type SectionPolygon,
  type NogoZone,
} from './mapEditingHelpers';
import type {
  DeliveryMapRef,
  DeliveryStop,
  DriverLocation,
} from '../DeliveryMap';
import type { DeliveryMapNativeProps } from './DeliveryMapNative.types';

export type { DeliveryMapNativeProps };

// Quieten MapLibre's verbose native logging (tile 404s etc.) — keep errors.
try {
  LogManager.setLogLevel('error');
} catch {
  // older/newer signature — non-fatal
}

import { BACKEND_URL } from '../../utils/config';

// ─── Style source — VersaTiles (free FLOSS map stack) ────────────────────────
// VersaTiles provides free vector tiles, styles, fonts and sprites
// https://versatiles.org/ - completely free, no API keys needed
const VERSATILES_STYLE = 'https://tiles.versatiles.org/assets/styles/colorful/style.json';

// Fallback to backend proxy or VersaTiles
const _BACKEND_FOR_STYLE = BACKEND_URL.replace(/\/$/, '');
const MAP_STYLE = _BACKEND_FOR_STYLE
  ? `${_BACKEND_FOR_STYLE}/api/map/style`
  : VERSATILES_STYLE;

const DEFAULT_CENTER: [number, number] = [153.0667, -26.65]; // Sunshine Coast, QLD
const DEFAULT_ZOOM = 11;

// ─── Helpers ─────────────────────────────────────────────────────────────────

// Pin palette (parity with the WebView painter).
const PIN_COMPLETED = '#16a34a'; // green — delivered
const PIN_LOCKED = '#0b2545'; // navy — locked stop (has original_sequence)
const PIN_PLANNING = '#2563eb'; // blue — proposed drive order (planning)
const PIN_LATE = '#7c3aed'; // purple — late freight after lock-in

/** Prefer ML-corrected display coords, fall back to raw. Returns [lng, lat]. */
function stopLngLat(stop: DeliveryStop): [number, number] {
  const anyStop = stop as DeliveryStop & {
    display_latitude?: number | null;
    display_longitude?: number | null;
  };
  const lat = anyStop.display_latitude ?? stop.latitude;
  const lng = anyStop.display_longitude ?? stop.longitude;
  return [lng, lat];
}

/**
 * Offset radius in degrees (~15 meters at equator) for spreading overlapping
 * stops in a circle around their shared coordinate. Small enough to keep pins
 * visually grouped but large enough to allow individual tap targets.
 */
const OVERLAP_OFFSET_DEG = 0.00015;

/**
 * Apply circular offset to overlapping stops so they spread out and become
 * individually clickable. Stops at the exact same coordinate are arranged in
 * a circle around that point.
 * 
 * Returns a Map from stop.id → offset [lng, lat] for rendering.
 */
function computeOverlapOffsets(stops: DeliveryStop[]): Map<string | number, [number, number]> {
  const offsets = new Map<string | number, [number, number]>();
  
  // Group stops by their coordinate key (rounded to ~1m precision)
  const coordGroups = new Map<string, DeliveryStop[]>();
  for (const s of stops) {
    if (!Number.isFinite(s.latitude) || !Number.isFinite(s.longitude)) continue;
    const [lng, lat] = stopLngLat(s);
    // Round to 5 decimal places (~1.1m precision) for grouping
    const key = `${lng.toFixed(5)},${lat.toFixed(5)}`;
    const group = coordGroups.get(key) || [];
    group.push(s);
    coordGroups.set(key, group);
  }
  
  // For each group with multiple stops, spread them in a circle
  for (const [, group] of coordGroups) {
    if (group.length === 1) {
      // Single stop — no offset needed
      const s = group[0];
      offsets.set(s.id, stopLngLat(s));
    } else {
      // Multiple stops at same location — spread in circle
      const [baseLng, baseLat] = stopLngLat(group[0]);
      const count = group.length;
      const angleStep = (2 * Math.PI) / count;
      
      group.forEach((s, i) => {
        const angle = i * angleStep - Math.PI / 2; // Start from top
        const offsetLng = baseLng + OVERLAP_OFFSET_DEG * Math.cos(angle);
        const offsetLat = baseLat + OVERLAP_OFFSET_DEG * Math.sin(angle);
        offsets.set(s.id, [offsetLng, offsetLat]);
      });
    }
  }
  
  return offsets;
}

/**
 * Build the stop pins FeatureCollection with per-feature `label` + `color` + `marker`,
 * matching the WebView painter:
 *   - completed → green
 *   - locked stop (has original_sequence) → navy, shows its sequence number
 *   - late freight (no original_sequence):
 *       · planning mode  → blue, shows `order + 1`
 *       · locked mode    → purple, shows its slot label (e.g. "45A")
 * 
 * Overlapping stops at the same coordinate are spread in a small circle so each
 * pin is individually clickable.
 */
function stopsToFeatureCollection(
  stops: DeliveryStop[],
  routeConfirmed: boolean,
): GeoJSON.FeatureCollection {
  const lateLabels = buildLateFreightLabels(stops as any);
  
  // Debug logging for late freight labels
  if (__DEV__ && Object.keys(lateLabels).length > 0) {
    console.log('[stopsToFeatureCollection] Late freight labels:', lateLabels);
    console.log('[stopsToFeatureCollection] routeConfirmed:', routeConfirmed);
  }
  
  const validStops = (stops || []).filter(
    (s) => Number.isFinite(s.latitude) && Number.isFinite(s.longitude)
  );
  
  // Compute offset coordinates for overlapping stops
  const offsetCoords = computeOverlapOffsets(validStops);
  
  return {
    type: 'FeatureCollection',
    features: validStops.map((s) => {
      // Use offset coordinates if computed, otherwise fall back to original
      const coords = offsetCoords.get(s.id) || stopLngLat(s);
      const [lng, lat] = coords;
      
      const anyStop = s as DeliveryStop & { original_sequence?: number | null };
      const hasSeq = anyStop.original_sequence != null;
      const completed = !!s.completed;

      let label: string;
      let color: string;
      let marker: string;
      if (hasSeq) {
        label = String(anyStop.original_sequence);
        color = completed ? PIN_COMPLETED : PIN_LOCKED;
        marker = completed ? 'marker-green' : 'marker-navy';
      } else if (routeConfirmed) {
        // Late freight on a locked route.
        label = (s.id && lateLabels[s.id]) || '★';
        color = completed ? PIN_COMPLETED : PIN_LATE;
        marker = completed ? 'marker-green' : 'marker-purple';
      } else {
        // Planning mode — proposed drive order.
        label = String((s.order ?? 0) + 1);
        color = completed ? PIN_COMPLETED : PIN_PLANNING;
        marker = completed ? 'marker-green' : 'marker-blue';
      }

      return {
        type: 'Feature' as const,
        id: s.id,
        properties: { id: s.id, label, color, marker, completed },
        geometry: { type: 'Point' as const, coordinates: [lng, lat] },
      };
    }),
  };
}

function lineFeature(coords: number[][] | null): GeoJSON.FeatureCollection {
  const valid = Array.isArray(coords) && coords.length >= 2;
  return {
    type: 'FeatureCollection',
    features: valid
      ? [
          {
            type: 'Feature',
            properties: {},
            geometry: { type: 'LineString', coordinates: coords as number[][] },
          },
        ]
      : [],
  };
}

// ─── RouteLine — pure memo component so GPS ticks never re-render the route ──
// The parent re-renders on every GPS update (driverLocation prop change), but
// this component only re-renders when the route GeoJSON or preview flag changes.
// Keeps the GeoJSONSource and its child layers off the 250 ms render cycle.
type RouteLineProps = {
  routeFC: GeoJSON.FeatureCollection;
  routeIsPreview?: boolean;
};
const RouteLine = React.memo(function RouteLine({ routeFC, routeIsPreview }: RouteLineProps) {
  return (
    <GeoJSONSource id="route-src" data={routeFC}>
      <Layer
        id="route-line"
        type="line"
        layout={{ 'line-cap': 'round', 'line-join': 'round' }}
        paint={{
          'line-color': '#2563eb',
          'line-width': 6,
          'line-opacity': 0.9,
          ...(routeIsPreview ? { 'line-dasharray': [2, 2] } : {}),
        }}
      />
      {/* Directional arrows along the route line - vector-based with MapLibre */}
      <Layer
        id="route-arrows"
        type="symbol"
        layout={{
          'symbol-placement': 'line',
          'symbol-spacing': 100,
          'icon-image': 'route-arrow',
          'icon-size': 0.6,
          'icon-rotation-alignment': 'map',
          'icon-keep-upright': true,
          'icon-allow-overlap': true,
          'icon-ignore-placement': true,
          'icon-pitch-alignment': 'map',
        }}
        paint={{
          'icon-opacity': 0.95,
        }}
      />
    </GeoJSONSource>
  );
});

// ─── Driving-camera tuning (parity with WebView 3D driving mode) ─────────────
const DRIVING_PITCH = 60; // degrees — 3D look-ahead tilt
const DRIVING_ZOOM = 18.5; // street-level, close to driver
const DRIVING_EASE_MS = 200; // finish before next 250 ms GPS tick — no skipped frames
const DRIVING_BOTTOM_PAD_RATIO = 0.45; // push driver toward bottom of screen

// ─── Phase 2 overlay tuning ──────────────────────────────────────────────────
const EMPTY_FC: GeoJSON.FeatureCollection = { type: 'FeatureCollection', features: [] };
// Below this zoom we show delivery clusters; at/above it we show numbered pins.
// Mirrors the WebView CLUSTER_SWAP_ZOOM. Implemented declaratively via
// min/maxzoom so no per-frame visibility toggling is needed.
const CLUSTER_SWAP_ZOOM = 14;

// ─── Turn detection for route arrows ─────────────────────────────────────────
// Calculate bearing between two points
function bearing(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const toRad = (d: number) => (d * Math.PI) / 180;
  const toDeg = (r: number) => (r * 180) / Math.PI;
  const dLon = toRad(lon2 - lon1);
  const y = Math.sin(dLon) * Math.cos(toRad(lat2));
  const x = Math.cos(toRad(lat1)) * Math.sin(toRad(lat2)) -
            Math.sin(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.cos(dLon);
  return (toDeg(Math.atan2(y, x)) + 360) % 360;
}

// Extract significant turns from route coordinates
function extractTurnPoints(coords: number[][] | null): GeoJSON.FeatureCollection {
  if (!coords || coords.length < 3) return EMPTY_FC;
  
  const turns: GeoJSON.Feature[] = [];
  const minTurnAngle = 25; // Minimum angle to consider a turn (lowered to catch more turns)
  const minDistance = 0.0002; // ~20m minimum distance between turn indicators
  
  let lastTurnCoord: number[] | null = null;
  
  for (let i = 1; i < coords.length - 1; i++) {
    const [lon1, lat1] = coords[i - 1];
    const [lon2, lat2] = coords[i];
    const [lon3, lat3] = coords[i + 1];
    
    // Skip if too close to last turn
    if (lastTurnCoord) {
      const dist = Math.sqrt(
        Math.pow(lon2 - lastTurnCoord[0], 2) + 
        Math.pow(lat2 - lastTurnCoord[1], 2)
      );
      if (dist < minDistance) continue;
    }
    
    const bearing1 = bearing(lat1, lon1, lat2, lon2);
    const bearing2 = bearing(lat2, lon2, lat3, lon3);
    
    let turnAngle = bearing2 - bearing1;
    // Normalize to -180 to 180
    if (turnAngle > 180) turnAngle -= 360;
    if (turnAngle < -180) turnAngle += 360;
    
    const absTurn = Math.abs(turnAngle);
    
    if (absTurn >= minTurnAngle) {
      // Use the bearing of the outgoing segment (direction of travel after the turn)
      // This tells the driver which way they'll be heading
      const exitBearing = bearing2;
      
      turns.push({
        type: 'Feature',
        properties: { 
          turnType: turnAngle > 0 ? 'turn-right' : 'turn-left',
          // Rotation for the icon - bearing2 points in direction of travel after turn
          // Icon base orientation is 0° = pointing right, so subtract 90 for north-up
          rotation: exitBearing,
          angle: Math.round(absTurn),
        },
        geometry: {
          type: 'Point',
          coordinates: [lon2, lat2],
        },
      });
      
      lastTurnCoord = [lon2, lat2];
    }
  }
  
  return { type: 'FeatureCollection', features: turns };
}

// ─── Component ───────────────────────────────────────────────────────────────

const DeliveryMapNativeInner = forwardRef<DeliveryMapRef, DeliveryMapNativeProps>(
  (props, ref) => {
    const {
      stops,
      routeCoordinates,
      routeIsPreview,
      driverLocation,
      traveledPath,
      initialCenter,
      initialZoom,
      followDriver,
      onStopClick,
      onCameraIdle,
      onMapReady,
      nextStopCoord,
      nextStopColor,
    } = props;

    const mapRef = useRef<MapRef | null>(null);
    const cameraRef = useRef<CameraRef | null>(null);
    const lastZoomRef = useRef<number>(initialZoom ?? DEFAULT_ZOOM);

    // Planning (false) vs locked (true) pin painter — set via setRouteConfirmed.
    const [routeConfirmed, setRouteConfirmedState] = useState(false);
    // Bumped by forceStopsRefresh() to force a full pin recompute.
    const [refreshNonce, setRefreshNonce] = useState(0);

    // Driving-camera bookkeeping.
    const easeInFlightRef = useRef(false);
    const userInteractingRef = useRef(false);
    // True once the entry zoom has been applied for the current nav session.
    // driveCamera sets zoom on the FIRST tick only so the user can freely
    // pinch-zoom without being snapped back to DRIVING_ZOOM every 250 ms.
    const drivingZoomSetRef = useRef(false);
    // Mirrors the highFreqCameraActive prop in a ref so handleRegionDidChange
    // (a stable callback) can read the current value without being re-created.
    const highFreqCameraActiveRef = useRef(false);
    const userInteractTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const mapHeightRef = useRef<number>(Dimensions.get('window').height);
    // Reactive copy of mapHeightRef used for Camera padding prop during navigation.
    const [mapHeight, setMapHeight] = useState<number>(Dimensions.get('window').height);
    const wasDrivingRef = useRef(false);

    // ── Phase 2 overlay state ──────────────────────────────────────────────
    // GeoJSON fed from backend tiles on camera idle (self-hosted QLD buildings,
    // cadastral parcels, street-number addresses, OSM house numbers) plus the
    // imperatively-pushed delivery clusters.
    const [buildingsFC, setBuildingsFC] = useState<GeoJSON.FeatureCollection>(EMPTY_FC);
    const [msBuildingsFC, setMsBuildingsFC] = useState<GeoJSON.FeatureCollection>(EMPTY_FC);
    const [parcelsFC, setParcelsFC] = useState<GeoJSON.FeatureCollection>(EMPTY_FC);
    const [addressesFC, setAddressesFC] = useState<GeoJSON.FeatureCollection>(EMPTY_FC);
    const [houseNumbersFC, setHouseNumbersFC] =
      useState<GeoJSON.FeatureCollection>(EMPTY_FC);
    const [clustersFC, setClustersFC] = useState<GeoJSON.FeatureCollection>(EMPTY_FC);
    const [parcelsVisible, setParcelsVisible] = useState(false);
    // Guards against overlapping overlay refreshes on rapid idle events.
    const overlayBusyRef = useRef(false);
    // Separate busy guard for MS buildings during driving — avoids contention
    // with overlayBusyRef which isn't consumed during highFreqCameraActive.
    const msBusyRef = useRef(false);
    const parcelsVisibleRef = useRef(false);
    const clusterSourceRef = useRef<GeoJSONSourceRef | null>(null);
    const hasClusterData = clustersFC.features.length > 0;

    // ── Phase 3 editing state (lasso / sections / no-go zones) ──────────────
    const [drawingMode, setDrawingModeState] = useState(false);
    const drawingModeRef = useRef(false);
    const blockRoadRef = useRef(false);
    // Freehand lasso path in SCREEN space (px) for the SVG overlay.
    const [lassoScreenPts, setLassoScreenPts] = useState<{ x: number; y: number }[]>([]);
    const lassoPtsRef = useRef<{ x: number; y: number }[]>([]);
    const lassoThrottleRef = useRef(0);
    const lassoFinishingRef = useRef(false);
    // Driver-drawn section polygons (added imperatively after a lasso).
    const [sections, setSections] = useState<SectionPolygon[]>([]);
    // No-go zones (red impassable polygons).
    const [nogoFC, setNogoFC] = useState<GeoJSON.FeatureCollection>(EMPTY_FC);

    // ── Animated pulse ring state ────────────────────────────────────────────
    // The static MapLibre circle layer provides a base; this animated overlay
    // adds a pulsing outer ring in screen-space using react-native-reanimated.
    const pulseScale = useSharedValue(1);
    const pulseOpacity = useSharedValue(0.6);
    const [pulseScreenXY, setPulseScreenXY] = useState<{ x: number; y: number } | null>(null);

    // Kick off the infinite pulse animation.
    useEffect(() => {
      pulseScale.value = withRepeat(
        withTiming(2.0, { duration: 1800, easing: Easing.out(Easing.ease) }),
        -1, // infinite
        false, // no reverse — snap back to 1
      );
      pulseOpacity.value = withRepeat(
        withTiming(0, { duration: 1800, easing: Easing.out(Easing.ease) }),
        -1,
        false,
      );
    }, [pulseScale, pulseOpacity]);

    // Convert next-stop geo coords → screen XY for the pulse overlay. Called
    // after every camera change so the ring tracks the map.
    const updatePulseScreenPos = useCallback(() => {
      if (!nextStopCoord || !mapRef.current) {
        setPulseScreenXY(null);
        return;
      }
      // MapLibre native v11: project() returns screen coords for a geo coord.
      mapRef.current
        .project(nextStopCoord)
        .then((pt) => {
          if (pt && typeof pt[0] === 'number' && typeof pt[1] === 'number') {
            setPulseScreenXY({ x: pt[0], y: pt[1] });
          } else {
            setPulseScreenXY(null);
          }
        })
        .catch(() => setPulseScreenXY(null));
    }, [nextStopCoord]);

    // Re-project the pulse position whenever the next-stop or camera changes.
    useEffect(() => {
      updatePulseScreenPos();
    }, [nextStopCoord, updatePulseScreenPos]);

    const pulseAnimatedStyle = useAnimatedStyle(() => ({
      transform: [{ scale: pulseScale.value }],
      opacity: pulseOpacity.value,
    }));

    const center = initialCenter ?? DEFAULT_CENTER;
    const zoom = initialZoom ?? DEFAULT_ZOOM;
    const highFreqCameraActive = !!props.highFreqCameraActive;
    // Keep the ref in sync so stable callbacks can read it without stale closure
    highFreqCameraActiveRef.current = highFreqCameraActive;

    // Stop centroids (lng/lat) for tagging nearby address labels as delivery
    // targets — rebuilt only when the stop set changes.
    const stopCoords = useMemo<[number, number][]>(
      () =>
        (stops || [])
          .filter((s) => Number.isFinite(s.latitude) && Number.isFinite(s.longitude))
          .map((s) => stopLngLat(s)),
      [stops],
    );

    // Driveway-hint connectors + access dots, derived from stop access points.
    const drivewayFC = useMemo(
      () => buildDrivewayFC(stops as any[], (s) => stopLngLat(s as DeliveryStop)),
      [stops],
    );

    // Live refs for values the lasso gesture reads on release — keeps
    // `finishLasso` / the PanResponder STABLE so they aren't recreated mid-drag
    // when `lassoScreenPts` state updates trigger a re-render.
    const stopsRef = useRef(stops);
    stopsRef.current = stops;
    const onLassoCompleteRef = useRef(props.onLassoComplete);
    onLassoCompleteRef.current = props.onLassoComplete;

    // Derived GeoJSON — memoised so GPS / camera ticks don't rebuild them.
    const stopsFC = useMemo(() => {
      const fc = stopsToFeatureCollection(stops, routeConfirmed);
      if (__DEV__ && fc.features.length > 0) {
        console.log('[stopsFC] Generated', fc.features.length, 'stop features');
        console.log('[stopsFC] First feature:', JSON.stringify(fc.features[0]));
      }
      return fc;
    }, [stops, routeConfirmed, refreshNonce]);
    const routeFC = useMemo(() => lineFeature(routeCoordinates), [routeCoordinates]);
    // Turn points for showing turn indicators at corners
    const turnPointsFC = useMemo(() => extractTurnPoints(routeCoordinates), [routeCoordinates]);
    const traveledFC = useMemo(() => lineFeature(traveledPath), [traveledPath]);
    const nextRingFC = useMemo<GeoJSON.FeatureCollection>(
      () => ({
        type: 'FeatureCollection',
        features: nextStopCoord
          ? [
              {
                type: 'Feature',
                properties: {},
                geometry: { type: 'Point', coordinates: nextStopCoord },
              },
            ]
          : [],
      }),
      [nextStopCoord],
    );

    // ── Driving camera: imperatively track the puck from a drivingCamera message.
    //    Look-ahead is achieved with top-heavy padding (puck → lower third,
    //    road ahead up top), matching the WebView's pixel-space offset.
    //
    //    Zoom is applied ONCE per navigation session (first tick only) so the
    //    user can freely pinch-zoom without being snapped back every 250 ms.
    //    All subsequent ticks skip the zoom axis entirely. ──────────────────
    const driveCamera = useCallback(
      (lng: number, lat: number, bearing?: number, _speedMps?: number) => {
        const cam = cameraRef.current;
        if (!cam) return;
        if (easeInFlightRef.current) return;
        if (userInteractingRef.current) return;
        const topPad = Math.round(mapHeightRef.current * DRIVING_BOTTOM_PAD_RATIO);
        easeInFlightRef.current = true;
        // Set zoom only on the first tick of a navigation session.
        const entryZoom = drivingZoomSetRef.current ? undefined : DRIVING_ZOOM;
        drivingZoomSetRef.current = true;
        try {
          cam.easeTo({
            center: [lng, lat],
            ...(entryZoom !== undefined ? { zoom: entryZoom } : {}),
            bearing: bearing ?? 0,
            pitch: DRIVING_PITCH,
            padding: { top: topPad, right: 0, bottom: 0, left: 0 },
            duration: DRIVING_EASE_MS,
          });
        } catch {
          // ignore transient native camera errors
        }
        // Guard cleared after ease finishes (DRIVING_EASE_MS=200ms).
        // Interval is 250ms → 50ms settling gap before next tick.
        setTimeout(() => { easeInFlightRef.current = false; }, DRIVING_EASE_MS + 20);
      },
      [],
    );

    // ── Phase 2: overlay tile refresh on camera idle ───────────────────────
    // Ports the WebView's loadBuildingTiles / loadParcelTiles / loadAddressTiles
    // / fetchHouseNumbers pipeline. Pulls the current visible bounds from the
    // native map and feeds GeoJSON state. Self-debounced (each loader returns
    // null when the visible tile set is unchanged) + a busy guard so rapid idle
    // events don't stack network + setData storms during driving.
    const refreshOverlays = useCallback(
      async (centerLng: number, centerLat: number, z: number) => {
        if (overlayBusyRef.current) return;
        const map = mapRef.current;
        if (!map) return;
        overlayBusyRef.current = true;
        try {
          let bounds: Bounds | null = null;
          try {
            bounds = (await map.getBounds()) as Bounds;
          } catch {
            bounds = null;
          }
          if (bounds) {
            // Self-hosted QLD buildings — always on (z ≥ 13).
            const b = await loadBuildingTiles(bounds, z);
            if (b) setBuildingsFC({ type: 'FeatureCollection', features: b });

            // Microsoft Global ML building footprints — worldwide supplement.
            const ms = await loadMsBuildingTiles(bounds, z);
            if (ms) setMsBuildingsFC({ type: 'FeatureCollection', features: ms });

            // Parcels + addresses — only when the Parcels layer is toggled on.
            if (parcelsVisibleRef.current) {
              const p = await loadParcelTiles(bounds, z);
              if (p) setParcelsFC({ type: 'FeatureCollection', features: p });
              const a = await loadAddressTiles(bounds, z);
              if (a) {
                setAddressesFC({
                  type: 'FeatureCollection',
                  features: tagAddressesWithStops(a, stopCoords),
                });
              }
            }
          }
          // House numbers — bbox query around the camera centre (z ≥ 17).
          const hn = await fetchHouseNumbers(centerLng, centerLat, z);
          if (hn) setHouseNumbersFC(hn);
        } finally {
          overlayBusyRef.current = false;
        }
      },
      [stopCoords],
    );

    // ── MS buildings refresh (driving-safe) ────────────────────────────────
    // Called during highFreqCameraActive so footprints still load while the
    // driver is moving. Uses its own busy guard (msBusyRef) so it never
    // contends with the main overlay path. Cache hits in _msBuildingCache are
    // instant, so this only makes network calls for genuinely new z14 tiles.
    const refreshMsBuildings = useCallback(
      async (z: number) => {
        if (msBusyRef.current) return;
        const map = mapRef.current;
        if (!map) return;
        msBusyRef.current = true;
        try {
          let bounds: Bounds | null = null;
          try {
            bounds = (await map.getBounds()) as Bounds;
          } catch {
            bounds = null;
          }
          if (bounds) {
            const ms = await loadMsBuildingTiles(bounds, z);
            if (ms) setMsBuildingsFC({ type: 'FeatureCollection', features: ms });
          }
        } finally {
          msBusyRef.current = false;
        }
      },
      [],
    );

    // ── Phase 3: lasso freehand selection ──────────────────────────────────
    // On release, convert the screen-space path to geo coords via the native
    // map's `unproject`, run point-in-polygon against the (non-completed) stops,
    // and emit `onLassoComplete(stopIds, polygon)`. The parent persists the
    // polygon as a coloured "Section N" via `addSectionPolygon`.
    const finishLasso = useCallback(async () => {
      if (lassoFinishingRef.current) return;
      const screenPts = downsamplePath(lassoPtsRef.current.slice(), 80);
      if (screenPts.length < 3) {
        // Too short to be a polygon — just clear.
        lassoPtsRef.current = [];
        setLassoScreenPts([]);
        return;
      }
      lassoFinishingRef.current = true;
      const map = mapRef.current;
      try {
        if (!map) return;
        // Screen px → geo [lng,lat] for every vertex.
        const geo: number[][] = [];
        for (const p of screenPts) {
          try {
            const ll = (await map.unproject([p.x, p.y])) as [number, number];
            if (Array.isArray(ll) && ll.length >= 2) geo.push([ll[0], ll[1]]);
          } catch {
            // skip un-projectable point
          }
        }
        if (geo.length < 3) return;
        const ring = geo.concat([geo[0]]); // close the polygon
        const ids: string[] = [];
        (stopsRef.current || []).forEach((s) => {
          if (s.completed) return;
          const [lng, lat] = stopLngLat(s);
          if (!Number.isFinite(lng) || !Number.isFinite(lat)) return;
          if (pointInPoly([lng, lat], ring)) ids.push(String(s.id));
        });
        onLassoCompleteRef.current?.(ids, ring);
      } finally {
        lassoFinishingRef.current = false;
      }
    }, []);

    // PanResponder overlay — captures the freehand drag so the map doesn't pan
    // while drawing. Only active when `drawingMode` is on (otherwise the
    // overlay isn't rendered, so normal map gestures are untouched).
    const panResponder = useMemo(
      () =>
        PanResponder.create({
          onStartShouldSetPanResponder: () => drawingModeRef.current,
          onMoveShouldSetPanResponder: () => drawingModeRef.current,
          onPanResponderGrant: (e) => {
            const { locationX, locationY } = e.nativeEvent;
            lassoPtsRef.current = [{ x: locationX, y: locationY }];
            lassoThrottleRef.current = 0;
            setLassoScreenPts(lassoPtsRef.current.slice());
          },
          onPanResponderMove: (e) => {
            if (lassoPtsRef.current.length === 0) return;
            lassoThrottleRef.current++;
            if (lassoThrottleRef.current % 2 !== 0) return; // ~halve the rate
            const { locationX, locationY } = e.nativeEvent;
            lassoPtsRef.current.push({ x: locationX, y: locationY });
            setLassoScreenPts(lassoPtsRef.current.slice());
          },
          onPanResponderRelease: () => {
            finishLasso();
          },
          onPanResponderTerminate: () => {
            finishLasso();
          },
        }),
      [finishLasso],
    );

    //    parent's useNavigationCamera hook owns the camera (no tug-of-war). ──
    useEffect(() => {
      if (highFreqCameraActive) return;
      if (!followDriver || !driverLocation || !cameraRef.current) return;
      if (userInteractingRef.current) return;
      const targetZoom = Math.max(lastZoomRef.current, 16);
      cameraRef.current.easeTo({
        center: [driverLocation.longitude, driverLocation.latitude],
        zoom: targetZoom,
        bearing: driverLocation.heading ?? 0,
        duration: 600,
      });
    }, [followDriver, driverLocation, highFreqCameraActive]);

    // ── Initial camera center on driver location (even when not in navigation mode) ──
    const initialCenterDoneRef = useRef(false);
    useEffect(() => {
      if (initialCenterDoneRef.current) return;
      if (!driverLocation || !cameraRef.current) return;
      // Center camera on user's current location once when map loads
      initialCenterDoneRef.current = true;
      cameraRef.current.flyTo({
        center: [driverLocation.longitude, driverLocation.latitude],
        zoom: 16,
        duration: 800,
      });
    }, [driverLocation]);

    // ── Flatten the camera (pitch → 0) when leaving driving mode ───────────
    // Also prime MS buildings when entering driving mode so the current viewport
    // is populated before refreshOverlays is suppressed.
    useEffect(() => {
      if (!wasDrivingRef.current && highFreqCameraActive) {
        // Entering navigation — load MS buildings for the current viewport now.
        refreshMsBuildings(lastZoomRef.current);
      }
      if (wasDrivingRef.current && !highFreqCameraActive) {
        // Navigation ended — reset so the NEXT session gets a fresh entry zoom.
        drivingZoomSetRef.current = false;
        if (cameraRef.current) {
          try {
            cameraRef.current.setStop({ pitch: 0, duration: 400 });
          } catch {
            // ignore
          }
        }
      }
      wasDrivingRef.current = highFreqCameraActive;
    }, [highFreqCameraActive, refreshMsBuildings]);

    // ── Imperative ref (DeliveryMapRef drop-in) ────────────────────────────
    useImperativeHandle(
      ref,
      (): DeliveryMapRef => ({
        flyTo: (c, opts) => {
          cameraRef.current?.flyTo({
            center: c,
            zoom: opts?.zoom,
            bearing: opts?.bearing,
            pitch: opts?.pitch,
            duration: opts?.duration ?? 1000,
          });
        },
        jumpTo: (c, opts) => {
          cameraRef.current?.jumpTo({
            center: c,
            bearing: opts?.bearing,
            pitch: opts?.pitch,
          });
        },
        fitBounds: (bounds, padding = 48) => {
          // bounds: [[sw_lng, sw_lat], [ne_lng, ne_lat]] → flat [w,s,e,n]
          const [[swLng, swLat], [neLng, neLat]] = bounds;
          cameraRef.current?.fitBounds([swLng, swLat, neLng, neLat], {
            padding: { top: padding, right: padding, bottom: padding, left: padding },
            duration: 600,
          });
        },
        // Planning ⇄ locked pin painter.
        setRouteConfirmed: (confirmed) => setRouteConfirmedState(!!confirmed),
        // Force a full pin recompute (e.g. after POST /routes/confirm).
        forceStopsRefresh: () => setRefreshNonce((n) => n + 1),
        // High-frequency driving camera channel (from useNavigationCamera).
        sendMessage: (msg: any) => {
          if (!msg || typeof msg !== 'object') return;
          if (msg.type === 'drivingCamera') {
            const lng = msg.center ? msg.center[0] : msg.lng;
            const lat = msg.center ? msg.center[1] : msg.lat;
            if (lng != null && lat != null) {
              driveCamera(lng, lat, msg.bearing, msg.speedMps);
            }
          }
          // 'updateDriver' position is rendered by the native UserLocation puck.
        },
        // ── Phase 2 features ──
        // Parcels + address labels overlay (cadastral grid). Toggled from the
        // planning HUD. When enabled we kick an immediate overlay refresh so the
        // grid appears without waiting for the next camera idle.
        toggleParcels: (enabled: boolean) => {
          const on = !!enabled;
          parcelsVisibleRef.current = on;
          setParcelsVisible(on);
          if (on) {
            mapRef.current
              ?.getCenter()
              .then((c) => {
                const cc = c as unknown as [number, number] | { lng: number; lat: number };
                const lng = Array.isArray(cc) ? cc[0] : cc.lng;
                const lat = Array.isArray(cc) ? cc[1] : cc.lat;
                refreshOverlays(lng, lat, lastZoomRef.current);
              })
              .catch(() => {});
          }
        },
        // Delivery clusters (zoomed-out overview). Fed imperatively from the
        // parent whenever the stop set changes — drives the native clustering
        // GeoJSON source. Empty FC clears it.
        setClusters: (fc) => {
          setClustersFC(
            fc && Array.isArray(fc.features)
              ? (fc as GeoJSON.FeatureCollection)
              : EMPTY_FC,
          );
        },
        // ── Phase 3 editing features ──
        // Freehand lasso draw mode. Enabling shows the gesture overlay + clears
        // any previous lasso path; disabling hides it.
        setDrawingMode: (enabled: boolean) => {
          const on = !!enabled;
          drawingModeRef.current = on;
          setDrawingModeState(on);
          if (on) {
            lassoPtsRef.current = [];
            setLassoScreenPts([]);
          }
        },
        // Clear the sticky lasso path (called by the parent after it persists
        // the selection as a section polygon).
        clearLasso: () => {
          lassoPtsRef.current = [];
          setLassoScreenPts([]);
        },
        // Persist a coloured "Section N" polygon (fill + outline + centroid
        // label). Replaces any existing section with the same id.
        addSectionPolygon: (id, coords, color, label) => {
          setSections((prev) => [
            ...prev.filter((s) => s.id !== id),
            { id, coords, color, label },
          ]);
        },
        removeSectionPolygon: (id) => {
          setSections((prev) => prev.filter((s) => s.id !== id));
        },
        clearAllSectionPolygons: () => setSections([]),
        // Tap-to-block: arm a one-shot map tap that the parent turns into a
        // no-go zone via /api/nogo-zones/from-point.
        setBlockRoadMode: (enabled: boolean) => {
          blockRoadRef.current = !!enabled;
        },
        // Replace the rendered no-go zones (red impassable polygons).
        setNogoZones: (zones) => {
          setNogoFC(nogoToFC((zones || []) as NogoZone[]));
        },
        getMap: () => null,
      }),
      [driveCamera, refreshOverlays],
    );

    const handleRegionIsChanging = useCallback((e: any) => {
      try {
        const p = e?.nativeEvent?.payload || e?.nativeEvent || {};
        if (p.userInteraction) {
          userInteractingRef.current = true;
          if (userInteractTimerRef.current) clearTimeout(userInteractTimerRef.current);
          userInteractTimerRef.current = setTimeout(() => {
            userInteractingRef.current = false;
          }, 2000);
        }
      } catch {
        // ignore
      }
    }, []);

    const handleRegionDidChange = useCallback(
      (e: any) => {
        try {
          const payload = e?.nativeEvent?.payload || e?.nativeEvent || {};
          const c = payload.center;
          const z = payload.zoom;
          if (typeof z === 'number') lastZoomRef.current = z;
          const zz = typeof z === 'number' ? z : zoom;
          if (onCameraIdle && Array.isArray(c) && c.length >= 2) {
            onCameraIdle({ lng: c[0], lat: c[1] }, zz);
          }
          // Phase 2: refresh data-driven overlays for the new viewport.
          // During driving, skip the heavy overlays (buildings, parcels, addresses,
          // house numbers) but still refresh MS buildings — cached tiles are instant.
          if (Array.isArray(c) && c.length >= 2) {
            if (!highFreqCameraActiveRef.current) {
              refreshOverlays(c[0], c[1], zz);
            } else {
              refreshMsBuildings(zz);
            }
          }
          // Update pulse ring position to track the camera.
          updatePulseScreenPos();
        } catch {
          // ignore malformed region events
        }
      },
      [onCameraIdle, zoom, refreshOverlays, refreshMsBuildings, updatePulseScreenPos],
    );

    // Handle a tap on the delivery-clusters source: expand a cluster bubble or
    // forward a single (un-clustered) stop tap to the parent.
    const handleClusterPress = useCallback(
      async (e: any) => {
        try {
          const feat = e?.features?.[0] || e?.nativeEvent?.payload?.features?.[0];
          if (!feat) return;
          const props2 = feat.properties || {};
          if (props2.cluster || props2.point_count != null) {
            const clusterId = props2.cluster_id;
            const coords = feat.geometry?.coordinates;
            if (clusterId != null && clusterSourceRef.current && Array.isArray(coords)) {
              try {
                const targetZoom =
                  await clusterSourceRef.current.getClusterExpansionZoom(clusterId);
                cameraRef.current?.easeTo({
                  center: coords as [number, number],
                  zoom: targetZoom,
                  duration: 500,
                });
              } catch {
                // ignore expansion failures
              }
            }
          } else if (props2.id && onStopClick) {
            onStopClick(String(props2.id));
          }
        } catch {
          // ignore
        }
      },
      [onStopClick],
    );

    const handleStopsPress = useCallback(
      (e: any) => {
        try {
          const feat = e?.features?.[0] || e?.nativeEvent?.payload?.features?.[0];
          const id = feat?.properties?.id;
          if (id && onStopClick) onStopClick(String(id));
        } catch {
          // ignore
        }
      },
      [onStopClick],
    );

    // Map-level tap: in block-road mode the next tap becomes a no-go zone
    // centre. Single-shot — disarmed immediately; the parent re-arms after the
    // server confirms the new zone.
    const handleMapPress = useCallback(
      (e: any) => {
        if (!blockRoadRef.current) return;
        try {
          const ll = e?.nativeEvent?.lngLat || e?.nativeEvent?.payload?.lngLat;
          if (Array.isArray(ll) && ll.length >= 2) {
            blockRoadRef.current = false;
            props.onBlockRoadTap?.(ll[1], ll[0]); // (lat, lng)
          }
        } catch {
          // ignore
        }
      },
      [props],
    );

    // Tap a no-go zone polygon → ask the parent to confirm deletion (skipped
    // while a draw/block mode is active so it doesn't fight those gestures).
    const handleNogoPress = useCallback(
      (e: any) => {
        if (drawingModeRef.current || blockRoadRef.current) return;
        try {
          const feat = e?.features?.[0] || e?.nativeEvent?.payload?.features?.[0];
          const p = feat?.properties;
          if (p?.id) props.onNogoZoneClick?.(String(p.id), p.name || '');
        } catch {
          // ignore
        }
      },
      [props],
    );

    const ringColor = nextStopColor || '#f59e0b';

    // SVG points string for the screen-space lasso overlay.
    const lassoPointsStr = useMemo(
      () => lassoScreenPts.map((p) => `${p.x},${p.y}`).join(' '),
      [lassoScreenPts],
    );

    return (
      <View
        style={styles.container}
        onLayout={(e) => {
          const h = e?.nativeEvent?.layout?.height;
          if (typeof h === 'number' && h > 0) {
            mapHeightRef.current = h;
            setMapHeight(h);
          }
        }}
      >
        <MapLibreMap
          ref={mapRef}
          style={styles.map}
          mapStyle={props.mapStyle || MAP_STYLE}
          compass
          compassPosition={{ top: 8, right: 8 }}
          logo={false}
          attribution
          attributionPosition={{ bottom: 8, right: 8 }}
          onDidFinishLoadingMap={() => onMapReady?.()}
          onRegionDidChange={handleRegionDidChange}
          onRegionIsChanging={handleRegionIsChanging}
          onPress={handleMapPress}
        >
          <Camera
            ref={cameraRef}
            initialViewState={{ center, zoom }}
          />

          {/* ── 3D buildings — worldwide OSM (style's openmaptiles vector
              source). Always visible ≥ z13; height ramps 0→full by z16. ── */}
          <Layer
            id="buildings-3d"
            type="fill-extrusion"
            source="openmaptiles"
            source-layer="building"
            minzoom={13}
            filter={['!=', ['get', 'hide_3d'], true]}
            paint={{
              'fill-extrusion-color': [
                'interpolate', ['linear'],
                ['coalesce', ['to-number', ['get', 'render_height']], 8],
                0, '#d4d4d8', 15, '#a1a1aa', 40, '#78716c', 100, '#64748b',
              ],
              'fill-extrusion-height': [
                'interpolate', ['linear'], ['zoom'],
                13, 0,
                15, ['*', 0.5, ['coalesce', ['to-number', ['get', 'render_height']], 8]],
                16, ['coalesce', ['to-number', ['get', 'render_height']], 8],
              ],
              'fill-extrusion-base': [
                'case', ['>=', ['zoom'], 15],
                ['coalesce', ['to-number', ['get', 'render_min_height']], 0], 0,
              ],
              'fill-extrusion-opacity': [
                'interpolate', ['linear'], ['zoom'], 13, 0.3, 15, 0.55, 17, 0.7,
              ],
            }}
          />
          
          {/* ── OSM Building outlines for better visibility ── */}
          <Layer
            id="buildings-outline"
            type="line"
            source="openmaptiles"
            source-layer="building"
            minzoom={15}
            layout={{
              'line-join': 'round',
              'line-cap': 'round',
            }}
            paint={{
              'line-color': '#475569',
              'line-width': ['interpolate', ['linear'], ['zoom'], 15, 0.5, 17, 1, 19, 1.5],
              'line-opacity': ['interpolate', ['linear'], ['zoom'], 15, 0.4, 17, 0.7, 19, 0.9],
            }}
          />

          {/* ── Self-hosted QLD buildings (cadastre-derived). Flat-fill safety
              net + 3D extrusion overlay, fed by /api/tiles/buildings. ── */}
          <GeoJSONSource id="buildings-self-src" data={buildingsFC}>
            <Layer
              id="buildings-self-fill"
              type="fill"
              minzoom={14}
              paint={{
                'fill-color': '#9ca3af',
                'fill-opacity': ['interpolate', ['linear'], ['zoom'], 14, 0.25, 16, 0.35, 18, 0.45],
                'fill-outline-color': '#6b7280',
              }}
            />
            <Layer
              id="buildings-self-3d"
              type="fill-extrusion"
              minzoom={13}
              paint={{
                'fill-extrusion-color': [
                  'interpolate', ['linear'],
                  ['coalesce', ['to-number', ['get', 'render_height']], 8],
                  0, '#d4d4d8', 6, '#c4b5a0', 15, '#a1a1aa', 40, '#78716c', 100, '#64748b',
                ],
                'fill-extrusion-height': [
                  'interpolate', ['linear'], ['zoom'],
                  13, 0,
                  15, ['*', 0.5, ['coalesce', ['to-number', ['get', 'render_height']], 8]],
                  16, ['coalesce', ['to-number', ['get', 'render_height']], 8],
                ],
                'fill-extrusion-base': ['coalesce', ['to-number', ['get', 'render_min_height']], 0],
                'fill-extrusion-opacity': ['interpolate', ['linear'], ['zoom'], 13, 0.35, 15, 0.65, 17, 0.8],
              }}
            />
            {/* ── Self-hosted building outlines ── */}
            <Layer
              id="buildings-self-outline"
              type="line"
              minzoom={15}
              layout={{
                'line-join': 'round',
                'line-cap': 'round',
              }}
              paint={{
                'line-color': '#334155',
                'line-width': ['interpolate', ['linear'], ['zoom'], 15, 0.8, 17, 1.2, 19, 2],
                'line-opacity': ['interpolate', ['linear'], ['zoom'], 15, 0.5, 17, 0.8, 19, 1],
              }}
            />
          </GeoJSONSource>

          {/* ── Cadastral parcel boundaries (toggleable). ── */}
          {parcelsVisible && (
            <GeoJSONSource id="parcels-src" data={parcelsFC}>
              <Layer
                id="parcels-fill"
                type="fill"
                minzoom={15}
                paint={{ 'fill-color': '#9ca3af', 'fill-opacity': 0.03 }}
              />
              <Layer
                id="parcels-line"
                type="line"
                minzoom={15}
                layout={{ 'line-join': 'round', 'line-cap': 'round' }}
                paint={{
                  'line-color': '#6b7280',
                  'line-width': ['interpolate', ['linear'], ['zoom'], 15, 0.6, 17, 1.0],
                  'line-opacity': 0.55,
                }}
              />
            </GeoJSONSource>
          )}

          {/* ── Property street numbers (toggleable with parcels). Muted
              neighbourhood context + bolder red labels on delivery targets. ── */}
          {parcelsVisible && (
            <GeoJSONSource id="addresses-src" data={addressesFC}>
              <Layer
                id="address-label"
                type="symbol"
                minzoom={15.5}
                filter={['!=', ['get', 'isStop'], true]}
                layout={{
                  'text-field': ['get', 'street_number'],
                  'text-size': ['interpolate', ['linear'], ['zoom'], 15.5, 11, 17, 13, 19, 15],
                  'text-font': ['Noto Sans Bold', 'noto_sans_bold', 'Arial Unicode MS Bold'],
                  'text-allow-overlap': true,
                  'text-ignore-placement': true,
                  'text-offset': [0, 0.3],
                }}
                paint={{
                  'text-color': '#64748b',
                  'text-halo-color': '#ffffff',
                  'text-halo-width': 1.6,
                  'text-opacity': ['interpolate', ['linear'], ['zoom'], 15.5, 0.65, 16, 0.85],
                }}
              />
              <Layer
                id="address-label-stops"
                type="symbol"
                minzoom={15}
                filter={['==', ['get', 'isStop'], true]}
                layout={{
                  'text-field': ['get', 'street_number'],
                  'text-size': ['interpolate', ['linear'], ['zoom'], 15, 13, 17, 17, 19, 20],
                  'text-font': ['Noto Sans Bold', 'noto_sans_bold', 'Arial Unicode MS Bold'],
                  'text-allow-overlap': true,
                  'text-ignore-placement': true,
                  'text-offset': [0, 0.3],
                  'symbol-sort-key': 0,
                }}
                paint={{
                  'text-color': '#b91c1c',
                  'text-halo-color': '#ffffff',
                  'text-halo-width': 2.4,
                  'text-halo-blur': 0.2,
                  'text-opacity': 1,
                }}
              />
            </GeoJSONSource>
          )}

          {/* ── House numbers (global OSM, ≥ z17.5). ── */}
          <GeoJSONSource id="house-numbers-src" data={houseNumbersFC}>
            <Layer
              id="house-numbers"
              type="symbol"
              minzoom={17.5}
              layout={{
                'text-field': ['get', 'housenumber'],
                'text-font': ['Noto Sans Bold', 'noto_sans_bold', 'Arial Unicode MS Bold'],
                'text-size': ['interpolate', ['linear'], ['zoom'], 17, 12, 20, 16],
                'text-padding': 2,
                'text-allow-overlap': false,
                'text-ignore-placement': false,
                'text-rotation-alignment': 'map',
                'text-pitch-alignment': 'viewport',
                'text-anchor': 'bottom',
                'text-offset': [0, -0.2],
              }}
              paint={{
                'text-color': '#111827',
                'text-halo-color': '#ffffff',
                'text-halo-width': 2,
                'text-halo-blur': 0.3,
                'text-opacity': ['interpolate', ['linear'], ['zoom'], 17, 0, 17.5, 0.4, 18, 1],
              }}
            />
          </GeoJSONSource>

          {/* ── Microsoft Global ML Building Footprints (worldwide supplement).
              Fills in suburbs where the QLD cadastre data is sparse.
              The backend enriches each feature with:
                height_est    – best-available height in metres (footprint-area
                                heuristic when the dataset height ≤ 0)
                confidence_norm – dataset confidence clamped [0,1]; 0.5 for
                                  unknown buildings
              Color band and opacity are driven by height_est / confidence_norm
              so commercial/large footprints render visibly taller. ── */}
          <GeoJSONSource id="ms-buildings-src" data={msBuildingsFC}>
            <Layer
              id="ms-buildings-3d"
              type="fill-extrusion"
              minzoom={13}
              paint={{
                'fill-extrusion-color': [
                  'interpolate', ['linear'],
                  ['coalesce', ['to-number', ['get', 'height_est']], 6],
                  0, '#e2e8f0', 6, '#cbd5e1', 10, '#b0bec5', 15, '#90a4ae', 25, '#78909c', 50, '#78716c',
                ],
                'fill-extrusion-height': [
                  'interpolate', ['linear'], ['zoom'],
                  13, 0,
                  15, ['*', 0.5, ['coalesce', ['to-number', ['get', 'height_est']], 6]],
                  16, ['coalesce', ['to-number', ['get', 'height_est']], 6],
                ],
                'fill-extrusion-base': 0,
                'fill-extrusion-opacity': [
                  '*',
                  ['interpolate', ['linear'], ['zoom'], 13, 0.2, 15, 0.65, 17, 0.85],
                  [
                    '+', 0.55,
                    ['*', 0.45, ['coalesce', ['to-number', ['get', 'confidence_norm']], 0.5]],
                  ],
                ],
              }}
            />
            <Layer
              id="ms-buildings-outline"
              type="line"
              minzoom={15}
              layout={{ 'line-join': 'round', 'line-cap': 'round' }}
              paint={{
                'line-color': '#475569',
                'line-width': ['interpolate', ['linear'], ['zoom'], 15, 0.5, 17, 1, 19, 1.5],
                'line-opacity': ['interpolate', ['linear'], ['zoom'], 15, 0.35, 17, 0.65, 19, 0.85],
              }}
            />
          </GeoJSONSource>

          {/* ── Driveway hints: dashed connector from pin → access point +
              a purple dot at the access point (parity with WebView). ── */}
          <GeoJSONSource id="driveway-hints-src" data={drivewayFC}>
            <Layer
              id="driveway-hints-line"
              type="line"
              filter={['==', ['geometry-type'], 'LineString']}
              layout={{ 'line-join': 'round', 'line-cap': 'round' }}
              paint={{
                'line-color': '#a855f7',
                'line-width': 1.5,
                'line-opacity': 0.7,
                'line-dasharray': [2, 2],
              }}
            />
            <Layer
              id="driveway-hints-dot"
              type="circle"
              filter={['==', ['geometry-type'], 'Point']}
              paint={{
                'circle-color': '#a855f7',
                'circle-radius': 4.5,
                'circle-stroke-color': '#ffffff',
                'circle-stroke-width': 1.5,
                'circle-opacity': 0.95,
              }}
            />
          </GeoJSONSource>

          {/* Traveled breadcrumb (behind the active route) */}
          <GeoJSONSource id="traveled-src" data={traveledFC}>
            <Layer
              id="traveled-line"
              type="line"
              layout={{ 'line-cap': 'round', 'line-join': 'round' }}
              paint={{ 'line-color': '#94a3b8', 'line-width': 4, 'line-opacity': 0.7 }}
            />
          </GeoJSONSource>

          {/* Direction arrow icon for route + teardrop marker icons + Waze-style nav puck + turn indicators */}
          <Images
            images={{
              'route-arrow': require('../../../assets/images/route-arrow.png'),
              'marker-blue': require('../../../assets/images/marker-blue.png'),
              'marker-green': require('../../../assets/images/marker-green.png'),
              'marker-navy': require('../../../assets/images/marker-navy.png'),
              'marker-purple': require('../../../assets/images/marker-purple.png'),
              'nav-puck': require('../../../assets/images/nav-puck.png'),
              'mlrn-user-location-puck-heading': require('../../../assets/images/nav-puck.png'),
              'turn-left': require('../../../assets/images/turn-left.png'),
              'turn-right': require('../../../assets/images/turn-right.png'),
            }}
          />

          {/* Active / preview route polyline — rendered by RouteLine (React.memo)
              so GPS-driven parent re-renders never touch the GeoJSONSource */}
          <RouteLine routeFC={routeFC} routeIsPreview={routeIsPreview} />

          {/* Turn indicators disabled - route-arrows along the line are sufficient */}
          {/* <GeoJSONSource id="turn-points-src" data={turnPointsFC}>
            <Layer
              id="turn-indicators"
              type="symbol"
              minzoom={12}
              layout={{
                'icon-image': ['get', 'turnType'],
                'icon-size': 1.0,
                'icon-allow-overlap': true,
                'icon-ignore-placement': true,
              }}
              paint={{
                'icon-opacity': 1,
              }}
            />
          </GeoJSONSource> */}

          {/* Next-stop pulse ring */}
          <GeoJSONSource id="next-ring-src" data={nextRingFC}>
            <Layer
              id="next-ring"
              type="circle"
              paint={{
                'circle-radius': 22,
                'circle-color': ringColor,
                'circle-opacity': 0.25,
                'circle-stroke-width': 2,
                'circle-stroke-color': ringColor,
              }}
            />
          </GeoJSONSource>

          {/* ── No-go zones (red impassable polygons). Tap to delete. ── */}
          <GeoJSONSource id="nogo-zones" data={nogoFC} onPress={handleNogoPress}>
            <Layer
              id="nogo-zones-fill"
              type="fill"
              paint={{ 'fill-color': '#dc2626', 'fill-opacity': 0.28 }}
            />
            <Layer
              id="nogo-zones-line"
              type="line"
              paint={{
                'line-color': '#dc2626',
                'line-width': 2,
                'line-dasharray': [3, 2],
                'line-opacity': 0.95,
              }}
            />
          </GeoJSONSource>

          {/* ── Driver-drawn section polygons (coloured fill + outline + label
              at centroid). One source per section. ── */}
          {sections.map((s) => (
            <GeoJSONSource key={`section-${s.id}`} id={`section-${s.id}`} data={sectionToFC(s)}>
              <Layer
                id={`section-${s.id}-fill`}
                type="fill"
                filter={['==', ['geometry-type'], 'Polygon']}
                paint={{ 'fill-color': s.color, 'fill-opacity': 0.18 }}
              />
              <Layer
                id={`section-${s.id}-line`}
                type="line"
                filter={['==', ['geometry-type'], 'Polygon']}
                layout={{ 'line-join': 'round', 'line-cap': 'round' }}
                paint={{ 'line-color': s.color, 'line-width': 2.5, 'line-opacity': 0.85 }}
              />
              <Layer
                id={`section-${s.id}-lbl`}
                type="symbol"
                filter={['==', ['geometry-type'], 'Point']}
                layout={{
                  'text-field': ['get', 'label'],
                  'text-size': 13,
                  'text-font': ['Noto Sans Bold', 'noto_sans_bold', 'Arial Unicode MS Bold'],
                  'text-allow-overlap': true,
                }}
                paint={{ 'text-color': s.color, 'text-halo-color': '#ffffff', 'text-halo-width': 2 }}
              />
            </GeoJSONSource>
          ))}

          {/* Delivery stops: teardrop marker pins. When cluster data is present they
              hide below the swap zoom so the cluster bubbles take over. */}
          <GeoJSONSource id="stops-src" data={stopsFC} onPress={handleStopsPress}>
            {/* Teardrop marker icons - slightly bigger with dark text for contrast */}
            <Layer
              id="stops-marker"
              type="symbol"
              minzoom={hasClusterData ? CLUSTER_SWAP_ZOOM : undefined}
              layout={{
                'icon-image': ['get', 'marker'],
                'icon-size': 0.85,
                'icon-anchor': 'bottom',
                'icon-allow-overlap': true,
                'icon-ignore-placement': true,
                'text-field': ['get', 'label'],
                'text-font': ['Noto Sans Bold', 'noto_sans_bold', 'Arial Unicode MS Bold'],
                'text-size': 14,
                'text-anchor': 'center',
                'text-offset': [0, -3.1],
                'text-allow-overlap': true,
                'text-ignore-placement': true,
              }}
              paint={{
                'text-color': '#0f172a',
                'text-halo-color': 'rgba(255,255,255,0)',
                'text-halo-width': 0,
              }}
            />
          </GeoJSONSource>

          {/* ── Delivery clusters (zoomed-out overview). Native clustering
              GeoJSON source fed imperatively via setClusters(). Cluster bubbles
              + counts render below the swap zoom; un-clustered singles always.
              maxzoom on the cluster layers performs the pin⇄cluster swap. ── */}
          {hasClusterData && (
            <GeoJSONSource
              id="delivery-clusters"
              ref={clusterSourceRef}
              data={clustersFC}
              cluster
              clusterRadius={60}
              clusterMaxZoom={CLUSTER_SWAP_ZOOM}
              clusterProperties={{ parcels: ['+', ['coalesce', ['get', 'parcel_count'], 1]] }}
              onPress={handleClusterPress}
            >
              <Layer
                id="clusters"
                type="circle"
                maxzoom={CLUSTER_SWAP_ZOOM}
                filter={['has', 'point_count']}
                paint={{
                  'circle-color': '#0b2545',
                  'circle-stroke-color': '#ffffff',
                  'circle-stroke-width': 2,
                  'circle-radius': ['step', ['get', 'point_count'], 18, 25, 24, 100, 32],
                }}
              />
              <Layer
                id="cluster-count"
                type="symbol"
                maxzoom={CLUSTER_SWAP_ZOOM}
                filter={['has', 'point_count']}
                layout={{
                  'text-field': ['get', 'point_count_abbreviated'],
                  'text-font': ['Noto Sans Bold', 'noto_sans_bold', 'Arial Unicode MS Bold'],
                  'text-size': 14,
                  'text-allow-overlap': true,
                }}
                paint={{ 'text-color': '#ffffff' }}
              />
              <Layer
                id="cluster-point"
                type="circle"
                maxzoom={CLUSTER_SWAP_ZOOM}
                filter={['!', ['has', 'point_count']]}
                paint={{
                  'circle-color': '#1d4ed8',
                  'circle-stroke-color': '#ffffff',
                  'circle-stroke-width': 2,
                  'circle-radius': 7,
                }}
              />
            </GeoJSONSource>
          )}

          {/* Late Freight Zipper overlay — sky-blue line + amber late-freight pins */}
          {props.zipperRoute && props.zipperRoute.length >= 2 && (
            <ZipperRouteLayer route={props.zipperRoute} />
          )}

          {/* Waze-style navigation puck with heading rotation */}
          <UserLocation
            animated
            heading={true}
            minDisplacement={3}
          />
        </MapLibreMap>

        {/* ── Animated pulse ring overlay (screen-space) ────────────────────────
            A reanimated View that renders a pulsing ring at the screen position
            of the next stop. This overlay animates smoothly at 60fps while the
            MapLibre circle layer underneath provides the static base ring. */}
        {pulseScreenXY && nextStopCoord && (
          <Animated.View
            pointerEvents="none"
            style={[
              {
                position: 'absolute',
                left: pulseScreenXY.x - 24,
                top: pulseScreenXY.y - 24,
                width: 48,
                height: 48,
                borderRadius: 24,
                borderWidth: 3,
                borderColor: nextStopColor || '#f59e0b',
                backgroundColor: 'transparent',
              },
              pulseAnimatedStyle,
            ]}
          />
        )}

        {/* ── Lasso drawing overlay ──────────────────────────────────────────
            Rendered ONLY while in draw mode so normal map gestures are
            untouched otherwise. The PanResponder captures the freehand drag
            (so the map doesn't pan) and an SVG paints the orange dashed path /
            translucent fill in screen space; on release `finishLasso` projects
            it to geo coords. */}
        {drawingMode && (
          <View
            style={StyleSheet.absoluteFill}
            {...panResponder.panHandlers}
            pointerEvents="auto"
          >
            <Svg style={StyleSheet.absoluteFill} pointerEvents="none">
              {lassoScreenPts.length >= 3 && (
                <SvgPolygon
                  points={lassoPointsStr}
                  fill="#f97316"
                  fillOpacity={0.12}
                  stroke="none"
                />
              )}
              {lassoScreenPts.length >= 2 && (
                <SvgPolyline
                  points={lassoPointsStr}
                  fill="none"
                  stroke="#f97316"
                  strokeWidth={3}
                  strokeDasharray="4,3"
                  strokeLinejoin="round"
                  strokeLinecap="round"
                />
              )}
            </Svg>
          </View>
        )}
      </View>
    );
  },
);

DeliveryMapNativeInner.displayName = 'DeliveryMapNative';

export const DeliveryMapNative = React.memo(DeliveryMapNativeInner);
export default DeliveryMapNative;

const styles = StyleSheet.create({
  container: { flex: 1 },
  map: { flex: 1 },
});
