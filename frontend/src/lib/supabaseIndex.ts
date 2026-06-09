/**
 * Supabase Integration Index
 * 
 * Re-exports all Supabase utilities for easy importing:
 * 
 * import { supabase, uploadProofPhoto, getUserPreferences } from '@/lib/supabaseIndex';
 */

// Core client
export { supabase, getSupabase } from './supabase';
export type { User, Session } from './supabase';

// Storage utilities
export {
  BUCKETS,
  uploadProofPhoto,
  uploadSignature,
  uploadRouteExport,
  uploadProfileImage,
  deleteFile,
  listUserFiles,
  getSignedUrl,
  initializeStorageBuckets,
} from './supabaseStorage';
export type { BucketName } from './supabaseStorage';

// Database utilities
export {
  TABLES,
  // User Preferences
  getUserPreferences,
  saveUserPreferences,
  // Driver Locations (Fleet tracking)
  updateDriverLocation,
  subscribeToDriverLocations,
  getActiveDriverLocations,
  // Delivery Events
  logDeliveryEvent,
  getRouteEvents,
  // Notifications
  getUnreadNotifications,
  markNotificationsRead,
  subscribeToNotifications,
  // Audit
  logAuditEvent,
  // Cleanup
  unsubscribe,
} from './supabaseDatabase';
export type {
  UserPreferences,
  DriverLocation,
  DeliveryEvent,
  Notification,
  TableName,
} from './supabaseDatabase';

// Auth context (use via React context)
export { SupabaseProvider, useSupabase, getSupabaseClient } from '../contexts/SupabaseContext';
