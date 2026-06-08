/**
 * Late Freight — React Native client layer
 * ----------------------------------------
 * Two concerns, deliberately separated to protect the bridge:
 *
 *   useLateFreightZipper()  – owns the network call + the planned route. The
 *                             route is a LOW-frequency value (changes only when
 *                             a parcel is added), so it lives in useState.
 *
 *   <ZipperRouteLayer/>     – renders the GeoJSON line. It is React.memo'd and
 *                             reads ONLY the route, never GPS. High-frequency
 *                             puck movement must never pass through here or the
 *                             whole line re-serialises across the bridge on
 *                             every fix and the UI jitters.
 *
 * The puck and camera follow are delegated to native via <UserLocation/> +
 * followUserLocation. We never push static camera props while following.
 */

import { useCallback, useMemo, useRef, useState } from "react";
import { GeoJSONSource, Layer } from "@maplibre/maplibre-react-native";

const API = process.env.EXPO_PUBLIC_BACKEND_URL || "https://api.getrouted.xyz";

export type PlannedStop = {
  id: string;
  label: string;            // "12", "45A" ...
  lat: number;
  lon: number;
  original_sequence: number | null;
  is_late_freight: boolean;
};

type ZipperResponse =
  | { ok: true; total_distance_m: number; inserted_labels: string[]; route: PlannedStop[] }
  | { ok: false; error: string; detail?: string };

type ZipStopInput = Omit<PlannedStop, "label" | "is_late_freight"> & { is_depot?: boolean };

export function useLateFreightZipper() {
  const [route, setRoute] = useState<PlannedStop[]>([]);
  const [inserting, setInserting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Coalesce rapid taps: if the driver fires off two parcels in a second we
  // only honour the latest in-flight request and drop the stale one.
  const reqId = useRef(0);

  const zip = useCallback(
    async (stops: ZipStopInput[]): Promise<PlannedStop[] | null> => {
      const mine = ++reqId.current;
      setInserting(true);
      setError(null);
      try {
        const res = await fetch(`${API}/api/route/zipper`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ stops, return_to_depot: false, time_limit_s: 5 }),
        });
        const data: ZipperResponse = await res.json();
        if (mine !== reqId.current) return null;   // a newer request superseded us
        if (!data.ok) {
          setError(data.error);
          return null;
        }
        setRoute(data.route);
        return data.route;
      } catch (e: any) {
        if (mine === reqId.current) setError(e?.message ?? "network");
        return null;
      } finally {
        if (mine === reqId.current) setInserting(false);
      }
    },
    [],
  );

  return { route, inserting, error, zip };
}

/**
 * Bridge-safe route line. Memoised on the route reference only. The parent
 * may re-render 10×/sec from GPS; this component will not re-cross the bridge
 * unless the actual route changes.
 */
export const ZipperRouteLayer = (function () {
  const Inner = ({ route }: { route: PlannedStop[] }) => {
    const lineGeoJSON = useMemo(
      () => ({
        type: "Feature" as const,
        geometry: {
          type: "LineString" as const,
          coordinates: route.map((s) => [s.lon, s.lat]),
        },
        properties: {},
      }),
      [route],
    );

    // Late freight gets a distinct style so the driver can eyeball insertions.
    const pointGeoJSON = useMemo(
      () => ({
        type: "FeatureCollection" as const,
        features: route
          .filter((s) => s.original_sequence !== 0)
          .map((s) => ({
            type: "Feature" as const,
            geometry: { type: "Point" as const, coordinates: [s.lon, s.lat] },
            properties: { label: s.label, late: s.is_late_freight ? 1 : 0 },
          })),
      }),
      [route],
    );

    if (route.length < 2) return null;

    return (
      <>
        <GeoJSONSource id="zip-line" data={lineGeoJSON}>
          <Layer
            id="zip-line-layer"
            type="line"
            layout={{ 'line-cap': 'round', 'line-join': 'round' }}
            paint={{ 'line-color': '#38bdf8', 'line-width': 5 }}
          />
        </GeoJSONSource>

        <GeoJSONSource id="zip-stops" data={pointGeoJSON}>
          <Layer
            id="zip-stop-dot"
            type="circle"
            paint={{
              'circle-radius': 13,
              'circle-color': ['case', ['==', ['get', 'late'], 1], '#f59e0b', '#1e293b'],
              'circle-stroke-color': '#ffffff',
              'circle-stroke-width': 2,
            }}
          />
          <Layer
            id="zip-stop-label"
            type="symbol"
            layout={{ 'text-field': ['get', 'label'], 'text-size': 11, 'text-font': ['Noto Sans Bold'] }}
            paint={{ 'text-color': '#ffffff' }}
          />
        </GeoJSONSource>
      </>
    );
  };
  // Only re-render when the route object identity changes.
  return require("react").memo(Inner, (a: any, b: any) => a.route === b.route);
})();
