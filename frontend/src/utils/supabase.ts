// Supabase exports - single entry point for all Supabase functionality
export { supabase, type User, type Session } from '../lib/supabase';
export { SupabaseProvider, useSupabase } from '../contexts/SupabaseContext';
export * from './supabaseStorage';
export * from './supabaseDatabase';
