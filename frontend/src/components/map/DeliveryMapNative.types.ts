/**
 * DeliveryMapNative.types.ts — shared, dependency-free prop contract for the
 * native MapLibre map. Lives in its own module so the WEB stub
 * (`DeliveryMapNative.tsx`) can reuse the type WITHOUT importing the native
 * (`.native.tsx`) file that pulls in `@maplibre/maplibre-react-native`.
 */
import type {
  DeliveryStop,
  DriverLocation,
  NextTurnInfo,
} from '../DeliveryMap';

/** Superset mirror of the WebView map's prop contract. */
export interface DeliveryMapNativeProps {
  stops: DeliveryStop[];
  routeCoordinates: number[][] | null;
  routeIsPreview?: boolean;
  driverLocation: DriverLocation | null;
  traveledPath: number[][] | null;
  mapStyle?: string;
  initialCenter?: [number, number];
  initialZoom?: number;
  followDriver?: boolean;
  onStopClick?: (stopId: string) => void;
  onCameraIdle?: (center: { lng: number; lat: number }, zoom: number) => void;
  onMapReady?: () => void;
  onLassoComplete?: (stopIds: string[], polygon: number[][]) => void;
  onBlockRoadTap?: (lat: number, lng: number) => void;
  onNogoZoneClick?: (id: string, name: string) => void;
  drawingMode?: boolean;
  speed?: number | null;
  etaMinutes?: number | null;
  distanceRemaining?: string | null;
  nextTurn?: NextTurnInfo | null;
  nextStopCoord?: [number, number] | null;
  nextStopColor?: string | null;
  highFreqCameraActive?: boolean;
}
