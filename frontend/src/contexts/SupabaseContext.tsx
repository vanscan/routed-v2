// Supabase Auth Context Provider - Platform-aware with SSR support
import React, { createContext, useContext, useEffect, useState, ReactNode } from 'react';
import { Platform } from 'react-native';

// Types from supabase
interface User {
  id: string;
  email?: string;
  [key: string]: unknown;
}

interface Session {
  access_token: string;
  user: User;
  [key: string]: unknown;
}

interface SupabaseContextType {
  user: User | null;
  session: Session | null;
  loading: boolean;
  isReady: boolean;
  signIn: (email: string, password: string) => Promise<{ error: Error | null }>;
  signUp: (email: string, password: string) => Promise<{ error: Error | null }>;
  signOut: () => Promise<void>;
  signInWithOAuth: (provider: 'google' | 'github' | 'apple') => Promise<{ error: Error | null }>;
}

const defaultContextValue: SupabaseContextType = {
  user: null,
  session: null,
  loading: true,
  isReady: false,
  signIn: async () => ({ error: new Error('Supabase not initialized') }),
  signUp: async () => ({ error: new Error('Supabase not initialized') }),
  signOut: async () => {},
  signInWithOAuth: async () => ({ error: new Error('Supabase not initialized') }),
};

const SupabaseContext = createContext<SupabaseContextType>(defaultContextValue);

// Check if we're on client side (works for both web and native)
const isClient = () => {
  if (Platform.OS !== 'web') return true;
  return typeof window !== 'undefined';
};

export function SupabaseProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);
  const [isReady, setIsReady] = useState(false);
  const [supabaseClient, setSupabaseClient] = useState<any>(null);

  useEffect(() => {
    // Only initialize on client side
    if (!isClient()) {
      setLoading(false);
      return;
    }

    // Dynamically import supabase to avoid SSR issues
    const initSupabase = async () => {
      try {
        const { supabase } = await import('../lib/supabase');
        setSupabaseClient(supabase);
        
        // Get initial session
        const { data: { session: initialSession } } = await supabase.auth.getSession();
        setSession(initialSession);
        setUser(initialSession?.user ?? null);
        setLoading(false);
        setIsReady(true);

        // Listen for auth changes
        const { data: { subscription } } = supabase.auth.onAuthStateChange(
          async (_event: string, newSession: Session | null) => {
            setSession(newSession);
            setUser(newSession?.user ?? null);
          }
        );

        return () => {
          subscription.unsubscribe();
        };
      } catch (error) {
        console.warn('[Supabase] Initialization failed:', error);
        setLoading(false);
      }
    };

    initSupabase();
  }, []);

  const signIn = async (email: string, password: string) => {
    if (!supabaseClient) return { error: new Error('Supabase not initialized') };
    try {
      const { error } = await supabaseClient.auth.signInWithPassword({ email, password });
      return { error: error as Error | null };
    } catch (error) {
      return { error: error as Error };
    }
  };

  const signUp = async (email: string, password: string) => {
    if (!supabaseClient) return { error: new Error('Supabase not initialized') };
    try {
      const { error } = await supabaseClient.auth.signUp({ email, password });
      return { error: error as Error | null };
    } catch (error) {
      return { error: error as Error };
    }
  };

  const signOut = async () => {
    if (!supabaseClient) return;
    await supabaseClient.auth.signOut();
  };

  const signInWithOAuth = async (provider: 'google' | 'github' | 'apple') => {
    if (!supabaseClient) return { error: new Error('Supabase not initialized') };
    try {
      const { error } = await supabaseClient.auth.signInWithOAuth({ provider });
      return { error: error as Error | null };
    } catch (error) {
      return { error: error as Error };
    }
  };

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
