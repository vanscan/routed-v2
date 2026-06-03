// Supabase Hooks - Central Export
// This file exports all Supabase-related hooks for easy imports

// Auth hook (Google OAuth)
export { useGoogleAuth } from './useGoogleAuth';
export type { UseGoogleAuthReturn, GoogleAuthResult } from './useGoogleAuth';

// Storage hook (file uploads)
export { useSupabaseStorage } from './useSupabaseStorage';
export type { UploadProgress, UploadResult, FileInfo } from './useSupabaseStorage';

// Database hook (profiles, saved routes, generic queries)
export { useSupabaseDatabase } from './useSupabaseDatabase';
export type { 
  UserProfile, 
  ProfilePreferences, 
  SavedRoute 
} from './useSupabaseDatabase';

// Realtime hook (live subscriptions)
export { useRealtime, usePresence } from './useSupabaseRealtime';
export type { 
  RealtimeEvent, 
  RealtimeSubscription, 
  UseRealtimeOptions 
} from './useSupabaseRealtime';
