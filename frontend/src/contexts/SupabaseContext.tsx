// Enhanced Supabase Auth Context Provider - Platform-aware with SSR support
import React, { createContext, useContext, useEffect, useState, ReactNode, useCallback } from 'react';
import { Platform } from 'react-native';
import * as Linking from 'expo-linking';
import type { User as SupabaseUserType, Session as SupabaseSessionType, AuthError as SupabaseAuthError } from '@supabase/supabase-js';
import { registerSupabaseClientGetter } from '../utils/authTokenBridge';

// Re-export Supabase types with our aliases
export type SupabaseUser = SupabaseUserType;
export type SupabaseSession = SupabaseSessionType;

export interface AuthError {
  message: string;
  status?: number;
}

export interface SupabaseContextType {
  // State
  user: SupabaseUser | null;
  session: SupabaseSession | null;
  loading: boolean;
  isReady: boolean;
  
  // Auth methods
  signIn: (email: string, password: string) => Promise<{ error: AuthError | null }>;
  signUp: (email: string, password: string, metadata?: { full_name?: string }) => Promise<{ error: AuthError | null; needsConfirmation?: boolean }>;
  signOut: () => Promise<void>;
  signInWithOAuth: (provider: 'google' | 'github' | 'apple') => Promise<{ error: AuthError | null }>;
  
  // Password management
  resetPassword: (email: string) => Promise<{ error: AuthError | null }>;
  updatePassword: (newPassword: string) => Promise<{ error: AuthError | null }>;
  
  // Magic link
  signInWithMagicLink: (email: string) => Promise<{ error: AuthError | null }>;
  
  // Session management
  refreshSession: () => Promise<{ error: AuthError | null }>;
  getAccessToken: () => Promise<string | null>;
}

const defaultContextValue: SupabaseContextType = {
  user: null,
  session: null,
  loading: true,
  isReady: false,
  signIn: async () => ({ error: { message: 'Supabase not initialized' } }),
  signUp: async () => ({ error: { message: 'Supabase not initialized' } }),
  signOut: async () => {},
  signInWithOAuth: async () => ({ error: { message: 'Supabase not initialized' } }),
  resetPassword: async () => ({ error: { message: 'Supabase not initialized' } }),
  updatePassword: async () => ({ error: { message: 'Supabase not initialized' } }),
  signInWithMagicLink: async () => ({ error: { message: 'Supabase not initialized' } }),
  refreshSession: async () => ({ error: { message: 'Supabase not initialized' } }),
  getAccessToken: async () => null,
};

const SupabaseContext = createContext<SupabaseContextType>(defaultContextValue);

// Check if we're on client side (works for both web and native)
const isClient = () => {
  if (Platform.OS !== 'web') return true;
  return typeof window !== 'undefined';
};

export function SupabaseProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<SupabaseUser | null>(null);
  const [session, setSession] = useState<SupabaseSession | null>(null);
  const [loading, setLoading] = useState(true);
  const [isReady, setIsReady] = useState(false);
  const [supabaseClient, setSupabaseClient] = useState<any>(null);

  useEffect(() => {
    // Only initialize on client side
    if (!isClient()) {
      setLoading(false);
      return;
    }

    let subscription: { unsubscribe: () => void } | null = null;

    // Dynamically import supabase to avoid SSR issues
    const initSupabase = async () => {
      try {
        const { supabase } = await import('../lib/supabase');
        setSupabaseClient(supabase);
        
        // Register the supabase client getter with the auth token bridge
        // This allows stopsStore and other API callers to get tokens
        registerSupabaseClientGetter(async () => {
          const { getSupabase } = await import('../lib/supabase');
          return getSupabase();
        });
        
        // Get initial session
        const { data: { session: initialSession } } = await supabase.auth.getSession();
        setSession(initialSession);
        setUser(initialSession?.user ?? null);
        setLoading(false);
        setIsReady(true);

        // Listen for auth changes
        const { data: { subscription: sub } } = supabase.auth.onAuthStateChange(
          (_event, newSession) => {
            setSession(newSession);
            setUser(newSession?.user ?? null);
          }
        );
        subscription = sub;
      } catch (error) {
        console.warn('[Supabase] Initialization failed:', error);
        setLoading(false);
      }
    };

    initSupabase();

    return () => {
      subscription?.unsubscribe();
    };
  }, []);

  const signIn = useCallback(async (email: string, password: string) => {
    if (!supabaseClient) return { error: { message: 'Supabase not initialized' } };
    try {
      const { error } = await supabaseClient.auth.signInWithPassword({ email, password });
      return { error: error ? { message: error.message, status: error.status } : null };
    } catch (error: any) {
      return { error: { message: error?.message || 'Sign in failed' } };
    }
  }, [supabaseClient]);

  const signUp = useCallback(async (email: string, password: string, metadata?: { full_name?: string }) => {
    if (!supabaseClient) return { error: { message: 'Supabase not initialized' } };
    try {
      const { data, error } = await supabaseClient.auth.signUp({
        email,
        password,
        options: {
          data: metadata,
        },
      });
      
      // Check if email confirmation is required
      const needsConfirmation = !error && data?.user && !data?.session;
      
      return {
        error: error ? { message: error.message, status: error.status } : null,
        needsConfirmation,
      };
    } catch (error: any) {
      return { error: { message: error?.message || 'Sign up failed' } };
    }
  }, [supabaseClient]);

  const signOut = useCallback(async () => {
    if (!supabaseClient) return;
    await supabaseClient.auth.signOut();
  }, [supabaseClient]);

  const signInWithOAuth = useCallback(async (provider: 'google' | 'github' | 'apple') => {
    if (!supabaseClient) return { error: { message: 'Supabase not initialized' } };
    try {
      // Build the redirect URL based on platform
      // Native apps use the custom scheme (routr://), web uses origin
      const redirectUrl = Platform.OS === 'web' 
        ? window.location.origin 
        : Linking.createURL('/');
      
      console.log('[Supabase OAuth] Provider:', provider, 'Redirect URL:', redirectUrl);
      
      const { error } = await supabaseClient.auth.signInWithOAuth({
        provider,
        options: {
          redirectTo: redirectUrl,
          // Skip the intermediate Supabase auth page and go directly to the provider
          skipBrowserRedirect: Platform.OS !== 'web',
        },
      });
      return { error: error ? { message: error.message, status: error.status } : null };
    } catch (error: any) {
      return { error: { message: error?.message || 'OAuth sign in failed' } };
    }
  }, [supabaseClient]);

  const resetPassword = useCallback(async (email: string) => {
    if (!supabaseClient) return { error: { message: 'Supabase not initialized' } };
    try {
      // Build redirect URL - native uses custom scheme, web uses origin
      const redirectUrl = Platform.OS === 'web' 
        ? `${window.location.origin}/reset-password`
        : Linking.createURL('/reset-password');
      
      const { error } = await supabaseClient.auth.resetPasswordForEmail(email, {
        redirectTo: redirectUrl,
      });
      return { error: error ? { message: error.message, status: error.status } : null };
    } catch (error: any) {
      return { error: { message: error?.message || 'Password reset failed' } };
    }
  }, [supabaseClient]);

  const updatePassword = useCallback(async (newPassword: string) => {
    if (!supabaseClient) return { error: { message: 'Supabase not initialized' } };
    try {
      const { error } = await supabaseClient.auth.updateUser({ password: newPassword });
      return { error: error ? { message: error.message, status: error.status } : null };
    } catch (error: any) {
      return { error: { message: error?.message || 'Password update failed' } };
    }
  }, [supabaseClient]);

  const signInWithMagicLink = useCallback(async (email: string) => {
    if (!supabaseClient) return { error: { message: 'Supabase not initialized' } };
    try {
      // Build redirect URL - native uses custom scheme, web uses origin
      const redirectUrl = Platform.OS === 'web' 
        ? window.location.origin
        : Linking.createURL('/');
      
      const { error } = await supabaseClient.auth.signInWithOtp({
        email,
        options: {
          emailRedirectTo: redirectUrl,
        },
      });
      return { error: error ? { message: error.message, status: error.status } : null };
    } catch (error: any) {
      return { error: { message: error?.message || 'Magic link failed' } };
    }
  }, [supabaseClient]);

  const refreshSession = useCallback(async () => {
    if (!supabaseClient) return { error: { message: 'Supabase not initialized' } };
    try {
      const { error } = await supabaseClient.auth.refreshSession();
      return { error: error ? { message: error.message, status: error.status } : null };
    } catch (error: any) {
      return { error: { message: error?.message || 'Session refresh failed' } };
    }
  }, [supabaseClient]);

  const getAccessToken = useCallback(async () => {
    if (!supabaseClient) return null;
    try {
      const { data: { session } } = await supabaseClient.auth.getSession();
      return session?.access_token || null;
    } catch {
      return null;
    }
  }, [supabaseClient]);

  return (
    <SupabaseContext.Provider
      value={{
        user,
        session,
        loading,
        isReady,
        signIn,
        signUp,
        signOut,
        signInWithOAuth,
        resetPassword,
        updatePassword,
        signInWithMagicLink,
        refreshSession,
        getAccessToken,
      }}
    >
      {children}
    </SupabaseContext.Provider>
  );
}

export function useSupabase() {
  const context = useContext(SupabaseContext);
  return context;
}

// Re-export for direct access (lazy loaded)
export const getSupabaseClient = async () => {
  if (!isClient()) {
    throw new Error('Supabase client can only be accessed on client side');
  }
  const { supabase } = await import('../lib/supabase');
  return supabase;
};
