# RouTeD — Map Migration: WebView MapLibre GL JS → Native `@maplibre/maplibre-react-native`

> Handoff doc for the agent doing the migration (target agent: **E2**).
> Source of truth for app context remains `/app/memory/PRD.md`. This file is
> the focused phase checklist for the map migration only.

## Goal
Replace the WebView-based map with the **native** MapLibre SDK, to **full
feature parity**, behind a `useNativeMap` feature flag, WITHOUT breaking the
existing WebView map until parity is reached. Drivers: puck jitter/GPS
smoothness, performance with large stop counts, and maintainability (escape
the 2,700-line HTML string).

## Why native MapLibre (not Mapbox/Google)
- Keeps the **free/open** stack: OpenFreeMap "Liberty" vector tiles + self-hosted
  OSRM. No Mapbox MAU / Google per-load billing.
- **Style/layer/expression parity** with the current `maplibre-gl` WebView code
  → existing layers port ~1:1.
- Native `UserLocation` gives `setMinDisplacement` + `renderMode="gps"` —
  directly replaces the JS puck-jitter workarounds.

## Current architecture (what we're migrating FROM)
- `frontend/src/components/DeliveryMap.native.tsx` (~2,700 lines): MapLibre GL JS
  (`maplibre-gl@5.22`) inside `react-native-webview@13.15`. HTML string +
  `injectJavaScript`/`postMessage` bridge (~25 message types).
- `frontend/src/components/DeliveryMap.tsx`: web fallback (maplibre-gl direct).
- Style: OpenFreeMap Liberty (`https://tiles.openfreemap.org/styles/liberty`),
  sprites/glyphs proxied by backend.
- Routing: OSRM (`pathpilot-osrm.fly.dev`) via `/api/directions` + `/api/optimize`.
- Backend: FastAPI → GitHub → Coolify (`https://api.getrouted.xyz`).
- Frontend ships via EAS OTA; **native deps require an EAS APK rebuild**
  (user confirmed builds are reliable). EAS helper:
  `frontend/scripts/eas-update-guarded.js`.

## Feature inventory to port (16 sources / ~25 layers)
| WebView source/layer | Native equivalent | Difficulty |
|---|---|---|
| `stops-icon` pins + drive-order number sprites | `ShapeSource`+`SymbolLayer`+`Images` | Med (sprite gen differs) |
| `stops-pending-dot` | `CircleLayer` | Low |
| route: casing/line/pulse/completed/upcoming/chase | `ShapeSource`+`LineLayer` ×N | Low |
| `traveled` breadcrumb | `LineLayer` | Low |
| `driver` puck + bearing lerp | **native `UserLocation` (renderMode gps)** | Low → win |
| driving camera (look-ahead, pitch-60, follow course) | `Camera` (followUserMode/pitch/bearing) | Low–Med |
| `buildings-3d` / `buildings-self-3d` | `FillExtrusionLayer` (sourceLayerID) | Med |
| `parcels` (cadastral) | `FillLayer`/`LineLayer` (minzoom 15) | Low–Med |
| `address-label`, `address-label-stops`, `house-numbers` | `SymbolLayer` | Low–Med |
| `next-stop` pulse/core | `CircleLayer` | Low |
| `delivery-clusters` (cluster/count/point) | `ShapeSource cluster` + layers | Low |
| **`nogo-zones` (tap-to-draw polygons)** | `onPress` + GeoJSON state + `FillLayer` | **High** |
| **`lasso` (freehand select)** | `PanResponder` + screen→coord projection | **High** |

Bridge messages today (must keep equivalent behaviour): updateStops,
updateRoute, updateTraveled, appendTraveled, updateDriver, updateHUD,
drivingCamera, setDrivingMode, setNextStop, setClusters, setNogoZones,
setRouteConfirmed, setDrawingMode, setBlockRoadMode, addSectionPolygon,
removeSectionPolygon, clearAllSectionPolygons, toggleParcels,
updateHouseNumbers, clearLasso, flyTo, jumpTo, fitBounds, celebrateCompletion,
resetCompletionCelebration.

## Phases (do in order)
- [ ] **Phase 0 — Spike (de-risk first):** add `@maplibre/maplibre-react-native`
      + Expo config plugin; prebuild dev client; render a bare native `MapView`
      with the Liberty style + `stops` pins + native `UserLocation` puck;
      trigger an EAS APK build; confirm runs + puck smoothness on a real device.
- [ ] **Phase 1 — Driving parity:** pins, route lines (all sub-layers), traveled,
      native puck (setMinDisplacement/renderMode gps), driving `Camera`
      (look-ahead + pitch + course follow). ~80% of on-road value.
- [ ] **Phase 2 — Overlays:** clusters, next-stop pulse, address labels, house
      numbers, 3D buildings (FillExtrusion from OpenMapTiles `building` layer).
- [ ] **Phase 3 — Editing tools:** no-go zone draw (tap-to-add vertices),
      lasso freehand select, parcels.
- [ ] **Phase 4 — Cutover:** gate both maps behind `useNativeMap`, A/B on device,
      then delete the WebView (`DeliveryMap.native.tsx`) path.

## Hard constraints
- NO paid map vendors — keep OpenFreeMap tiles + OSRM.
- Preserve EVERY current feature and these contracts:
  - `nav.legs[i]` shape from `/api/directions` (incl. Sugar Bag Rd coalescing).
  - Stop pin numbers come from `original_sequence` (see
    `frontend/src/utils/stopPinNumber.ts`); late-freight stops show 45A/45B via
    `buildLateFreightLabels` / `getDisplaySequence`. Apply to native pins too.
  - Off-route line re-snap (40 m `SNAP_RADIUS_M` in `index.tsx`) behaviour.
- Don't regress the OTA path for non-map JS.
- Use existing EAS build scripts/patterns in the repo.

## Pre-flight before starting
1. Ensure the **backend OSRM directions fix** is deployed to Coolify
   (Save to GitHub). Verify `GET https://api.getrouted.xyz/api/directions?...`
   returns `"source":"osrm"`.
2. Read `/app/memory/PRD.md` for the full change history.

## Effort estimate
Full parity ≈ 3–5 focused weeks. Phase 0+1 (usable native driving map) ≈ 1–1.5
weeks. Recommend **forking the chat per phase** to keep context/credits lean.

## Credentials
See `/app/memory/test_credentials.md`. Admin: `xmltvg@gmail.com`.
