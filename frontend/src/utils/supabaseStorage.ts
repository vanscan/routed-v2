// Supabase Storage helpers for file operations
import { supabase } from '../lib/supabase';

export type StorageBucket = 'imports' | 'exports' | 'user-files';

/**
 * Upload a file to Supabase Storage
 */
export async function uploadFile(
  bucket: StorageBucket,
  path: string,
  file: File | Blob,
  options?: { contentType?: string; upsert?: boolean }
) {
  const { data, error } = await supabase.storage
    .from(bucket)
    .upload(path, file, {
      contentType: options?.contentType,
      upsert: options?.upsert ?? false,
    });

  if (error) throw error;
  return data;
}

/**
 * Download a file from Supabase Storage
 */
export async function downloadFile(bucket: StorageBucket, path: string) {
  const { data, error } = await supabase.storage
    .from(bucket)
    .download(path);

  if (error) throw error;
  return data;
}

/**
 * Get a public URL for a file
 */
export function getPublicUrl(bucket: StorageBucket, path: string) {
  const { data } = supabase.storage
    .from(bucket)
    .getPublicUrl(path);

  return data.publicUrl;
}

/**
 * Delete a file from Supabase Storage
 */
export async function deleteFile(bucket: StorageBucket, path: string) {
  const { error } = await supabase.storage
    .from(bucket)
    .remove([path]);

  if (error) throw error;
}

/**
 * List files in a bucket/folder
 */
export async function listFiles(bucket: StorageBucket, folder?: string) {
  const { data, error } = await supabase.storage
    .from(bucket)
    .list(folder);

  if (error) throw error;
  return data;
}
