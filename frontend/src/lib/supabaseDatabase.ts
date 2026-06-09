/**
 * Supabase Database Integration for Routr
 * 
 * This provides direct Supabase Postgres access for features that benefit from:
 * - Real-time subscriptions
 * - Row-level security
 * - Postgres functions/triggers
 * 
 * Note: Primary data (stops, routes, users) is in MongoDB Atlas.
 * Supabase DB is used for supplementary features like:
 * - User preferences sync
 * - Real-time driver tracking for fleet view
 * - Delivery notifications
 * - Audit logs
 */

import { getSupabase } from './supabase';
import { RealtimeChannel } from '@supabase/supabase-js';

// Table names
export const TABLES = {
  USER_PREFERENCES: 'user_preferences',
  DRIVER_LOCATIONS: 'driver_locations',
  DELIVERY_EVENTS: 'delivery_events',
  NOTIFICATIONS: 'notifications',
  AUDIT_LOGS: 'audit_logs',
} as const;

export type TableName = typeof TABLES[keyof typeof TABLES];

// Type definitions
export interface UserPreferences {
  id?: string;
  user_id: string;
  prefer_familiar_roads: boolean;
  voice_enabled: boolean;
  auto_advance: boolean;
  map_style: string;
  units: 'metric' | 'imperial';
  created_at?: string;
  updated_at?: string;
}

export interface DriverLocation {
  id?: string;
  user_id: string;
  latitude: number;
  longitude: number;
  heading: number;
  speed: number;
  accuracy: number;
  timestamp: string;
  route_id?: string;
}

export interface DeliveryEvent {
  id?: string;
  user_id: string;
  stop_id: string;
  route_id: string;
  event_type: 'started' | 'arrived' | 'completed' | 'skipped' | 'failed';
  latitude?: number;
  longitude?: number;
  notes?: string;
  proof_photo_url?: string;
  signature_url?: string;
  created_at?: string;
}

export interface Notification {
  id?: string;
  user_id: string;
  title: string;
  body: string;
  type: 'info' | 'warning' | 'success' | 'error';
  read: boolean;
  data?: Record<string, any>;
  created_at?: string;
}

// ============================================
// User Preferences
// ============================================

/**
 * Get user preferences
 */
export const getUserPreferences = async (
  userId: string
): Promise<{ data: UserPreferences | null; error: string | null }> => {
  try {
    const client = getSupabase();
    const { data, error } = await client
      .from(TABLES.USER_PREFERENCES)
      .select('*')
      .eq('user_id', userId)
      .single();
    
    if (error && error.code !== 'PGRST116') { // PGRST116 = no rows
      return { data: null, error: error.message };
    }
    
    return { data: data as UserPreferences | null, error: null };
  } catch (e: any) {
    return { data: null, error: e.message };
  }
};

/**
 * Upsert user preferences
 */
export const saveUserPreferences = async (
  prefs: UserPreferences
): Promise<{ success: boolean; error: string | null }> => {
  try {
    const client = getSupabase();
    const { error } = await client
      .from(TABLES.USER_PREFERENCES)
      .upsert(
        { ...prefs, updated_at: new Date().toISOString() },
        { onConflict: 'user_id' }
      );
    
    if (error) {
      return { success: false, error: error.message };
    }
    
    return { success: true, error: null };
  } catch (e: any) {
    return { success: false, error: e.message };
  }
};

// ============================================
// Driver Location (Real-time fleet tracking)
// ============================================

/**
 * Update driver location
 */
export const updateDriverLocation = async (
  location: DriverLocation
): Promise<{ success: boolean; error: string | null }> => {
  try {
    const client = getSupabase();
    const { error } = await client
      .from(TABLES.DRIVER_LOCATIONS)
      .upsert(location, { onConflict: 'user_id' });
    
    if (error) {
      return { success: false, error: error.message };
    }
    
    return { success: true, error: null };
  } catch (e: any) {
    return { success: false, error: e.message };
  }
};

/**
 * Subscribe to driver location updates (for fleet view)
 */
export const subscribeToDriverLocations = (
  onUpdate: (location: DriverLocation) => void,
  onError?: (error: any) => void
): RealtimeChannel => {
  const client = getSupabase();
  
  const channel = client
    .channel('driver-locations')
    .on(
      'postgres_changes',
      {
        event: '*',
        schema: 'public',
        table: TABLES.DRIVER_LOCATIONS,
      },
      (payload) => {
        if (payload.new) {
          onUpdate(payload.new as DriverLocation);
        }
      }
    )
    .subscribe((status) => {
      if (status === 'CHANNEL_ERROR' && onError) {
        onError(new Error('Channel error'));
      }
    });
  
  return channel;
};

/**
 * Get all active driver locations (for fleet view)
 */
export const getActiveDriverLocations = async (
  maxAgeMinutes: number = 15
): Promise<{ data: DriverLocation[]; error: string | null }> => {
  try {
    const client = getSupabase();
    const cutoff = new Date(Date.now() - maxAgeMinutes * 60 * 1000).toISOString();
    
    const { data, error } = await client
      .from(TABLES.DRIVER_LOCATIONS)
      .select('*')
      .gte('timestamp', cutoff)
      .order('timestamp', { ascending: false });
    
    if (error) {
      return { data: [], error: error.message };
    }
    
    return { data: data as DriverLocation[], error: null };
  } catch (e: any) {
    return { data: [], error: e.message };
  }
};

// ============================================
// Delivery Events
// ============================================

/**
 * Log a delivery event
 */
export const logDeliveryEvent = async (
  event: DeliveryEvent
): Promise<{ success: boolean; error: string | null }> => {
  try {
    const client = getSupabase();
    const { error } = await client
      .from(TABLES.DELIVERY_EVENTS)
      .insert({ ...event, created_at: new Date().toISOString() });
    
    if (error) {
      return { success: false, error: error.message };
    }
    
    return { success: true, error: null };
  } catch (e: any) {
    return { success: false, error: e.message };
  }
};

/**
 * Get delivery events for a route
 */
export const getRouteEvents = async (
  routeId: string
): Promise<{ data: DeliveryEvent[]; error: string | null }> => {
  try {
    const client = getSupabase();
    const { data, error } = await client
      .from(TABLES.DELIVERY_EVENTS)
      .select('*')
      .eq('route_id', routeId)
      .order('created_at', { ascending: true });
    
    if (error) {
      return { data: [], error: error.message };
    }
    
    return { data: data as DeliveryEvent[], error: null };
  } catch (e: any) {
    return { data: [], error: e.message };
  }
};

// ============================================
// Notifications
// ============================================

/**
 * Get unread notifications for a user
 */
export const getUnreadNotifications = async (
  userId: string
): Promise<{ data: Notification[]; error: string | null }> => {
  try {
    const client = getSupabase();
    const { data, error } = await client
      .from(TABLES.NOTIFICATIONS)
      .select('*')
      .eq('user_id', userId)
      .eq('read', false)
      .order('created_at', { ascending: false })
      .limit(50);
    
    if (error) {
      return { data: [], error: error.message };
    }
    
    return { data: data as Notification[], error: null };
  } catch (e: any) {
    return { data: [], error: e.message };
  }
};

/**
 * Mark notifications as read
 */
export const markNotificationsRead = async (
  notificationIds: string[]
): Promise<{ success: boolean; error: string | null }> => {
  try {
    const client = getSupabase();
    const { error } = await client
      .from(TABLES.NOTIFICATIONS)
      .update({ read: true })
      .in('id', notificationIds);
    
    if (error) {
      return { success: false, error: error.message };
    }
    
    return { success: true, error: null };
  } catch (e: any) {
    return { success: false, error: e.message };
  }
};

/**
 * Subscribe to new notifications
 */
export const subscribeToNotifications = (
  userId: string,
  onNotification: (notification: Notification) => void
): RealtimeChannel => {
  const client = getSupabase();
  
  const channel = client
    .channel(`notifications-${userId}`)
    .on(
      'postgres_changes',
      {
        event: 'INSERT',
        schema: 'public',
        table: TABLES.NOTIFICATIONS,
        filter: `user_id=eq.${userId}`,
      },
      (payload) => {
        if (payload.new) {
          onNotification(payload.new as Notification);
        }
      }
    )
    .subscribe();
  
  return channel;
};

// ============================================
// Audit Logs
// ============================================

/**
 * Log an audit event
 */
export const logAuditEvent = async (
  userId: string,
  action: string,
  details: Record<string, any>
): Promise<void> => {
  try {
    const client = getSupabase();
    await client
      .from(TABLES.AUDIT_LOGS)
      .insert({
        user_id: userId,
        action,
        details,
        created_at: new Date().toISOString(),
      });
  } catch (e) {
    // Audit logging is best-effort, don't throw
    console.warn('[Audit] Failed to log event:', e);
  }
};

// ============================================
// Cleanup
// ============================================

/**
 * Unsubscribe from a channel
 */
export const unsubscribe = async (channel: RealtimeChannel): Promise<void> => {
  const client = getSupabase();
  await client.removeChannel(channel);
};

export default {
  TABLES,
  // User Preferences
  getUserPreferences,
  saveUserPreferences,
  // Driver Locations
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
};
