/**
 * Auth Token Bridge
 * 
 * Provides a unified way to get the current auth token regardless of
 * whether the user logged in via Supabase or legacy auth.
 * 
 * Priority:
 * 1. Supabase session access_token (if available)
 * 2. Legacy session_token from AsyncStorage
 */

import AsyncStorage from '@react-native-async-storage/async-storage';

// Cached reference to supabase client getter
let supabaseClientGetter: (() => Promise<any>) | null = null;

/**
 * Register the Supabase client getter from SupabaseContext.
 * Called once during app initialization.
 */
export function registerSupabaseClientGetter(getter: () => Promise<any>) {
  supabaseClientGetter = getter;
}

/**
 * Get the current auth token (Supabase JWT or legacy session token).
 * This is the main function used by stopsStore and other API callers.
 */
export async function getAuthToken(): Promise<string | null> {
  // Try Supabase first
  if (supabaseClientGetter) {
    try {
      const client = await supabaseClientGetter();
      if (client?.auth) {
        const { data: { session } } = await client.auth.getSession();
        console.log('[authTokenBridge] Supabase session check:', {
          hasSession: !!session,
          hasAccessToken: !!session?.access_token,
          tokenLength: session?.access_token?.length || 0,
          userEmail: session?.user?.email || 'none',
        });
        if (session?.access_token) {
          return session.access_token;
        }
      } else {
        console.log('[authTokenBridge] Supabase client has no auth property');
      }
    } catch (error) {
      console.warn('[authTokenBridge] Supabase token fetch failed, trying legacy:', error);
    }
  } else {
    console.log('[authTokenBridge] No supabaseClientGetter registered');
  }

  // Fallback to legacy session_token
  try {
    const legacyToken = await AsyncStorage.getItem('session_token');
    console.log('[authTokenBridge] Legacy token check:', {
      hasLegacyToken: !!legacyToken,
      tokenLength: legacyToken?.length || 0,
    });
    if (legacyToken) {
      return legacyToken;
    }
  } catch (error) {
    console.warn('[authTokenBridge] Legacy token fetch failed:', error);
  }

  console.log('[authTokenBridge] No auth token found');
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
