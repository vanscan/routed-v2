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
 */

import AsyncStorage from '@react-native-async-storage/async-storage';

// Cached reference to supabase client getter
let supabaseClientGetter: (() => Promise<any>) | null = null;

/**
 * Register the Supabase client getter from SupabaseContext.
 * Called once during app initialization.
 */
export function registerSupabaseClientGetter(getter: () => Promise<any>) {
  console.log('[authTokenBridge] Supabase client getter registered');
  supabaseClientGetter = getter;
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
 * Get the current auth token (Supabase JWT or legacy session token).
 * This is the main function used by stopsStore and other API callers.
 * 
 * CRITICAL: Only returns tokens that are HS256 signed (Supabase JWTs).
 * ES256 signed tokens (Google ID tokens) will be rejected.
 */
export async function getAuthToken(): Promise<string | null> {
  // Try Supabase first - this is the preferred path
  if (supabaseClientGetter) {
    try {
      const client = await supabaseClientGetter();
      if (client?.auth) {
        const { data: { session }, error } = await client.auth.getSession();
        
        const tokenAlg = session?.access_token ? getJwtAlgorithm(session.access_token) : null;
        
        console.log('[authTokenBridge] Supabase session check:', {
          hasSession: !!session,
          hasAccessToken: !!session?.access_token,
          tokenAlgorithm: tokenAlg || 'none',
          tokenLength: session?.access_token?.length || 0,
          tokenPreview: session?.access_token ? session.access_token.substring(0, 30) + '...' : 'none',
          userEmail: session?.user?.email || 'none',
          error: error?.message || 'none',
        });
        
        if (session?.access_token) {
          // Supabase access tokens start with 'eyJ' (base64 encoded JSON)
          if (!session.access_token.startsWith('eyJ')) {
            console.warn('[authTokenBridge] WARNING: Supabase access_token does not look like a JWT');
          }
          
          // CRITICAL: Verify this is a Supabase JWT (HS256), NOT a Google ID token (ES256)
          if (tokenAlg === 'ES256') {
            console.error('[authTokenBridge] CRITICAL ERROR: Got ES256 token (Google ID token) instead of HS256 (Supabase token)!');
            console.error('[authTokenBridge] This indicates Supabase signInWithIdToken did not properly exchange the token.');
            // Don't return this token - it will be rejected by the backend
            return null;
          }
          
          if (tokenAlg !== 'HS256') {
            console.warn('[authTokenBridge] WARNING: Unexpected token algorithm:', tokenAlg);
          }
          
          return session.access_token;
        }
      } else {
        console.log('[authTokenBridge] Supabase client has no auth property');
      }
    } catch (error) {
      console.warn('[authTokenBridge] Supabase token fetch failed, trying legacy:', error);
    }
  } else {
    console.log('[authTokenBridge] No supabaseClientGetter registered yet - will try direct import');
    
    // Fallback: Try direct import of Supabase client
    try {
      const { getSupabase } = await import('../lib/supabase');
      const client = getSupabase();
      const { data: { session }, error } = await client.auth.getSession();
      
      const tokenAlg = session?.access_token ? getJwtAlgorithm(session.access_token) : null;
      
      console.log('[authTokenBridge] Direct Supabase import session check:', {
        hasSession: !!session,
        tokenAlgorithm: tokenAlg || 'none',
        userEmail: session?.user?.email || 'none',
        error: error?.message || 'none',
      });
      
      if (session?.access_token && tokenAlg === 'HS256') {
        return session.access_token;
      }
    } catch (importError) {
      console.warn('[authTokenBridge] Direct Supabase import failed:', importError);
    }
  }

  // Fallback to legacy session_token - ONLY if it's a valid HS256 JWT
  try {
    const legacyToken = await AsyncStorage.getItem('session_token');
    
    if (legacyToken) {
      const legacyAlg = getJwtAlgorithm(legacyToken);
      
      console.log('[authTokenBridge] Legacy token check:', {
        hasLegacyToken: true,
        tokenAlgorithm: legacyAlg || 'unknown',
        tokenLength: legacyToken.length,
      });
      
      // Only return if it's a valid JWT with correct algorithm
      if (legacyToken.startsWith('eyJ')) {
        // CRITICAL: Reject ES256 tokens (Google ID tokens)
        if (legacyAlg === 'ES256') {
          console.error('[authTokenBridge] CRITICAL: Legacy token is a Google ID token (ES256), clearing it!');
          await AsyncStorage.removeItem('session_token').catch(() => {});
          return null;
        }
        
        return legacyToken;
      } else {
        console.warn('[authTokenBridge] Legacy token is not a JWT, clearing it');
        await AsyncStorage.removeItem('session_token').catch(() => {});
      }
    } else {
      console.log('[authTokenBridge] No legacy token found');
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
