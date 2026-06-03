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
        if (session?.access_token) {
          return session.access_token;
        }
      }
    } catch (error) {
      console.debug('[authTokenBridge] Supabase token fetch failed, trying legacy:', error);
    }
  }

  // Fallback to legacy session_token
  try {
    const legacyToken = await AsyncStorage.getItem('session_token');
    if (legacyToken) {
      return legacyToken;
    }
  } catch (error) {
    console.debug('[authTokenBridge] Legacy token fetch failed:', error);
  }

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
