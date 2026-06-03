// Supabase Database hook for user profile and data management
import { useState, useCallback, useEffect } from 'react';
import { getSupabase } from '../lib/supabase';
import { useSupabase } from '../contexts/SupabaseContext';

// User profile interface matching Supabase public.profiles table
export interface UserProfile {
  id: string;
  email?: string;
  full_name?: string;
  avatar_url?: string;
  phone?: string;
  company_name?: string;
  
  // Subscription/Plan info
  plan_type?: 'free' | 'pro' | 'enterprise';
  plan_expires_at?: string;
  
  // Usage tracking
  routes_optimized_count?: number;
  last_route_at?: string;
  
  // Preferences
  default_vehicle_type?: string;
  preferred_units?: 'metric' | 'imperial';
  timezone?: string;
  
  created_at?: string;
  updated_at?: string;
}

export interface UserSettings {
  id: string;
  user_id: string;
  
  // Map preferences
  map_style?: 'streets' | 'satellite' | 'dark' | 'light';
  show_traffic?: boolean;
  default_zoom_level?: number;
  
  // Route optimization preferences
  default_solver?: 'auto' | 'lkh' | 'ortools' | 'vroom';
  optimize_for?: 'distance' | 'time' | 'balanced';
  avoid_tolls?: boolean;
  avoid_highways?: boolean;
  
  // Vehicle settings
  vehicle_type?: string;
  vehicle_capacity?: { weight?: number; volume?: number };
  
  // Notification preferences
  email_notifications?: boolean;
  push_notifications?: boolean;
  route_completion_alerts?: boolean;
  
  // Export preferences
  default_export_format?: 'csv' | 'xlsx' | 'json' | 'gpx';
  
  created_at?: string;
  updated_at?: string;
}

export interface SavedLocation {
  id: string;
  user_id: string;
  name: string;
  address?: string;
  latitude: number;
  longitude: number;
  category?: 'home' | 'work' | 'warehouse' | 'customer' | 'other';
  tags?: string[];
  contact_name?: string;
  contact_phone?: string;
  notes?: string;
  default_service_time_minutes?: number;
  preferred_time_start?: string;
  preferred_time_end?: string;
  is_favorite?: boolean;
  use_count?: number;
  last_used_at?: string;
  created_at?: string;
  updated_at?: string;
}

export interface RouteHistory {
  id: string;
  user_id: string;
  route_name?: string;
  stops: any;
  optimized_order?: any;
  total_distance_meters?: number;
  total_duration_seconds?: number;
  stop_count?: number;
  solver_used?: string;
  optimization_time_ms?: number;
  status?: 'planned' | 'in_progress' | 'completed' | 'cancelled';
  started_at?: string;
  completed_at?: string;
  created_at?: string;
  updated_at?: string;
}

export interface RouteTemplate {
  id: string;
  user_id: string;
  name: string;
  description?: string;
  stops: any;
  is_recurring?: boolean;
  recurrence_days?: number[];
  preferred_start_time?: string;
  use_count?: number;
  last_used_at?: string;
  created_at?: string;
  updated_at?: string;
}

export interface ImportHistory {
  id: string;
  user_id: string;
  file_name: string;
  file_type?: string;
  file_size_bytes?: number;
  storage_path?: string;
  total_rows?: number;
  successful_rows?: number;
  failed_rows?: number;
  error_details?: any;
  status?: 'pending' | 'processing' | 'completed' | 'failed';
  created_at?: string;
  completed_at?: string;
}

export function useSupabaseDatabase() {
  const { user } = useSupabase();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // ============================================
  // PROFILE OPERATIONS
  // ============================================

  /**
   * Fetch the current user's profile from Supabase
   */
  const getProfile = useCallback(async (): Promise<UserProfile | null> => {
    if (!user) {
      console.log('[Database] No user, cannot fetch profile');
      return null;
    }

    setLoading(true);
    setError(null);

    try {
      const supabase = getSupabase();
      
      const { data, error: fetchError } = await supabase
        .from('profiles')
        .select('*')
        .eq('id', user.id)
        .single();

      if (fetchError) {
        // Profile might not exist yet (new user)
        if (fetchError.code === 'PGRST116') {
          console.log('[Database] Profile not found, will be created on first update');
          return null;
        }
        throw new Error(fetchError.message);
      }

      console.log('[Database] Profile fetched:', data?.email);
      return data as UserProfile;
    } catch (err: any) {
      const errorMessage = err.message || 'Failed to fetch profile';
      console.error('[Database] Get profile error:', errorMessage);
      setError(errorMessage);
      return null;
    } finally {
      setLoading(false);
    }
  }, [user]);

  /**
   * Update the current user's profile
   */
  const updateProfile = useCallback(async (
    updates: Partial<Omit<UserProfile, 'id' | 'created_at'>>
  ): Promise<boolean> => {
    if (!user) {
      setError('User not authenticated');
      return false;
    }

    setLoading(true);
    setError(null);

    try {
      const supabase = getSupabase();
      
      const { error: updateError } = await supabase
        .from('profiles')
        .upsert({
          id: user.id,
          email: user.email,
          ...updates,
          updated_at: new Date().toISOString(),
        });

      if (updateError) {
        throw new Error(updateError.message);
      }

      console.log('[Database] Profile updated successfully');
      return true;
    } catch (err: any) {
      const errorMessage = err.message || 'Failed to update profile';
      console.error('[Database] Update profile error:', errorMessage);
      setError(errorMessage);
      return false;
    } finally {
      setLoading(false);
    }
  }, [user]);

  // ============================================
  // USER SETTINGS OPERATIONS
  // ============================================

  /**
   * Get user settings
   */
  const getSettings = useCallback(async (): Promise<UserSettings | null> => {
    if (!user) return null;

    setLoading(true);
    setError(null);

    try {
      const supabase = getSupabase();
      
      const { data, error: fetchError } = await supabase
        .from('user_settings')
        .select('*')
        .eq('user_id', user.id)
        .single();

      if (fetchError && fetchError.code !== 'PGRST116') {
        throw new Error(fetchError.message);
      }

      return data as UserSettings | null;
    } catch (err: any) {
      console.error('[Database] Get settings error:', err.message);
      setError(err.message);
      return null;
    } finally {
      setLoading(false);
    }
  }, [user]);

  /**
   * Update user settings
   */
  const updateSettings = useCallback(async (
    updates: Partial<Omit<UserSettings, 'id' | 'user_id' | 'created_at'>>
  ): Promise<boolean> => {
    if (!user) {
      setError('User not authenticated');
      return false;
    }

    setLoading(true);
    setError(null);

    try {
      const supabase = getSupabase();
      
      const { error: updateError } = await supabase
        .from('user_settings')
        .upsert({
          user_id: user.id,
          ...updates,
          updated_at: new Date().toISOString(),
        });

      if (updateError) {
        throw new Error(updateError.message);
      }

      console.log('[Database] Settings updated successfully');
      return true;
    } catch (err: any) {
      console.error('[Database] Update settings error:', err.message);
      setError(err.message);
      return false;
    } finally {
      setLoading(false);
    }
  }, [user]);

  // ============================================
  // SAVED LOCATIONS OPERATIONS
  // ============================================

  /**
   * Get all saved locations for the current user
   */
  const getSavedLocations = useCallback(async (
    options?: { category?: string; limit?: number }
  ): Promise<SavedLocation[]> => {
    if (!user) return [];

    setLoading(true);
    setError(null);

    try {
      const supabase = getSupabase();
      
      let query = supabase
        .from('saved_locations')
        .select('*')
        .eq('user_id', user.id)
        .order('is_favorite', { ascending: false })
        .order('use_count', { ascending: false });

      if (options?.category) {
        query = query.eq('category', options.category);
      }

      if (options?.limit) {
        query = query.limit(options.limit);
      }

      const { data, error: fetchError } = await query;

      if (fetchError) {
        throw new Error(fetchError.message);
      }

      return (data || []) as SavedLocation[];
    } catch (err: any) {
      console.error('[Database] Get locations error:', err.message);
      setError(err.message);
      return [];
    } finally {
      setLoading(false);
    }
  }, [user]);

  /**
   * Save a new location
   */
  const saveLocation = useCallback(async (
    location: Omit<SavedLocation, 'id' | 'user_id' | 'created_at' | 'updated_at'>
  ): Promise<SavedLocation | null> => {
    if (!user) {
      setError('User not authenticated');
      return null;
    }

    setLoading(true);
    setError(null);

    try {
      const supabase = getSupabase();
      
      const { data, error: insertError } = await supabase
        .from('saved_locations')
        .insert({
          user_id: user.id,
          ...location,
        })
        .select()
        .single();

      if (insertError) {
        throw new Error(insertError.message);
      }

      console.log('[Database] Location saved:', data?.name);
      return data as SavedLocation;
    } catch (err: any) {
      console.error('[Database] Save location error:', err.message);
      setError(err.message);
      return null;
    } finally {
      setLoading(false);
    }
  }, [user]);

  /**
   * Update a saved location
   */
  const updateLocation = useCallback(async (
    id: string,
    updates: Partial<SavedLocation>
  ): Promise<boolean> => {
    if (!user) {
      setError('User not authenticated');
      return false;
    }

    setLoading(true);
    setError(null);

    try {
      const supabase = getSupabase();
      
      const { error: updateError } = await supabase
        .from('saved_locations')
        .update({
          ...updates,
          updated_at: new Date().toISOString(),
        })
        .eq('id', id)
        .eq('user_id', user.id);

      if (updateError) {
        throw new Error(updateError.message);
      }

      return true;
    } catch (err: any) {
      console.error('[Database] Update location error:', err.message);
      setError(err.message);
      return false;
    } finally {
      setLoading(false);
    }
  }, [user]);

  /**
   * Delete a saved location
   */
  const deleteLocation = useCallback(async (id: string): Promise<boolean> => {
    if (!user) {
      setError('User not authenticated');
      return false;
    }

    setLoading(true);
    setError(null);

    try {
      const supabase = getSupabase();
      
      const { error: deleteError } = await supabase
        .from('saved_locations')
        .delete()
        .eq('id', id)
        .eq('user_id', user.id);

      if (deleteError) {
        throw new Error(deleteError.message);
      }

      return true;
    } catch (err: any) {
      console.error('[Database] Delete location error:', err.message);
      setError(err.message);
      return false;
    } finally {
      setLoading(false);
    }
  }, [user]);

  // ============================================
  // ROUTE HISTORY OPERATIONS
  // ============================================

  /**
   * Get route history
   */
  const getRouteHistory = useCallback(async (
    options?: { status?: string; limit?: number }
  ): Promise<RouteHistory[]> => {
    if (!user) return [];

    setLoading(true);
    setError(null);

    try {
      const supabase = getSupabase();
      
      let query = supabase
        .from('route_history')
        .select('*')
        .eq('user_id', user.id)
        .order('created_at', { ascending: false });

      if (options?.status) {
        query = query.eq('status', options.status);
      }

      if (options?.limit) {
        query = query.limit(options.limit);
      }

      const { data, error: fetchError } = await query;

      if (fetchError) {
        throw new Error(fetchError.message);
      }

      return (data || []) as RouteHistory[];
    } catch (err: any) {
      console.error('[Database] Get route history error:', err.message);
      setError(err.message);
      return [];
    } finally {
      setLoading(false);
    }
  }, [user]);

  /**
   * Save a route to history
   */
  const saveRouteToHistory = useCallback(async (
    route: Omit<RouteHistory, 'id' | 'user_id' | 'created_at' | 'updated_at'>
  ): Promise<RouteHistory | null> => {
    if (!user) {
      setError('User not authenticated');
      return null;
    }

    setLoading(true);
    setError(null);

    try {
      const supabase = getSupabase();
      
      const { data, error: insertError } = await supabase
        .from('route_history')
        .insert({
          user_id: user.id,
          ...route,
        })
        .select()
        .single();

      if (insertError) {
        throw new Error(insertError.message);
      }

      console.log('[Database] Route saved to history:', data?.route_name);
      return data as RouteHistory;
    } catch (err: any) {
      console.error('[Database] Save route error:', err.message);
      setError(err.message);
      return null;
    } finally {
      setLoading(false);
    }
  }, [user]);

  /**
   * Update route status
   */
  const updateRouteStatus = useCallback(async (
    id: string,
    status: 'planned' | 'in_progress' | 'completed' | 'cancelled'
  ): Promise<boolean> => {
    if (!user) {
      setError('User not authenticated');
      return false;
    }

    setLoading(true);
    setError(null);

    try {
      const supabase = getSupabase();
      
      const updates: any = {
        status,
        updated_at: new Date().toISOString(),
      };

      if (status === 'in_progress') {
        updates.started_at = new Date().toISOString();
      } else if (status === 'completed') {
        updates.completed_at = new Date().toISOString();
      }

      const { error: updateError } = await supabase
        .from('route_history')
        .update(updates)
        .eq('id', id)
        .eq('user_id', user.id);

      if (updateError) {
        throw new Error(updateError.message);
      }

      return true;
    } catch (err: any) {
      console.error('[Database] Update route status error:', err.message);
      setError(err.message);
      return false;
    } finally {
      setLoading(false);
    }
  }, [user]);

  /**
   * Delete a route from history
   */
  const deleteRoute = useCallback(async (id: string): Promise<boolean> => {
    if (!user) {
      setError('User not authenticated');
      return false;
    }

    setLoading(true);
    setError(null);

    try {
      const supabase = getSupabase();
      
      const { error: deleteError } = await supabase
        .from('route_history')
        .delete()
        .eq('id', id)
        .eq('user_id', user.id);

      if (deleteError) {
        throw new Error(deleteError.message);
      }

      return true;
    } catch (err: any) {
      console.error('[Database] Delete route error:', err.message);
      setError(err.message);
      return false;
    } finally {
      setLoading(false);
    }
  }, [user]);

  // ============================================
  // ROUTE TEMPLATES OPERATIONS
  // ============================================

  /**
   * Get route templates
   */
  const getRouteTemplates = useCallback(async (): Promise<RouteTemplate[]> => {
    if (!user) return [];

    setLoading(true);
    setError(null);

    try {
      const supabase = getSupabase();
      
      const { data, error: fetchError } = await supabase
        .from('route_templates')
        .select('*')
        .eq('user_id', user.id)
        .order('use_count', { ascending: false });

      if (fetchError) {
        throw new Error(fetchError.message);
      }

      return (data || []) as RouteTemplate[];
    } catch (err: any) {
      console.error('[Database] Get templates error:', err.message);
      setError(err.message);
      return [];
    } finally {
      setLoading(false);
    }
  }, [user]);

  /**
   * Save a route template
   */
  const saveRouteTemplate = useCallback(async (
    template: Omit<RouteTemplate, 'id' | 'user_id' | 'created_at' | 'updated_at'>
  ): Promise<RouteTemplate | null> => {
    if (!user) {
      setError('User not authenticated');
      return null;
    }

    setLoading(true);
    setError(null);

    try {
      const supabase = getSupabase();
      
      const { data, error: insertError } = await supabase
        .from('route_templates')
        .insert({
          user_id: user.id,
          ...template,
        })
        .select()
        .single();

      if (insertError) {
        throw new Error(insertError.message);
      }

      console.log('[Database] Template saved:', data?.name);
      return data as RouteTemplate;
    } catch (err: any) {
      console.error('[Database] Save template error:', err.message);
      setError(err.message);
      return null;
    } finally {
      setLoading(false);
    }
  }, [user]);

  /**
   * Delete a route template
   */
  const deleteRouteTemplate = useCallback(async (id: string): Promise<boolean> => {
    if (!user) {
      setError('User not authenticated');
      return false;
    }

    setLoading(true);
    setError(null);

    try {
      const supabase = getSupabase();
      
      const { error: deleteError } = await supabase
        .from('route_templates')
        .delete()
        .eq('id', id)
        .eq('user_id', user.id);

      if (deleteError) {
        throw new Error(deleteError.message);
      }

      return true;
    } catch (err: any) {
      console.error('[Database] Delete template error:', err.message);
      setError(err.message);
      return false;
    } finally {
      setLoading(false);
    }
  }, [user]);

  // ============================================
  // IMPORT HISTORY OPERATIONS
  // ============================================

  /**
   * Create an import record
   */
  const createImportRecord = useCallback(async (
    importData: Omit<ImportHistory, 'id' | 'user_id' | 'created_at'>
  ): Promise<ImportHistory | null> => {
    if (!user) {
      setError('User not authenticated');
      return null;
    }

    setLoading(true);
    setError(null);

    try {
      const supabase = getSupabase();
      
      const { data, error: insertError } = await supabase
        .from('import_history')
        .insert({
          user_id: user.id,
          ...importData,
        })
        .select()
        .single();

      if (insertError) {
        throw new Error(insertError.message);
      }

      console.log('[Database] Import record created:', data?.file_name);
      return data as ImportHistory;
    } catch (err: any) {
      console.error('[Database] Create import error:', err.message);
      setError(err.message);
      return null;
    } finally {
      setLoading(false);
    }
  }, [user]);

  /**
   * Update import record status
   */
  const updateImportStatus = useCallback(async (
    id: string,
    updates: Partial<ImportHistory>
  ): Promise<boolean> => {
    if (!user) {
      setError('User not authenticated');
      return false;
    }

    setLoading(true);
    setError(null);

    try {
      const supabase = getSupabase();
      
      const { error: updateError } = await supabase
        .from('import_history')
        .update(updates)
        .eq('id', id)
        .eq('user_id', user.id);

      if (updateError) {
        throw new Error(updateError.message);
      }

      return true;
    } catch (err: any) {
      console.error('[Database] Update import error:', err.message);
      setError(err.message);
      return false;
    } finally {
      setLoading(false);
    }
  }, [user]);

  /**
   * Get import history
   */
  const getImportHistory = useCallback(async (
    limit: number = 20
  ): Promise<ImportHistory[]> => {
    if (!user) return [];

    setLoading(true);
    setError(null);

    try {
      const supabase = getSupabase();
      
      const { data, error: fetchError } = await supabase
        .from('import_history')
        .select('*')
        .eq('user_id', user.id)
        .order('created_at', { ascending: false })
        .limit(limit);

      if (fetchError) {
        throw new Error(fetchError.message);
      }

      return (data || []) as ImportHistory[];
    } catch (err: any) {
      console.error('[Database] Get import history error:', err.message);
      setError(err.message);
      return [];
    } finally {
      setLoading(false);
    }
  }, [user]);

  // ============================================
  // GENERIC HELPERS
  // ============================================

  /**
   * Generic query helper for custom tables
   */
  const query = useCallback(async <T>(
    table: string,
    options?: {
      select?: string;
      filters?: Record<string, any>;
      order?: { column: string; ascending?: boolean };
      limit?: number;
    }
  ): Promise<T[]> => {
    setLoading(true);
    setError(null);

    try {
      const supabase = getSupabase();
      
      let queryBuilder = supabase
        .from(table)
        .select(options?.select || '*');

      // Apply filters
      if (options?.filters) {
        Object.entries(options.filters).forEach(([key, value]) => {
          queryBuilder = queryBuilder.eq(key, value);
        });
      }

      // Apply ordering
      if (options?.order) {
        queryBuilder = queryBuilder.order(
          options.order.column,
          { ascending: options.order.ascending ?? true }
        );
      }

      // Apply limit
      if (options?.limit) {
        queryBuilder = queryBuilder.limit(options.limit);
      }

      const { data, error: queryError } = await queryBuilder;

      if (queryError) {
        throw new Error(queryError.message);
      }

      return (data || []) as T[];
    } catch (err: any) {
      const errorMessage = err.message || 'Query failed';
      console.error('[Database] Query error:', errorMessage);
      setError(errorMessage);
      return [];
    } finally {
      setLoading(false);
    }
  }, []);

  return {
    // Profile operations
    getProfile,
    updateProfile,
    
    // Settings operations
    getSettings,
    updateSettings,
    
    // Saved locations
    getSavedLocations,
    saveLocation,
    updateLocation,
    deleteLocation,
    
    // Route history
    getRouteHistory,
    saveRouteToHistory,
    updateRouteStatus,
    deleteRoute,
    
    // Route templates
    getRouteTemplates,
    saveRouteTemplate,
    deleteRouteTemplate,
    
    // Import history
    createImportRecord,
    updateImportStatus,
    getImportHistory,
    
    // Generic helpers
    query,
    
    // State
    loading,
    error,
    clearError: () => setError(null),
  };
}
