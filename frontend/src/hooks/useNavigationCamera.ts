/**
 * useNavigationCamera
 *
 * High-frequency GPS + compass hook dedicated solely to camera control.
 * Runs at 250ms independent of the main 800ms navigation GPS subscription
 * so the map bearing / zoom transitions stay buttery-smooth without
 * triggering expensive React re-renders for every GPS tick.
 *
 * Architecture:
 *   GPS (250ms) ──► bearing ref ──► drivingCamera msg ──► WebView easeTo
 *   compass     ──┘                (raw lng/lat, no pre-offset)
 *
 * The look-ahead offset is computed inside the WebView using map.project /
 * map.unproject so it adapts correctly to the current zoom and pitch.
 */

import { useEffect, useRef } from 'react';
import * as Location from 'expo-location';

// Great-circle distance in metres between two lng/lat points. Used to derive
// speed from consecutive GPS fixes when the OS doesn't report coords.speed.
function haversineMeters(
  lat1: number,
  lon1: number,
  lat2: number,
  lon2: number,
): number {
  const R = 6371000; // Earth radius (m)
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(a)));
}

interface NavigationCameraOptions {
  /** Activate / deactivate the hook without unmounting. */
  enabled: boolean;
  /** Wait until the WebView map is ready before subscribing. */
  mapReady: boolean;
  /**
   * Optional side-channel: notify parent of speed changes without
   * causing a re-render loop. Parent should store value in a ref.
   */
  onSpeedUpdate?: (speedKmh: number) => void;
}

/**
 * @param sendMessage  Stable callback (from useCallback / ref) that serialises
 *                     a message and calls webViewRef.injectJavaScript.
 */
export function useNavigationCamera(
  sendMessage: (msg: object) => void,
  options: NavigationCameraOptions,
): void {
  const headingRef  = useRef(0);
  const posSubRef   = useRef<Location.LocationSubscription | null>(null);
  const headSubRef  = useRef<Location.LocationSubscription | null>(null);
  const lastFireRef = useRef(0);
  // Have we ever seen a valid GPS course? Once we have, we stop trusting the
  // magnetometer entirely — in-vehicle metal & electrical interference make it
  // useless, and it was rotating the puck while the driver was stopped.
  const hasGpsCourseRef = useRef(false);

  // Previous GPS fix used to derive speed when the OS doesn't report it.
  // (Android fused / interpolated fixes routinely return coords.speed = 0,
  //  which would pin the speed-adaptive camera zoom at street level forever.)
  const prevFixRef = useRef<{ lat: number; lng: number; t: number } | null>(null);
  // EMA-smoothed speed (m/s). Raw 250 ms deltas carry ~±4 m/s of noise from
  // 1 m GPS jitter, so we low-pass them before they drive the camera zoom.
  const emaSpeedRef = useRef(0);

  // Speed threshold below which we FREEZE the bearing. Google Maps uses ~2 km/h
  // (~0.56 m/s); we use 1.4 m/s (~5 km/h) so brief coasting doesn't jitter.
  const MOVING_SPEED_MPS = 1.4;
  // Target time constant for the speed EMA (seconds). A fixed alpha like 0.2
  // assumes a constant 250 ms tick rate — but with distanceInterval:1 the hook
  // fires at 1-tick-per-metre, so at 4 km/h it fires every ~900 ms and the
  // effective time constant balloons to ~4 s (sluggish/shows 0 when moving).
  // Using alpha = 1 - exp(-dt/τ) gives consistent 1-second convergence at any
  // driving speed.
  const SPEED_EMA_TAU_S = 1.0;
  // Cap on derived speed (~162 km/h) to reject GPS teleports between fixes.
  const MAX_DERIVED_SPEED_MPS = 45;

  // Keep a stable reference so the async callbacks don't close over stale fns
  const sendRef  = useRef(sendMessage);
  const optsRef  = useRef(options);
  useEffect(() => { sendRef.current = sendMessage; }, [sendMessage]);
  useEffect(() => { optsRef.current = options; },    [options]);

  useEffect(() => {
    if (__DEV__) console.log('[NAV_CAM] Hook effect running. enabled:', options.enabled, 'mapReady:', options.mapReady);
    
    if (!options.enabled || !options.mapReady) {
      // Tear down any active subscriptions when disabled
      posSubRef.current?.remove();
      headSubRef.current?.remove();
      posSubRef.current  = null;
      headSubRef.current = null;
      hasGpsCourseRef.current = false;
      prevFixRef.current = null;
      emaSpeedRef.current = 0;
      if (__DEV__) console.log('[NAV_CAM] Hook disabled or map not ready - subscriptions cleared');
      return;
    }

    let alive = true;
    if (__DEV__) console.log('[NAV_CAM] Starting GPS subscriptions...');

    (async () => {
      // ── 1. Compass heading (fallback ONLY — used until the first valid GPS
      //       course arrives, so the puck points roughly correctly before the
      //       vehicle starts moving). Once GPS gives us a real course, we stop
      //       accepting magnetometer updates.
      headSubRef.current = await Location.watchHeadingAsync((h) => {
        if (!alive) return;
        if (hasGpsCourseRef.current) return;  // GPS is authoritative once moving
        headingRef.current = h.trueHeading ?? h.magHeading ?? 0;
      });
      if (__DEV__) console.log('[NAV_CAM] Compass subscription started');

      // ── 2. High-frequency position for camera smoothness ───────────────
      posSubRef.current = await Location.watchPositionAsync(
        {
          accuracy:         Location.Accuracy.BestForNavigation,
          timeInterval:     250,  // 4 Hz GPS — high enough for smooth camera, low enough to avoid CPU/battery thrash
          distanceInterval: 1,    // only fire on ≥1 m moves (drops duplicate fixes while parked)
        },
        (location) => {
          if (!alive) return;

          // Throttle to 4 fps (250 ms). Paired with the 160 ms easeTo
          // duration below so each rotation animation finishes well before
          // the next tick arrives — gives the camera a settled moment per
          // tick instead of being perpetually mid-interpolation (which is
          // what caused the chronic "laggy turns" feel at 100 ms / 90 ms).
          const now = Date.now();
          if (now - lastFireRef.current < 250) return;
          lastFireRef.current = now;

          const { latitude, longitude, speed, heading: gpsHeading } = location.coords;

          // ── Speed: trust the OS value when present, but Android fused /
          //    interpolated fixes routinely report 0 (or null), which would
          //    pin the speed-adaptive camera zoom at street level forever.
          //    Derive a fallback from the distance/time delta between
          //    consecutive fixes, take whichever is larger, then EMA-smooth
          //    out GPS jitter. (This also re-engages GPS-course bearing once
          //    moving, instead of leaning on the unreliable magnetometer.) ──
          const osSpeed = Math.max(0, speed ?? 0);
          let derived = 0;
          const prevFix = prevFixRef.current;
          // dt: seconds since the last *processed* fix (respects the throttle above,
          // so it's the true elapsed wall time between speed samples).
          const dtS = prevFix ? Math.max(0, (now - prevFix.t) / 1000) : 0.25;
          if (prevFix && dtS > 0) {
            const raw = haversineMeters(prevFix.lat, prevFix.lng, latitude, longitude) / dtS;
            if (raw <= MAX_DERIVED_SPEED_MPS) derived = raw;
          }
          prevFixRef.current = { lat: latitude, lng: longitude, t: now };
          const instantSpeed = Math.max(osSpeed, derived);
          // Time-based EMA: alpha scales with the actual elapsed interval so the
          // time constant stays ~SPEED_EMA_TAU_S seconds regardless of how often
          // the hook fires (which varies with distanceInterval + driving speed).
          const alpha = 1 - Math.exp(-dtS / SPEED_EMA_TAU_S);
          emaSpeedRef.current = alpha * instantSpeed + (1 - alpha) * emaSpeedRef.current;
          const speedMps = Math.max(0, emaSpeedRef.current);

          optsRef.current.onSpeedUpdate?.(Math.round(speedMps * 3.6));

          // ── Bearing selection (Google-Maps-style) ──
          // While moving: trust GPS course-over-ground (robust, never drifts).
          // While stopped: FREEZE bearing at its last moving value — do NOT let
          // the magnetometer keep rotating the puck / camera.
          if (speedMps >= MOVING_SPEED_MPS && typeof gpsHeading === 'number' && gpsHeading >= 0) {
            headingRef.current = gpsHeading;
            hasGpsCourseRef.current = true;
          }
          // else: headingRef stays at its last good value (frozen)

          // ── Single-source-of-truth: drive BOTH the puck marker AND the
          //     camera from the same GPS tick. Previously the puck was being
          //     updated from a separate, slower (400 ms) subscription via the
          //     `driverLocation` prop, so the puck and the camera centre saw
          //     two slightly different GPS fixes at two slightly different
          //     times → puck visibly drifted/jumped inside the screen frame
          //     between each tick. By emitting `updateDriver` + `drivingCamera`
          //     back-to-back here, both are guaranteed to reference the exact
          //     same fix → puck stays glued to its screen anchor.
          sendRef.current({
            type: 'updateDriver',
            location: { latitude, longitude, heading: headingRef.current },
          });
          // Send raw GPS — the WebView computes pixel-space look-ahead offset
          if (__DEV__) console.log('[NAV_CAM] Sending drivingCamera:', { lng: longitude.toFixed(5), lat: latitude.toFixed(5), bearing: headingRef.current.toFixed(1) });
          sendRef.current({
            type:     'drivingCamera',
            lng:      longitude,
            lat:      latitude,
            bearing:  headingRef.current,
            speedMps,
          });
        },
      );
    })();

    return () => {
      alive = false;
      posSubRef.current?.remove();
      headSubRef.current?.remove();
      posSubRef.current  = null;
      headSubRef.current = null;
      hasGpsCourseRef.current = false;
      prevFixRef.current = null;
      emaSpeedRef.current = 0;
    };
  // Only re-run when enabled/mapReady flip — not on every render
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [options.enabled, options.mapReady]);
}
