/**
 * Centralized configuration for the app.
 * 
 * CRITICAL: The backend URL is hardcoded here to prevent OTA updates 
 * from accidentally using the wrong environment (e.g., preview URLs).
 * 
 * This file should be imported everywhere instead of directly accessing
 * process.env.EXPO_PUBLIC_BACKEND_URL.
 */

import Constants from 'expo-constants';

// HARDCODED production backend URL - DO NOT CHANGE
// This ensures all builds and OTA updates always point to production
const HARDCODED_BACKEND_URL = 'https://api.getrouted.xyz';

/**
 * Get the backend URL. Always returns the hardcoded production URL
 * to prevent OTA updates from using preview/development URLs.
 */
export function getBackendUrl(): string {
  // Priority:
  // 1. Hardcoded production URL (always wins for safety)
  // 2. app.config.js extra.backendUrl (for builds)
  // 3. Environment variable (fallback, but shouldn't be needed)
  
  return HARDCODED_BACKEND_URL;
}

/**
 * Alias for backward compatibility
 */
export const BACKEND_URL = HARDCODED_BACKEND_URL;

/**
 * Get Supabase URL from environment
 */
export function getSupabaseUrl(): string {
  return process.env.EXPO_PUBLIC_SUPABASE_URL || '';
}

/**
 * Get Supabase anon key from environment
 */
export function getSupabaseAnonKey(): string {
  return process.env.EXPO_PUBLIC_SUPABASE_ANON_KEY || '';
}

/**
 * Google OAuth client IDs
 */
export const googleClientIds = {
  web: process.env.EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID || '',
  ios: process.env.EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID || '',
  android: process.env.EXPO_PUBLIC_GOOGLE_ANDROID_CLIENT_ID || '',
};
