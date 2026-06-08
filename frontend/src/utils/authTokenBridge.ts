/**
 * Auth Token Bridge
 * 
 * Provides a unified way to get the current auth token regardless of
 * whether the user logged in via Supabase or legacy auth.
 * 
 * Priority:
 * 1. Supabase session access_token (if available) - accepts both HS256 and ES256
 * 2. Legacy session_token from AsyncStorage (only as fallback)
 * 
 * NOTE (2026 Update): Supabase now uses ES256 by default for JWTs.
 * Both HS256 and ES256 are valid Supabase token algorithms.
 */

import AsyncStorage from '@react-native-async-storage/async-storage';

// Verbose debug logs are stripped in production builds
const debugLog = __DEV__ ? console.log.bind(console) : () => {};

// Cached reference to supabase client getter
let supabaseClientGetter: (() => Promise<any>) | null = null;

// Cache the supabase client directly for synchronous access
let cachedSupabaseClient: any = null;

/**
 * Register the Supabase client getter from SupabaseContext.
 * Called once during app initialization.
 * Also pre-fetches the client for faster synchronous access.
 */
export function registerSupabaseClientGetter(getter: () => Promise<any>) {
  debugLog('[authTokenBridge] Supabase client getter registered');
  supabaseClientGetter = getter;
  
  // Pre-fetch the client immediately
  getter().then(client => {
    cachedSupabaseClient = client;
    debugLog('[authTokenBridge] Supabase client cached for fast access');
  }).catch(err => {
    console.warn('[authTokenBridge] Failed to pre-cache Supabase client:', err);
  });
}

/**
 * Helper to decode JWT header and extract algorithm
 */
function getJwtAlgorithm(token: string): string | null {
  try {
    const parts = token.split('.');
    if (parts.length !== 3) return null;
    const header = JSON.parse(atob(parts[0]));
    return header.alg || null;
  } catch {
    return null;
  }
}

/**
 * VALIDATION: Check if a token is a valid JWT (Supabase token).
 * Accepts both HS256 and ES256 algorithms (Supabase uses ES256 by default since 2026).
 */
function isValidSupabaseToken(token: string | null | undefined): boolean {
  if (!token || typeof token !== 'string') return false;
  if (!token.startsWith('eyJ')) return false;
  
  const alg = getJwtAlgorithm(token);
  
  // Accept both HS256 and ES256 - both are valid Supabase JWT algorithms
  if (alg === 'HS256' || alg === 'ES256') {
    return true;
  }
  
  if (alg) {
    console.warn('[authTokenBridge] Unexpected algorithm:', alg);
    // Still allow tokens with other algorithms in case Supabase changes again
    return true;
  }
  
  return false;
}

/**
 * Get the current auth token (Supabase JWT or legacy session token).
 * This is the main function used by stopsStore and other API callers.
 * 
 * Accepts both HS256 and ES256 Supabase JWTs.
 */
export async function getAuthToken(): Promise<string | null> {
  // Method 1: Try cached Supabase client (fastest path)
  if (cachedSupabaseClient?.auth) {
    try {
      const { data: { session }, error } = await cachedSupabaseClient.auth.getSession();
      
      if (session?.access_token && isValidSupabaseToken(session.access_token)) {
        debugLog('[authTokenBridge] ✓ Got valid token from cached Supabase client');
        return session.access_token;
      }
      
      if (session?.access_token) {
        // Token exists but is invalid format
        console.error('[authTokenBridge] Invalid token format, algorithm:', 
          getJwtAlgorithm(session.access_token));
      }
    } catch (error) {
      console.warn('[authTokenBridge] Cached client getSession failed:', error);
    }
  }

  // Method 2: Try supabaseClientGetter (registered by SupabaseContext)
  if (supabaseClientGetter) {
    try {
      const client = await supabaseClientGetter();
      if (client?.auth) {
        const { data: { session }, error } = await client.auth.getSession();
        
        if (session?.access_token && isValidSupabaseToken(session.access_token)) {
          // Cache the client for future calls
          cachedSupabaseClient = client;
          debugLog('[authTokenBridge] ✓ Got valid token from getter, caching client');
          return session.access_token;
        }
        
        if (session?.access_token) {
          console.error('[authTokenBridge] Invalid token from getter, algorithm:', 
            getJwtAlgorithm(session.access_token));
        }
      }
    } catch (error) {
      console.warn('[authTokenBridge] Getter getSession failed:', error);
    }
  }

  // Method 3: Direct Supabase import (fallback if context not ready)
  try {
    const { getSupabase } = await import('../lib/supabase');
    const client = getSupabase();
    const { data: { session }, error } = await client.auth.getSession();
    
    if (session?.access_token && isValidSupabaseToken(session.access_token)) {
      // Cache the client for future calls
      cachedSupabaseClient = client;
      debugLog('[authTokenBridge] ✓ Got valid token from direct import');
      return session.access_token;
    }
    
    if (session?.access_token) {
      console.error('[authTokenBridge] Invalid token from direct import, algorithm:', 
        getJwtAlgorithm(session.access_token));
    }
  } catch (importError) {
    console.warn('[authTokenBridge] Direct Supabase import failed:', importError);
  }

  // Method 4: Legacy session_token from AsyncStorage (LAST RESORT)
  try {
    const legacyToken = await AsyncStorage.getItem('session_token');
    
    if (legacyToken) {
      if (isValidSupabaseToken(legacyToken)) {
        debugLog('[authTokenBridge] ✓ Using valid legacy session_token');
        return legacyToken;
      }
      
      // Invalid token - clear it
      console.warn('[authTokenBridge] Clearing invalid legacy token, algorithm:', 
        getJwtAlgorithm(legacyToken));
      await AsyncStorage.removeItem('session_token').catch(() => {});
    }
  } catch (error) {
    console.warn('[authTokenBridge] Legacy token fetch failed:', error);
  }

  debugLog('[authTokenBridge] No valid auth token found');
  return null;
}

/**
 * Check if user is authenticated via either method.
 */
export async function isAuthenticated(): Promise<boolean> {
  const token = await getAuthToken();
  return token !== null;
}

/**
 * Clear all auth tokens (both Supabase and legacy).
 * Used during logout.
 */
export async function clearAuthTokens(): Promise<void> {
  // Clear legacy token
  await AsyncStorage.removeItem('session_token').catch(() => {});
  
  // Supabase signOut is handled by SupabaseContext
}

/**
 * Clear any truly corrupted tokens from all storage locations.
 * Note: ES256 tokens are now valid since Supabase uses ES256 by default.
 * Only clears tokens that don't match known valid formats.
 */
export async function clearCorruptedTokens(): Promise<void> {
  debugLog('[authTokenBridge] Checking for corrupted tokens...');
  
  // Check and clear legacy session_token if it's not a valid JWT
  try {
    const legacyToken = await AsyncStorage.getItem('session_token');
    if (legacyToken && !isValidSupabaseToken(legacyToken)) {
      console.warn('[authTokenBridge] Clearing corrupted legacy token');
      await AsyncStorage.removeItem('session_token');
    }
  } catch (e) {
    console.warn('[authTokenBridge] Error checking legacy token:', e);
  }
  
  debugLog('[authTokenBridge] Corruption check complete');
}
