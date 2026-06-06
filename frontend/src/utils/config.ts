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

// Production backend URL — used as a safe fallback when EXPO_PUBLIC_BACKEND_URL is not set.
// EXPO_PUBLIC_BACKEND_URL (Replit env / .env) takes priority so config survives a git sync.
const FALLBACK_BACKEND_URL = 'https://api.getrouted.xyz';

/**
 * Get the backend URL.
 * Priority:
 *  1. EXPO_PUBLIC_BACKEND_URL env var (set in Replit Secrets/env — survives git sync)
 *  2. Hardcoded production fallback (safe default for OTA updates without env configured)
 */
export function getBackendUrl(): string {
  return process.env.EXPO_PUBLIC_BACKEND_URL || FALLBACK_BACKEND_URL;
}

/**
 * Alias for backward compatibility — resolves at module load time.
 */
export const BACKEND_URL = process.env.EXPO_PUBLIC_BACKEND_URL || FALLBACK_BACKEND_URL;

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
