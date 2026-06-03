/**
 * Unified Auth Hook - Bridges existing AuthContext with Supabase Auth
 * 
 * This hook provides a unified interface to access both auth systems:
 * - Primary: Existing AuthContext (Emergent Google OAuth, Email/Password)
 * - Secondary: Supabase Auth (for additional features like magic link, OAuth providers)
 * 
 * Usage:
 * ```tsx
 * const { user, isAuthenticated, signOut } = useUnifiedAuth();
 * ```
 */
import { useAuth } from '../context/AuthContext';
import { useSupabase, SupabaseUser } from '../contexts/SupabaseContext';
import { useCallback, useMemo } from 'react';

// Primary auth user type (from existing AuthContext)
interface PrimaryUser {
  user_id: string;
  email: string;
  name: string;
  picture?: string;
}

export interface UnifiedUser {
  // Common fields
  id: string;
  email: string;
  name?: string;
  picture?: string;
  
  // Source info
  source: 'primary' | 'supabase';
  
  // Raw user objects for advanced use
  primaryUser?: PrimaryUser | null;
  supabaseUser?: SupabaseUser | null;
}

export interface UnifiedAuthState {
  // User state
  user: UnifiedUser | null;
  isAuthenticated: boolean;
  loading: boolean;
  
  // Primary auth methods (existing system)
  primaryAuth: {
    login: (sessionId: string) => Promise<void>;
    loginWithEmail: (email: string, password: string) => Promise<void>;
    registerWithEmail: (email: string, password: string, name: string) => Promise<void>;
    loginAsReviewer: (email: string, passcode: string) => Promise<void>;
    logout: () => Promise<void>;
    reconnect: () => Promise<boolean>;
    reconnecting: boolean;
  };
  
  // Supabase auth methods (additional features)
  supabaseAuth: {
    signIn: (email: string, password: string) => Promise<{ error: { message: string } | null }>;
    signUp: (email: string, password: string, metadata?: { full_name?: string }) => Promise<{ error: { message: string } | null; needsConfirmation?: boolean }>;
    signOut: () => Promise<void>;
    signInWithOAuth: (provider: 'google' | 'github' | 'apple') => Promise<{ error: { message: string } | null }>;
    signInWithMagicLink: (email: string) => Promise<{ error: { message: string } | null }>;
    resetPassword: (email: string) => Promise<{ error: { message: string } | null }>;
    updatePassword: (newPassword: string) => Promise<{ error: { message: string } | null }>;
    getAccessToken: () => Promise<string | null>;
    isReady: boolean;
  };
  
  // Convenience methods
  signOut: () => Promise<void>;
}

export function useUnifiedAuth(): UnifiedAuthState {
  const primaryAuth = useAuth();
  const supabaseAuth = useSupabase();
  
  // Determine the unified user state
  // Primary auth takes precedence since it's the existing system
  const user = useMemo<UnifiedUser | null>(() => {
    if (primaryAuth.user) {
      return {
        id: primaryAuth.user.user_id,
        email: primaryAuth.user.email,
        name: primaryAuth.user.name,
        picture: primaryAuth.user.picture,
        source: 'primary',
        primaryUser: primaryAuth.user,
        supabaseUser: supabaseAuth.user,
      };
    }
    
    if (supabaseAuth.user) {
      return {
        id: supabaseAuth.user.id,
        email: supabaseAuth.user.email || '',
        name: supabaseAuth.user.user_metadata?.full_name as string || 
              supabaseAuth.user.user_metadata?.name as string,
        picture: supabaseAuth.user.user_metadata?.avatar_url as string ||
                supabaseAuth.user.user_metadata?.picture as string,
        source: 'supabase',
        primaryUser: null,
        supabaseUser: supabaseAuth.user,
      };
    }
    
    return null;
  }, [primaryAuth.user, supabaseAuth.user]);
  
  const isAuthenticated = useMemo(() => {
    return !!primaryAuth.user || !!supabaseAuth.user;
  }, [primaryAuth.user, supabaseAuth.user]);
  
  const loading = useMemo(() => {
    return primaryAuth.loading || supabaseAuth.loading;
  }, [primaryAuth.loading, supabaseAuth.loading]);
  
  // Unified sign out - logs out from both systems
  const signOut = useCallback(async () => {
    const promises: Promise<void>[] = [];
    
    if (primaryAuth.user) {
      promises.push(primaryAuth.logout());
    }
    
    if (supabaseAuth.user) {
      promises.push(supabaseAuth.signOut());
    }
    
    await Promise.all(promises);
  }, [primaryAuth, supabaseAuth]);
  
  return {
    user,
    isAuthenticated,
    loading,
    
    primaryAuth: {
      login: primaryAuth.login,
      loginWithEmail: primaryAuth.loginWithEmail,
      registerWithEmail: primaryAuth.registerWithEmail,
      loginAsReviewer: primaryAuth.loginAsReviewer,
      logout: primaryAuth.logout,
      reconnect: primaryAuth.reconnect,
      reconnecting: primaryAuth.reconnecting,
    },
    
    supabaseAuth: {
      signIn: supabaseAuth.signIn,
      signUp: supabaseAuth.signUp,
      signOut: supabaseAuth.signOut,
      signInWithOAuth: supabaseAuth.signInWithOAuth,
      signInWithMagicLink: supabaseAuth.signInWithMagicLink,
      resetPassword: supabaseAuth.resetPassword,
      updatePassword: supabaseAuth.updatePassword,
      getAccessToken: supabaseAuth.getAccessToken,
      isReady: supabaseAuth.isReady,
    },
    
    signOut,
  };
}

export default useUnifiedAuth;
