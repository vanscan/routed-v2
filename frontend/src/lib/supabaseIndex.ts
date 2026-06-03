/**
 * Supabase Integration - Centralized Exports
 * 
 * This file provides a clean API for all Supabase-related functionality.
 * 
 * IMPORTANT: All Supabase operations are SSR-safe. The client is lazily
 * initialized only on the client side to prevent "window is not defined" errors.
 * 
 * Usage Examples:
 * 
 * 1. Using the Supabase Context for Auth:
 * ```tsx
 * import { useSupabase } from '@/src/contexts/SupabaseContext';
 * 
 * function MyComponent() {
 *   const { user, signIn, signOut, loading } = useSupabase();
 *   
 *   if (loading) return <LoadingSpinner />;
 *   if (!user) return <LoginScreen />;
 *   return <Dashboard user={user} />;
 * }
 * ```
 * 
 * 2. Using Unified Auth (bridges existing auth with Supabase):
 * ```tsx
 * import { useUnifiedAuth } from '@/src/hooks/useUnifiedAuth';
 * 
 * function MyComponent() {
 *   const { user, isAuthenticated, primaryAuth, supabaseAuth } = useUnifiedAuth();
 *   
 *   // Use primaryAuth for existing Google/Email login
 *   // Use supabaseAuth for magic link, password reset, etc.
 * }
 * ```
 * 
 * 3. Database Operations:
 * ```tsx
 * import { queryTable, insertIntoTable, subscribeToTable } from '@/src/utils/supabaseDatabase';
 * 
 * // Query data
 * const { data, error } = await queryTable('users', {
 *   filter: { status: 'active' },
 *   order: { column: 'created_at', ascending: false },
 *   limit: 10
 * });
 * 
 * // Insert data
 * const { data: newUser, error } = await insertIntoTable('users', {
 *   name: 'John',
 *   email: 'john@example.com'
 * });
 * 
 * // Subscribe to changes
 * const unsubscribe = await subscribeToTable('messages', (payload) => {
 *   console.log('New message:', payload.new);
 * });
 * ```
 * 
 * 4. Storage Operations:
 * ```tsx
 * import { uploadFile, downloadFile, getPublicUrl } from '@/src/utils/supabaseStorage';
 * 
 * // Upload a file
 * const { data, error } = await uploadFile('user-files', 'avatar.png', file);
 * 
 * // Get public URL
 * const url = await getPublicUrl('user-files', 'avatar.png');
 * 
 * // Download a file
 * const { data: blob, error } = await downloadFile('user-files', 'document.pdf');
 * ```
 */

// Context and Hooks
export { SupabaseProvider, useSupabase, getSupabaseClient } from '../contexts/SupabaseContext';
export type { SupabaseUser, SupabaseSession, SupabaseContextType, AuthError } from '../contexts/SupabaseContext';

export { useUnifiedAuth } from '../hooks/useUnifiedAuth';
export type { UnifiedUser, UnifiedAuthState } from '../hooks/useUnifiedAuth';

// Database utilities
export {
  queryTable,
  getById,
  insertIntoTable,
  upsertIntoTable,
  updateTable,
  deleteFromTable,
  subscribeToTable,
  callRpc,
  countRecords,
} from '../utils/supabaseDatabase';
export type { QueryOptions, DatabaseResult } from '../utils/supabaseDatabase';

// Storage utilities
export {
  uploadFile,
  uploadFileFromUri,
  uploadBase64,
  downloadFile,
  getPublicUrl,
  getSignedUrl,
  deleteFile,
  listFiles,
  moveFile,
  copyFile,
  generateFilePath,
} from '../utils/supabaseStorage';
export type { StorageBucket, StorageResult, UploadOptions, FileObject } from '../utils/supabaseStorage';

// Raw client access (use with caution - prefer context/utilities)
export { supabase, getSupabase } from './supabase';
export type { User as SupabaseUserType, Session as SupabaseSessionType } from './supabase';
