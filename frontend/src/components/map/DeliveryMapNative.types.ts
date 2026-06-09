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

/** Minimal stop shape used by the Late Freight Zipper route overlay. */
export type ZipperStop = {
  id: string;
  label: string;
  lat: number;
  lon: number;
  original_sequence: number | null;
  is_late_freight: boolean;
};

/**
 * Data + callbacks for the on-map stop callout balloon. The balloon is
 * rendered as a MapLibre `Marker` anchored to the stop's coordinate, so it
 * tracks the pin natively as the map pans/zooms. The editable address state
 * and the save/regeocode handlers live in the parent screen (index.tsx) and
 * are threaded in here — the map just renders the card at the right spot.
 */
export interface MapCallout {
  id: string;
  lng: number;
  lat: number;
  /** Pin number/glyph — matches the label painted on the tapped pin. */
  label: string;
  completed: boolean;
  weight?: number | null;
  /** Current (editable) address value, owned by the parent. */
  address: string;
  needsFix?: boolean;
  saving?: boolean;
  regeocoding?: boolean;
  onAddressChange: (text: string) => void;
  onSave: () => void;
  onRegeocode: () => void;
  onClose: () => void;
  /** Optional — open the full detail sheet (delete / complete / navigate). */
  onDetails?: () => void;
}

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
  /** When set, renders the editable stop callout balloon anchored to the pin. */
  callout?: MapCallout | null;
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
  zipperRoute?: ZipperStop[];
}
