/**
 * Unified Auth Hook - Now fully powered by Supabase Auth
 * 
 * This hook provides a unified interface for authentication using Supabase:
 * - Google OAuth (Native SDK)
 * - Email/Password
 * - Magic Link
 * 
 * Usage:
 * ```tsx
 * const { user, isAuthenticated, signOut } = useUnifiedAuth();
 * ```
 */
import { useSupabase, SupabaseUser } from '../contexts/SupabaseContext';
import { useCallback, useMemo } from 'react';

export interface UnifiedUser {
  // Common fields
  id: string;
  email: string;
  name?: string;
  picture?: string;
  
  // Source info (always 'supabase' now)
  source: 'supabase';
  
  // Raw user object for advanced use
  supabaseUser: SupabaseUser | null;
}

export interface UnifiedAuthState {
  // User state
  user: UnifiedUser | null;
  isAuthenticated: boolean;
  loading: boolean;
  
  // Supabase auth methods
  supabaseAuth: {
    signIn: (email: string, password: string) => Promise<{ error: { message: string } | null }>;
    signUp: (email: string, password: string, metadata?: { full_name?: string }) => Promise<{ error: { message: string } | null; needsConfirmation?: boolean }>;
    signOut: () => Promise<void>;
    signInWithOAuth: (provider: 'google' | 'github' | 'apple') => Promise<{ error: { message: string } | null }>;
    signInWithMagicLink: (email: string) => Promise<{ error: { message: string } | null }>;
    resetPassword: (email: string) => Promise<{ error: { message: string } | null }>;
    updatePassword: (newPassword: string) => Promise<{ error: { message: string } | null }>;
    getAccessToken: () => Promise<string | null>;
    refreshSession: () => Promise<{ error: { message: string } | null }>;
    isReady: boolean;
  };
  
  // Convenience methods
  signOut: () => Promise<void>;
}

export function useUnifiedAuth(): UnifiedAuthState {
  const supabaseAuth = useSupabase();
  
  // Build unified user from Supabase user
  const user = useMemo<UnifiedUser | null>(() => {
    if (supabaseAuth.user) {
      return {
        id: supabaseAuth.user.id,
        email: supabaseAuth.user.email || '',
        name: supabaseAuth.user.user_metadata?.full_name as string || 
              supabaseAuth.user.user_metadata?.name as string,
        picture: supabaseAuth.user.user_metadata?.avatar_url as string ||
                supabaseAuth.user.user_metadata?.picture as string,
        source: 'supabase',
        supabaseUser: supabaseAuth.user,
      };
    }
    
    return null;
  }, [supabaseAuth.user]);
  
  const isAuthenticated = useMemo(() => {
    return !!supabaseAuth.user;
  }, [supabaseAuth.user]);
  
  const loading = supabaseAuth.loading;
  
  // Unified sign out
  const signOut = useCallback(async () => {
    await supabaseAuth.signOut();
  }, [supabaseAuth]);
  
  return {
    user,
    isAuthenticated,
    loading,
    
    supabaseAuth: {
      signIn: supabaseAuth.signIn,
      signUp: supabaseAuth.signUp,
      signOut: supabaseAuth.signOut,
      signInWithOAuth: supabaseAuth.signInWithOAuth,
      signInWithMagicLink: supabaseAuth.signInWithMagicLink,
      resetPassword: supabaseAuth.resetPassword,
      updatePassword: supabaseAuth.updatePassword,
      getAccessToken: supabaseAuth.getAccessToken,
      refreshSession: supabaseAuth.refreshSession,
      isReady: supabaseAuth.isReady,
    },
    
    signOut,
  };
}

export default useUnifiedAuth;
