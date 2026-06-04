// Google OAuth hook using native @react-native-google-signin/google-signin with Supabase
import { useEffect, useCallback, useState } from 'react';
import { Platform } from 'react-native';
import {
  GoogleSignin,
  statusCodes,
  isSuccessResponse,
  isErrorWithCode,
} from '@react-native-google-signin/google-signin';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { getSupabase } from '../lib/supabase';

// Google OAuth Client IDs from environment
const GOOGLE_WEB_CLIENT_ID = process.env.EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID || '';
const GOOGLE_IOS_CLIENT_ID = process.env.EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID || '';

// Configure Google Sign-In on module load
GoogleSignin.configure({
  webClientId: GOOGLE_WEB_CLIENT_ID, // Required for getting ID token
  iosClientId: GOOGLE_IOS_CLIENT_ID, // iOS client ID
  offlineAccess: false,
  scopes: ['profile', 'email'],
});

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

interface GoogleAuthState {
  loading: boolean;
  error: string | null;
}

export function useGoogleAuth() {
  const [state, setState] = useState<GoogleAuthState>({
    loading: false,
    error: null,
  });
  const [isConfigured, setIsConfigured] = useState(false);

  useEffect(() => {
    // Check if Google Sign-In is available
    const checkConfiguration = async () => {
      try {
        const hasPlayServices = await GoogleSignin.hasPlayServices({ showPlayServicesUpdateDialog: true });
        console.log('[GoogleAuth] Play Services available:', hasPlayServices);
        setIsConfigured(hasPlayServices);
      } catch (error) {
        console.log('[GoogleAuth] Play Services check failed:', error);
        setIsConfigured(false);
      }
    };
    
    if (Platform.OS === 'android') {
      checkConfiguration();
    } else {
      setIsConfigured(true);
    }
  }, []);

  const signInWithGoogle = useCallback(async () => {
    setState({ loading: true, error: null });
    
    try {
      console.log('[GoogleAuth] Starting native Google sign-in flow...');
      console.log('[GoogleAuth] Platform:', Platform.OS);
      console.log('[GoogleAuth] Web Client ID:', GOOGLE_WEB_CLIENT_ID ? 'Set' : 'Missing');
      
      // CRITICAL: Clear any legacy session_token BEFORE signing in
      // This prevents stale ES256 tokens from being used
      try {
        await AsyncStorage.removeItem('session_token');
        console.log('[GoogleAuth] Cleared legacy session_token');
      } catch (e) {
        console.warn('[GoogleAuth] Failed to clear legacy token:', e);
      }
      
      // Check Play Services
      await GoogleSignin.hasPlayServices({ showPlayServicesUpdateDialog: true });
      
      // Sign in with Google
      const response = await GoogleSignin.signIn();
      
      console.log('[GoogleAuth] Sign-in response received');
      
      if (isSuccessResponse(response)) {
        const { data } = response;
        const idToken = data.idToken;
        
        if (!idToken) {
          throw new Error('No ID token received from Google');
        }
        
        const idTokenAlg = getJwtAlgorithm(idToken);
        console.log('[GoogleAuth] Google ID token received:');
        console.log('[GoogleAuth]   - Algorithm:', idTokenAlg); // Should be ES256
        console.log('[GoogleAuth]   - Preview:', idToken.substring(0, 50) + '...');
        
        if (idTokenAlg !== 'ES256') {
          console.warn('[GoogleAuth] WARNING: Expected ES256 for Google ID token, got:', idTokenAlg);
        }
        
        // Get Supabase client and sign in with the ID token
        // This exchanges the ES256 Google ID token for an HS256 Supabase JWT
        console.log('[GoogleAuth] Exchanging Google ID token for Supabase session...');
        const supabase = getSupabase();
        const { data: authData, error: supabaseError } = await supabase.auth.signInWithIdToken({
          provider: 'google',
          token: idToken,
        });
        
        if (supabaseError) {
          console.error('[GoogleAuth] Supabase auth error:', supabaseError);
          throw supabaseError;
        }
        
        // CRITICAL: Verify we got a valid Supabase session with HS256 token
        if (!authData.session?.access_token) {
          console.error('[GoogleAuth] CRITICAL: No access_token in Supabase session!');
          throw new Error('Supabase did not return a valid session');
        }
        
        const supabaseTokenAlg = getJwtAlgorithm(authData.session.access_token);
        
        console.log('[GoogleAuth] Supabase authentication successful:');
        console.log('[GoogleAuth]   - User:', authData.user?.email);
        console.log('[GoogleAuth]   - User ID:', authData.user?.id);
        console.log('[GoogleAuth]   - Session exists:', true);
        console.log('[GoogleAuth]   - Access token algorithm:', supabaseTokenAlg); // Should be HS256
        console.log('[GoogleAuth]   - Access token preview:', authData.session.access_token.substring(0, 50) + '...');
        
        // CRITICAL VALIDATION: Ensure we got an HS256 token, not ES256
        if (supabaseTokenAlg !== 'HS256') {
          console.error('[GoogleAuth] CRITICAL ERROR: Expected HS256 Supabase token, got:', supabaseTokenAlg);
          if (supabaseTokenAlg === 'ES256') {
            throw new Error('Token exchange failed: Supabase returned Google ID token instead of Supabase JWT');
          }
        }
        
        console.log('[GoogleAuth] ✓ Token exchange successful - HS256 Supabase JWT received');
        
        // Double-check: Verify the session is actually stored
        const { data: verifySession } = await supabase.auth.getSession();
        if (verifySession.session?.access_token) {
          const verifyAlg = getJwtAlgorithm(verifySession.session.access_token);
          console.log('[GoogleAuth] Session verification - stored token alg:', verifyAlg);
          if (verifyAlg !== 'HS256') {
            console.error('[GoogleAuth] CRITICAL: Stored session has wrong algorithm!');
          }
        } else {
          console.warn('[GoogleAuth] WARNING: Could not verify stored session');
        }
        
        setState({ loading: false, error: null });
        return { success: true, user: authData.user };
      } else {
        throw new Error('Google sign-in was not successful');
      }
    } catch (error: any) {
      console.error('[GoogleAuth] Error:', error);
      
      let errorMessage = 'Google sign-in failed';
      
      if (isErrorWithCode(error)) {
        switch (error.code) {
          case statusCodes.SIGN_IN_CANCELLED:
            errorMessage = 'Sign-in was cancelled';
            break;
          case statusCodes.IN_PROGRESS:
            errorMessage = 'Sign-in is already in progress';
            break;
          case statusCodes.PLAY_SERVICES_NOT_AVAILABLE:
            errorMessage = 'Google Play Services not available';
            break;
          default:
            errorMessage = error.message || 'Unknown error occurred';
        }
      } else if (error.message) {
        errorMessage = error.message;
      }
      
      setState({ loading: false, error: errorMessage });
      return { success: false, error: errorMessage };
    }
  }, []);

  const signOut = useCallback(async () => {
    try {
      // Sign out from Google
      await GoogleSignin.signOut();
      
      // Also sign out from Supabase
      const supabase = getSupabase();
      await supabase.auth.signOut();
      
      console.log('[GoogleAuth] Signed out successfully');
    } catch (error) {
      console.error('[GoogleAuth] Sign out error:', error);
    }
  }, []);

  return {
    signInWithGoogle,
    signOut,
    loading: state.loading,
    error: state.error,
    isReady: isConfigured,
  };
}
