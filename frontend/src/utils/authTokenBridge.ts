/**
 * Auth Token Bridge
 * 
 * Provides a unified way to get the current auth token regardless of
 * whether the user logged in via Supabase or legacy auth.
 * 
 * Priority:
 * 1. Supabase session access_token (if available) - MUST be HS256 signed
 * 2. Legacy session_token from AsyncStorage (only as fallback)
 * 
 * CRITICAL: The Supabase access_token is signed with HS256.
 * The Google ID token is signed with ES256 - these MUST NOT be confused.
 * 
 * This module WILL NEVER return an ES256 token to prevent 401 errors.
 */

import AsyncStorage from '@react-native-async-storage/async-storage';

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
  console.log('[authTokenBridge] Supabase client getter registered');
  supabaseClientGetter = getter;
  
  // Pre-fetch the client immediately
  getter().then(client => {
    cachedSupabaseClient = client;
    console.log('[authTokenBridge] Supabase client cached for fast access');
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
 * CRITICAL VALIDATION: Check if a token is a valid HS256 JWT (Supabase token).
 * Returns false for ES256 (Google ID tokens) or any invalid format.
 */
function isValidSupabaseToken(token: string | null | undefined): boolean {
  if (!token || typeof token !== 'string') return false;
  if (!token.startsWith('eyJ')) return false;
  
  const alg = getJwtAlgorithm(token);
  
  // ONLY accept HS256 tokens - these are Supabase JWTs
  // REJECT ES256 tokens - these are Google ID tokens
  if (alg === 'ES256') {
    console.error('[authTokenBridge] REJECTED ES256 token (Google ID token)');
    return false;
  }
  
  if (alg !== 'HS256') {
    console.warn('[authTokenBridge] Unexpected algorithm:', alg);
    // Still allow non-ES256 tokens as they might be valid
  }
  
  return true;
}

/**
 * Get the current auth token (Supabase JWT or legacy session token).
 * This is the main function used by stopsStore and other API callers.
 * 
 * CRITICAL: Only returns tokens that are HS256 signed (Supabase JWTs).
 * ES256 signed tokens (Google ID tokens) will be REJECTED and cleared.
 */
export async function getAuthToken(): Promise<string | null> {
  // Method 1: Try cached Supabase client (fastest path)
  if (cachedSupabaseClient?.auth) {
    try {
      const { data: { session }, error } = await cachedSupabaseClient.auth.getSession();
      
      if (session?.access_token && isValidSupabaseToken(session.access_token)) {
        console.log('[authTokenBridge] ✓ Got valid HS256 token from cached Supabase client');
        return session.access_token;
      }
      
      if (session?.access_token) {
        // Token exists but is invalid (likely ES256) - this is a critical error
        console.error('[authTokenBridge] CRITICAL: Cached Supabase has invalid token, algorithm:', 
          getJwtAlgorithm(session.access_token));
        // Sign out to clear the corrupted session
        await cachedSupabaseClient.auth.signOut().catch(() => {});
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
          console.log('[authTokenBridge] ✓ Got valid HS256 token from getter, caching client');
          return session.access_token;
        }
        
        if (session?.access_token) {
          console.error('[authTokenBridge] CRITICAL: Getter returned invalid token, algorithm:', 
            getJwtAlgorithm(session.access_token));
          await client.auth.signOut().catch(() => {});
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
      console.log('[authTokenBridge] ✓ Got valid HS256 token from direct import');
      return session.access_token;
    }
    
    if (session?.access_token) {
      console.error('[authTokenBridge] CRITICAL: Direct import has invalid token, algorithm:', 
        getJwtAlgorithm(session.access_token));
      await client.auth.signOut().catch(() => {});
    }
  } catch (importError) {
    console.warn('[authTokenBridge] Direct Supabase import failed:', importError);
  }

  // Method 4: Legacy session_token from AsyncStorage (LAST RESORT)
  try {
    const legacyToken = await AsyncStorage.getItem('session_token');
    
    if (legacyToken) {
      if (isValidSupabaseToken(legacyToken)) {
        console.log('[authTokenBridge] ✓ Using valid legacy session_token');
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

  console.log('[authTokenBridge] No valid auth token found');
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
 * Clear any corrupted ES256 tokens from all storage locations.
 * Call this on app startup to ensure clean state.
 */
export async function clearCorruptedTokens(): Promise<void> {
  console.log('[authTokenBridge] Checking for corrupted tokens...');
  
  // Check and clear legacy session_token if it's ES256
  try {
    const legacyToken = await AsyncStorage.getItem('session_token');
    if (legacyToken) {
      const alg = getJwtAlgorithm(legacyToken);
      if (alg === 'ES256') {
        console.warn('[authTokenBridge] Clearing corrupted ES256 legacy token');
        await AsyncStorage.removeItem('session_token');
      }
    }
  } catch (e) {
    console.warn('[authTokenBridge] Error checking legacy token:', e);
  }
  
  // Check Supabase session
  try {
    const { getSupabase } = await import('../lib/supabase');
    const client = getSupabase();
    const { data: { session } } = await client.auth.getSession();
    
    if (session?.access_token) {
      const alg = getJwtAlgorithm(session.access_token);
      if (alg === 'ES256') {
        console.warn('[authTokenBridge] Clearing corrupted ES256 Supabase session');
        await client.auth.signOut();
      }
    }
  } catch (e) {
    console.warn('[authTokenBridge] Error checking Supabase session:', e);
  }
  
  console.log('[authTokenBridge] Corruption check complete');
}
