// Google OAuth hook using expo-auth-session with Supabase signInWithIdToken
import { useEffect, useCallback, useState } from 'react';
import { Platform } from 'react-native';
import * as WebBrowser from 'expo-web-browser';
import * as Google from 'expo-auth-session/providers/google';
import * as AuthSession from 'expo-auth-session';
import { getSupabase } from '../lib/supabase';

// Required for web browser auth session completion
WebBrowser.maybeCompleteAuthSession();

// Google OAuth Client IDs from environment
const GOOGLE_WEB_CLIENT_ID = process.env.EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID || '';
const GOOGLE_IOS_CLIENT_ID = process.env.EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID || '';
const GOOGLE_ANDROID_CLIENT_ID = process.env.EXPO_PUBLIC_GOOGLE_ANDROID_CLIENT_ID || '';

// Generate the correct redirect URI for each platform
// For standalone Android builds, use the Expo auth proxy which handles redirects reliably
const redirectUri = AuthSession.makeRedirectUri({
  scheme: 'routr',
  path: 'auth',
  // Use proxy for production builds - more reliable redirect handling
  preferLocalhost: false,
});

export interface GoogleAuthResult {
  success: boolean;
  error?: string;
}

export interface UseGoogleAuthReturn {
  signInWithGoogle: () => Promise<void>;
  isLoading: boolean;
  error: string | null;
  isReady: boolean;
}

export function useGoogleAuth(): UseGoogleAuthReturn {
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Configure Google OAuth request
  // useIdTokenAuthRequest returns an ID token that Supabase can verify directly
  const [request, response, promptAsync] = Google.useIdTokenAuthRequest({
    webClientId: GOOGLE_WEB_CLIENT_ID,
    iosClientId: GOOGLE_IOS_CLIENT_ID,
    androidClientId: GOOGLE_ANDROID_CLIENT_ID,
    redirectUri: redirectUri,
  });

  // Handle the OAuth response
  useEffect(() => {
    const handleGoogleResponse = async () => {
      if (response?.type === 'success') {
        setIsLoading(true);
        setError(null);
        
        try {
          const idToken = response.params.id_token;
          
          if (!idToken) {
            throw new Error('No ID token received from Google');
          }

          console.log('[GoogleAuth] Received ID token, signing in with Supabase...');
          
          // Use Supabase's signInWithIdToken - this verifies the Google token server-side
          const supabase = getSupabase();
          const { data, error: supabaseError } = await supabase.auth.signInWithIdToken({
            provider: 'google',
            token: idToken,
          });

          if (supabaseError) {
            console.error('[GoogleAuth] Supabase signInWithIdToken error:', supabaseError);
            throw new Error(supabaseError.message || 'Failed to sign in with Google');
          }

          console.log('[GoogleAuth] Successfully signed in:', data.user?.email);
          // Session will be picked up by SupabaseContext's onAuthStateChange listener
          
        } catch (err: any) {
          console.error('[GoogleAuth] Error:', err);
          setError(err.message || 'Google sign-in failed');
        } finally {
          setIsLoading(false);
        }
      } else if (response?.type === 'error') {
        console.error('[GoogleAuth] OAuth error:', response.error);
        setError(response.error?.message || 'Google sign-in was cancelled or failed');
      } else if (response?.type === 'dismiss' || response?.type === 'cancel') {
        console.log('[GoogleAuth] User cancelled sign-in');
        // Don't set error for user cancellation
      }
    };

    handleGoogleResponse();
  }, [response]);

  // Trigger the Google sign-in flow
  const signInWithGoogle = useCallback(async () => {
    if (!request) {
      setError('Google sign-in is not ready yet');
      return;
    }

    setError(null);
    setIsLoading(true);

    try {
      console.log('[GoogleAuth] Starting Google sign-in flow...');
      console.log('[GoogleAuth] Platform:', Platform.OS);
      console.log('[GoogleAuth] Redirect URI:', redirectUri);
      console.log('[GoogleAuth] Web Client ID:', GOOGLE_WEB_CLIENT_ID ? 'Set' : 'Missing');
      console.log('[GoogleAuth] iOS Client ID:', GOOGLE_IOS_CLIENT_ID ? 'Set' : 'Missing');
      console.log('[GoogleAuth] Android Client ID:', GOOGLE_ANDROID_CLIENT_ID ? 'Set' : 'Missing');
      
      await promptAsync();
      // Response will be handled in the useEffect above
    } catch (err: any) {
      console.error('[GoogleAuth] promptAsync error:', err);
      setError(err.message || 'Failed to start Google sign-in');
      setIsLoading(false);
    }
  }, [request, promptAsync]);

  return {
    signInWithGoogle,
    isLoading,
    error,
    isReady: !!request,
  };
}
