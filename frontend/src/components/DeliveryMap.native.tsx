/**
 * DeliveryMap.native.tsx — React Native (Android/iOS) implementation.
 *
 * Phase 4 cutover: This file now simply re-exports the native MapLibre
 * implementation from `DeliveryMapNative.native.tsx`. The legacy WebView-based
 * implementation (MapLibre GL JS inside a WebView) has been removed.
 *
 * The native SDK (`@maplibre/maplibre-react-native` v11) provides:
 *   - True 60 fps hardware-accelerated map rendering
 *   - Native gesture handling (pan, pinch-zoom, rotate, tilt)
 *   - Lower memory footprint vs. WebView + JS engine
 *   - Direct access to device GPS via native UserLocation puck
 *   - Full parity with the legacy WebView features (stops, route polyline,
 *     traveled breadcrumb, 3D buildings, cadastral parcels, house numbers,
 *     delivery clusters, lasso drawing, section polygons, no-go zones,
 *     driveway hints, next-stop pulse ring, driving camera).
 *
 * The same `DeliveryMapRef` interface is preserved so parent components
 * require no code changes.
 *
 * ⚠️ Native module — does NOT run in Expo Go or the web preview. Requires an
 * EAS development/production build to render.
 */
export {
  DeliveryMapNative as DeliveryMap,
  DeliveryMapNative as default,
} from './map/DeliveryMapNative';

// Re-export types from the shared web contract so consumers can import
// { DeliveryStop, DriverLocation, DeliveryMapRef, ... } from this file
// exactly as before.
export type {
  DeliveryStop,
  DriverLocation,
  NextTurnInfo,
  DeliveryMapRef,
} from './DeliveryMap';
