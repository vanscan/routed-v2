---
name: Android nav speed must be derived, not read from coords.speed
description: expo-location coords.speed is 0/null on Android fused fixes; derive speed from GPS deltas for any speed-driven nav feature
---

On Android, `expo-location`'s `location.coords.speed` routinely returns `0`
(or `null`) on fused / interpolated GPS fixes — even while driving at speed.
iOS reports it fine, so this is silent and device-dependent.

**Why it matters:** any feature keyed off `coords.speed` will appear broken on
Android only:
- speed-adaptive camera zoom stays pinned at street-level (never zooms out)
- the speed HUD reads 0 km/h while moving
- "paused" detection thinks the van is parked the whole drive
- the GPS-course bearing gate (`speed >= MOVING_SPEED_MPS`) never opens, so the
  puck heading falls back to the unreliable in-vehicle magnetometer forever

**How to apply:** never trust `coords.speed` as the sole speed source on Android.
Derive speed from consecutive fixes: `haversine(prev, cur) / dt`, cap it to
reject GPS teleports (~45 m/s), take `max(osSpeed, derived)`, and EMA-smooth
(alpha ~0.2 at ~4 Hz) before it drives anything. Reset the prev-fix and EMA
state on navigation teardown so a new session starts at rest.

The nav-camera hook derives this once and publishes smoothed km/h to a shared
ref; downstream consumers (HUD, pause detection) should coalesce with that ref
rather than each re-deriving. Truly-stationary handling stays correct because
the main position watcher fires on a time interval (distanceInterval: 0) and
uses displacement, not `coords.speed`, as the stationary arbiter.
