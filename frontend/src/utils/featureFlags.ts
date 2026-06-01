/**
 * featureFlags.ts — lightweight runtime feature-flag registry.
 *
 * Phase 4 (cutover complete): The native-map migration is now the default.
 * The flag `useNativeMap` resolves in this priority order:
 *
 *   1. A runtime override (set via the in-app dev toggle / `setUseNativeMap`)
 *      and persisted to AsyncStorage so it survives reloads.
 *   2. The build-time env default `EXPO_PUBLIC_USE_NATIVE_MAP` (if set).
 *   3. `true` — the native `@maplibre/maplibre-react-native` map is now the
 *      default. The legacy WebView-based map has been removed as of Phase 4.
 *
 * The sync getter (`getUseNativeMapSync`) reads a module-level cache so the
 * map component can branch ONCE at mount without an async race. Call
 * `hydrateFeatureFlags()` early in app startup to load any persisted override.
 */
import AsyncStorage from '@react-native-async-storage/async-storage';

const STORAGE_KEY = 'feature.useNativeMap';

// Phase 4: native map is now the default. The env var can override to 'false'
// if someone explicitly wants the legacy WebView (unlikely since it's removed).
const ENV_DEFAULT =
  String(process.env.EXPO_PUBLIC_USE_NATIVE_MAP || 'true').toLowerCase() !== 'false';

// null = no runtime override yet → fall back to the env default (now `true`).
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
