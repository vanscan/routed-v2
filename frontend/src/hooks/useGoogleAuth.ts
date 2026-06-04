// Google OAuth hook using native @react-native-google-signin/google-signin with Supabase
import { useEffect, useCallback, useState } from 'react';
import { Platform } from 'react-native';
import {
  GoogleSignin,
  statusCodes,
  isSuccessResponse,
  isErrorWithCode,
} from '@react-native-google-signin/google-signin';
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
        
        console.log('[GoogleAuth] ID token received, authenticating with Supabase...');
        
        // Get Supabase client and sign in with the ID token
        const supabase = getSupabase();
        const { data: authData, error: supabaseError } = await supabase.auth.signInWithIdToken({
          provider: 'google',
          token: idToken,
        });
        
        if (supabaseError) {
          console.error('[GoogleAuth] Supabase auth error:', supabaseError);
          throw supabaseError;
        }
        
        console.log('[GoogleAuth] Successfully authenticated with Supabase');
        console.log('[GoogleAuth] User:', authData.user?.email);
        
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
