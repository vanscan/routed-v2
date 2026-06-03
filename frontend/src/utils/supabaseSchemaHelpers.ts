/**
 * Supabase Schema Helpers - Type-safe database operations
 * 
 * These functions are specifically designed to work with the Supabase schema
 * defined in supabase-schema.sql
 */

import {
  queryTable,
  getById,
  insertIntoTable,
  updateTable,
  deleteFromTable,
  upsertIntoTable,
  subscribeToTable,
  DatabaseResult,
} from './supabaseDatabase';

// =====================================================
// TYPE DEFINITIONS (matching supabase-schema.sql)
// =====================================================

export interface Profile {
  id: string;
  email: string | null;
  full_name: string | null;
  avatar_url: string | null;
  phone: string | null;
  company_name: string | null;
  plan_type: 'free' | 'pro' | 'enterprise';
  plan_expires_at: string | null;
  routes_optimized_count: number;
  last_route_at: string | null;
  default_vehicle_type: string;
  preferred_units: 'metric' | 'imperial';
  timezone: string;
  created_at: string;
  updated_at: string;
}

export interface SavedLocation {
  id: string;
  user_id: string;
  name: string;
  address: string | null;
  latitude: number;
  longitude: number;
  category: 'home' | 'work' | 'warehouse' | 'customer' | 'other';
  tags: string[] | null;
  contact_name: string | null;
  contact_phone: string | null;
  notes: string | null;
  default_service_time_minutes: number;
  preferred_time_start: string | null;
  preferred_time_end: string | null;
  is_favorite: boolean;
  use_count: number;
  last_used_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface RouteStop {
  id?: string;
  name: string;
  address: string;
  latitude: number;
  longitude: number;
  service_time_minutes?: number;
  time_window?: { start: string; end: string };
  notes?: string;
}

export interface RouteHistory {
  id: string;
  user_id: string;
  route_name: string | null;
  stops: RouteStop[];
  optimized_order: number[] | null;
  total_distance_meters: number | null;
  total_duration_seconds: number | null;
  stop_count: number | null;
  solver_used: string | null;
  optimization_time_ms: number | null;
  status: 'planned' | 'in_progress' | 'completed' | 'cancelled';
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface RouteTemplate {
  id: string;
  user_id: string;
  name: string;
  description: string | null;
  stops: RouteStop[];
  is_recurring: boolean;
  recurrence_days: number[] | null;
  preferred_start_time: string | null;
  use_count: number;
  last_used_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface UserSettings {
  id: string;
  user_id: string;
  map_style: string;
  show_traffic: boolean;
  default_zoom_level: number;
  default_solver: string;
  optimize_for: 'distance' | 'time' | 'balanced';
  avoid_tolls: boolean;
  avoid_highways: boolean;
  vehicle_type: string;
  vehicle_capacity: { weight?: number; volume?: number } | null;
  email_notifications: boolean;
  push_notifications: boolean;
  route_completion_alerts: boolean;
  default_export_format: 'csv' | 'xlsx' | 'json' | 'gpx';
  created_at: string;
  updated_at: string;
}

export interface ImportHistory {
  id: string;
  user_id: string;
  file_name: string;
  file_type: string | null;
  file_size_bytes: number | null;
  storage_path: string | null;
  total_rows: number | null;
  successful_rows: number | null;
  failed_rows: number | null;
  error_details: Array<{ row: number; error: string }> | null;
  status: 'pending' | 'processing' | 'completed' | 'failed';
  created_at: string;
  completed_at: string | null;
}

// =====================================================
// PROFILE HELPERS
// =====================================================

export async function getProfile(userId: string): Promise<DatabaseResult<Profile>> {
  return getById<Profile>('profiles', userId);
}

export async function updateProfile(
  userId: string,
  updates: Partial<Omit<Profile, 'id' | 'created_at' | 'updated_at'>>
): Promise<DatabaseResult<Profile[]>> {
  return updateTable<Profile>('profiles', updates, { id: userId });
}

// =====================================================
// SAVED LOCATIONS HELPERS
// =====================================================

export async function getSavedLocations(userId: string): Promise<DatabaseResult<SavedLocation[]>> {
  return queryTable<SavedLocation>('saved_locations', {
    filter: { user_id: userId },
    order: [
      { column: 'is_favorite', ascending: false },
      { column: 'use_count', ascending: false },
    ],
  });
}

export async function getFavoriteLocations(userId: string): Promise<DatabaseResult<SavedLocation[]>> {
  return queryTable<SavedLocation>('saved_locations', {
    filter: { user_id: userId, is_favorite: true },
    order: { column: 'name', ascending: true },
  });
}

export async function getLocationsByCategory(
  userId: string,
  category: SavedLocation['category']
): Promise<DatabaseResult<SavedLocation[]>> {
  return queryTable<SavedLocation>('saved_locations', {
    filter: { user_id: userId, category },
    order: { column: 'name', ascending: true },
  });
}

export async function createSavedLocation(
  location: Omit<SavedLocation, 'id' | 'created_at' | 'updated_at' | 'use_count' | 'last_used_at'>
): Promise<DatabaseResult<SavedLocation[]>> {
  return insertIntoTable<SavedLocation>('saved_locations', location);
}

export async function updateSavedLocation(
  locationId: string,
  updates: Partial<Omit<SavedLocation, 'id' | 'user_id' | 'created_at' | 'updated_at'>>
): Promise<DatabaseResult<SavedLocation[]>> {
  return updateTable<SavedLocation>('saved_locations', updates, { id: locationId });
}

export async function deleteSavedLocation(locationId: string): Promise<{ error: Error | null }> {
  return deleteFromTable('saved_locations', { id: locationId });
}

export async function incrementLocationUseCount(locationId: string): Promise<DatabaseResult<SavedLocation[]>> {
  // Note: For proper increment, use an RPC function. This is a simplified version.
  return updateTable<SavedLocation>('saved_locations', { 
    last_used_at: new Date().toISOString() 
  }, { id: locationId });
}

// =====================================================
// ROUTE HISTORY HELPERS
// =====================================================

export async function getRouteHistory(
  userId: string,
  options?: { limit?: number; offset?: number; status?: RouteHistory['status'] }
): Promise<DatabaseResult<RouteHistory[]>> {
  const filter: Record<string, unknown> = { user_id: userId };
  if (options?.status) {
    filter.status = options.status;
  }
  
  return queryTable<RouteHistory>('route_history', {
    filter,
    order: { column: 'created_at', ascending: false },
    limit: options?.limit || 50,
    offset: options?.offset,
  });
}

export async function getRouteById(routeId: string): Promise<DatabaseResult<RouteHistory>> {
  return getById<RouteHistory>('route_history', routeId);
}

export async function createRoute(
  route: Omit<RouteHistory, 'id' | 'created_at' | 'updated_at'>
): Promise<DatabaseResult<RouteHistory[]>> {
  return insertIntoTable<RouteHistory>('route_history', route);
}

export async function updateRouteStatus(
  routeId: string,
  status: RouteHistory['status'],
  additionalUpdates?: Partial<RouteHistory>
): Promise<DatabaseResult<RouteHistory[]>> {
  const updates: Partial<RouteHistory> = { status, ...additionalUpdates };
  
  if (status === 'in_progress' && !additionalUpdates?.started_at) {
    updates.started_at = new Date().toISOString();
  } else if (status === 'completed' && !additionalUpdates?.completed_at) {
    updates.completed_at = new Date().toISOString();
  }
  
  return updateTable<RouteHistory>('route_history', updates, { id: routeId });
}

export async function deleteRoute(routeId: string): Promise<{ error: Error | null }> {
  return deleteFromTable('route_history', { id: routeId });
}

// =====================================================
// ROUTE TEMPLATES HELPERS
// =====================================================

export async function getRouteTemplates(userId: string): Promise<DatabaseResult<RouteTemplate[]>> {
  return queryTable<RouteTemplate>('route_templates', {
    filter: { user_id: userId },
    order: { column: 'use_count', ascending: false },
  });
}

export async function createRouteTemplate(
  template: Omit<RouteTemplate, 'id' | 'created_at' | 'updated_at' | 'use_count' | 'last_used_at'>
): Promise<DatabaseResult<RouteTemplate[]>> {
  return insertIntoTable<RouteTemplate>('route_templates', template);
}

export async function updateRouteTemplate(
  templateId: string,
  updates: Partial<Omit<RouteTemplate, 'id' | 'user_id' | 'created_at' | 'updated_at'>>
): Promise<DatabaseResult<RouteTemplate[]>> {
  return updateTable<RouteTemplate>('route_templates', updates, { id: templateId });
}

export async function deleteRouteTemplate(templateId: string): Promise<{ error: Error | null }> {
  return deleteFromTable('route_templates', { id: templateId });
}

// =====================================================
// USER SETTINGS HELPERS
// =====================================================

export async function getUserSettings(userId: string): Promise<DatabaseResult<UserSettings>> {
  const result = await queryTable<UserSettings>('user_settings', {
    filter: { user_id: userId },
    limit: 1,
  });
  
  return {
    data: result.data?.[0] || null,
    error: result.error,
  };
}

export async function upsertUserSettings(
  settings: Omit<UserSettings, 'id' | 'created_at' | 'updated_at'>
): Promise<DatabaseResult<UserSettings[]>> {
  return upsertIntoTable<UserSettings>('user_settings', settings, {
    onConflict: 'user_id',
  });
}

// =====================================================
// IMPORT HISTORY HELPERS
// =====================================================

export async function getImportHistory(
  userId: string,
  limit: number = 20
): Promise<DatabaseResult<ImportHistory[]>> {
  return queryTable<ImportHistory>('import_history', {
    filter: { user_id: userId },
    order: { column: 'created_at', ascending: false },
    limit,
  });
}

export async function createImportRecord(
  importData: Omit<ImportHistory, 'id' | 'created_at' | 'completed_at'>
): Promise<DatabaseResult<ImportHistory[]>> {
  return insertIntoTable<ImportHistory>('import_history', importData);
}

export async function updateImportStatus(
  importId: string,
  status: ImportHistory['status'],
  results?: {
    total_rows?: number;
    successful_rows?: number;
    failed_rows?: number;
    error_details?: Array<{ row: number; error: string }>;
  }
): Promise<DatabaseResult<ImportHistory[]>> {
  const updates: Partial<ImportHistory> = { status, ...results };
  
  if (status === 'completed' || status === 'failed') {
    updates.completed_at = new Date().toISOString();
  }
  
  return updateTable<ImportHistory>('import_history', updates, { id: importId });
}

// =====================================================
// REALTIME SUBSCRIPTIONS
// =====================================================

export async function subscribeToRouteUpdates(
  userId: string,
  callback: (payload: { eventType: string; new: RouteHistory; old: RouteHistory }) => void
): Promise<() => void> {
  return subscribeToTable('route_history', callback as any, {
    filter: `user_id=eq.${userId}`,
  });
}

export async function subscribeToLocationUpdates(
  userId: string,
  callback: (payload: { eventType: string; new: SavedLocation; old: SavedLocation }) => void
): Promise<() => void> {
  return subscribeToTable('saved_locations', callback as any, {
    filter: `user_id=eq.${userId}`,
  });
}
