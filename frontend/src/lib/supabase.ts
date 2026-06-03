import 'react-native-url-polyfill/auto'
import { createClient, SupabaseClient, User, Session } from '@supabase/supabase-js'
import { Platform } from 'react-native'

// Re-export types for use in context
export type { User, Session }

// Singleton instance - only created on client
let supabaseInstance: SupabaseClient | null = null

// Check if we're on client side (works for both web and native)
const isClient = (): boolean => {
  if (Platform.OS !== 'web') return true
  return typeof window !== 'undefined'
}

// Create the supabase client lazily
export const getSupabase = (): SupabaseClient => {
  // For SSR, throw a helpful error
  if (!isClient()) {
    throw new Error('Supabase client cannot be accessed during SSR. Use dynamic import in useEffect.')
  }

  if (supabaseInstance) {
    return supabaseInstance
  }

  // Import AsyncStorage synchronously on client
  const AsyncStorage = require('@react-native-async-storage/async-storage').default

  supabaseInstance = createClient(
    process.env.EXPO_PUBLIC_SUPABASE_URL || '',
    process.env.EXPO_PUBLIC_SUPABASE_KEY || '',
    {
      auth: {
        storage: AsyncStorage,
        autoRefreshToken: true,
        persistSession: true,
        detectSessionInUrl: Platform.OS === 'web',
        flowType: 'pkce',
      },
    }
  )

  return supabaseInstance
}

// Export a getter for the supabase client - ONLY use this on client side
export const supabase = {
  get auth() {
    return getSupabase().auth
  },
  get from() {
    return getSupabase().from.bind(getSupabase())
  },
  get storage() {
    return getSupabase().storage
  },
  get functions() {
    return getSupabase().functions
  },
  get realtime() {
    return getSupabase().realtime
  },
  get rpc() {
    return getSupabase().rpc.bind(getSupabase())
  },
}
