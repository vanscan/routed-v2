/**
 * featureFlags.ts — lightweight runtime feature-flag registry.
 *
 * Phase 0 of the native-map migration (`@maplibre/maplibre-react-native`)
 * is gated behind `useNativeMap`. The flag resolves in this priority order:
 *
 *   1. A runtime override (set via the in-app dev toggle / `setUseNativeMap`)
 *      and persisted to AsyncStorage so it survives reloads.
 *   2. The build-time env default `EXPO_PUBLIC_USE_NATIVE_MAP=true`.
 *   3. `false` (the existing WebView MapLibre GL JS map stays the default
 *      until full parity is reached — see PRD).
 *
 * The sync getter (`getUseNativeMapSync`) reads a module-level cache so the
 * map component can branch ONCE at mount without an async race. Call
 * `hydrateFeatureFlags()` early in app startup to load any persisted override.
 */
import AsyncStorage from '@react-native-async-storage/async-storage';

const STORAGE_KEY = 'feature.useNativeMap';

const ENV_DEFAULT =
  String(process.env.EXPO_PUBLIC_USE_NATIVE_MAP || '').toLowerCase() === 'true';

// null = no runtime override yet → fall back to the env default.
let _useNativeMapOverride: boolean | null = null;
let _hydrated = false;

/** Synchronous resolved value of the `useNativeMap` flag. */
export function getUseNativeMapSync(): boolean {
  return _useNativeMapOverride != null ? _useNativeMapOverride : ENV_DEFAULT;
}

/** Whether the persisted override has been loaded from AsyncStorage. */
export function isFeatureFlagsHydrated(): boolean {
  return _hydrated;
}

/** Load any persisted override. Safe to call multiple times. */
export async function hydrateFeatureFlags(): Promise<void> {
  try {
    const v = await AsyncStorage.getItem(STORAGE_KEY);
    if (v === 'true') _useNativeMapOverride = true;
    else if (v === 'false') _useNativeMapOverride = false;
  } catch {
    // Non-fatal — fall back to the env default.
  } finally {
    _hydrated = true;
  }
}

/** Flip the native-map flag at runtime and persist it. */
export async function setUseNativeMap(enabled: boolean): Promise<void> {
  _useNativeMapOverride = enabled;
  try {
    await AsyncStorage.setItem(STORAGE_KEY, enabled ? 'true' : 'false');
  } catch {
    // Non-fatal — the in-memory override still takes effect this session.
  }
}
