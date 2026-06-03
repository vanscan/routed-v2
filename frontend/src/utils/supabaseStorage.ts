// Enhanced Supabase Storage helpers for file operations
import { Platform } from 'react-native';

// Lazy import to avoid SSR issues
const getSupabase = async () => {
  if (Platform.OS === 'web' && typeof window === 'undefined') {
    throw new Error('Supabase cannot be used during SSR');
  }
  const { supabase } = await import('../lib/supabase');
  return supabase;
};

export type StorageBucket = 'imports' | 'exports' | 'user-files' | 'avatars' | 'documents';

export interface StorageResult<T> {
  data: T | null;
  error: Error | null;
}

export interface UploadOptions {
  contentType?: string;
  upsert?: boolean;
  cacheControl?: string;
}

export interface FileObject {
  name: string;
  id?: string;
  bucket_id?: string;
  created_at?: string;
  updated_at?: string;
  last_accessed_at?: string;
  metadata?: Record<string, unknown>;
}

/**
 * Upload a file to Supabase Storage
 */
export async function uploadFile(
  bucket: StorageBucket,
  path: string,
  file: File | Blob | ArrayBuffer | Uint8Array,
  options?: UploadOptions
): Promise<StorageResult<{ path: string }>> {
  try {
    const supabase = await getSupabase();
    const { data, error } = await supabase.storage
      .from(bucket)
      .upload(path, file, {
        contentType: options?.contentType,
        upsert: options?.upsert ?? false,
        cacheControl: options?.cacheControl || '3600',
      });

    if (error) {
      return { data: null, error: new Error(error.message) };
    }
    return { data: { path: data.path }, error: null };
  } catch (error) {
    return { data: null, error: error as Error };
  }
}

/**
 * Upload a file from a URI (React Native specific)
 */
export async function uploadFileFromUri(
  bucket: StorageBucket,
  path: string,
  uri: string,
  options?: UploadOptions & { fileName?: string }
): Promise<StorageResult<{ path: string }>> {
  try {
    // Fetch the file from URI
    const response = await fetch(uri);
    const blob = await response.blob();
    
    return uploadFile(bucket, path, blob, options);
  } catch (error) {
    return { data: null, error: error as Error };
  }
}

/**
 * Upload a base64 encoded file
 */
export async function uploadBase64(
  bucket: StorageBucket,
  path: string,
  base64: string,
  contentType: string
): Promise<StorageResult<{ path: string }>> {
  try {
    // Remove data URL prefix if present
    const base64Data = base64.replace(/^data:[^;]+;base64,/, '');
    
    // Convert base64 to Uint8Array
    const binaryString = atob(base64Data);
    const bytes = new Uint8Array(binaryString.length);
    for (let i = 0; i < binaryString.length; i++) {
      bytes[i] = binaryString.charCodeAt(i);
    }
    
    return uploadFile(bucket, path, bytes, { contentType });
  } catch (error) {
    return { data: null, error: error as Error };
  }
}

/**
 * Download a file from Supabase Storage
 */
export async function downloadFile(
  bucket: StorageBucket,
  path: string
): Promise<StorageResult<Blob>> {
  try {
    const supabase = await getSupabase();
    const { data, error } = await supabase.storage
      .from(bucket)
      .download(path);

    if (error) {
      return { data: null, error: new Error(error.message) };
    }
    return { data, error: null };
  } catch (error) {
    return { data: null, error: error as Error };
  }
}

/**
 * Get a public URL for a file
 */
export async function getPublicUrl(
  bucket: StorageBucket,
  path: string
): Promise<string> {
  const supabase = await getSupabase();
  const { data } = supabase.storage
    .from(bucket)
    .getPublicUrl(path);

  return data.publicUrl;
}

/**
 * Get a signed (temporary) URL for a private file
 */
export async function getSignedUrl(
  bucket: StorageBucket,
  path: string,
  expiresIn: number = 3600 // 1 hour default
): Promise<StorageResult<string>> {
  try {
    const supabase = await getSupabase();
    const { data, error } = await supabase.storage
      .from(bucket)
      .createSignedUrl(path, expiresIn);

    if (error) {
      return { data: null, error: new Error(error.message) };
    }
    return { data: data.signedUrl, error: null };
  } catch (error) {
    return { data: null, error: error as Error };
  }
}

/**
 * Delete a file from Supabase Storage
 */
export async function deleteFile(
  bucket: StorageBucket,
  path: string | string[]
): Promise<{ error: Error | null }> {
  try {
    const supabase = await getSupabase();
    const paths = Array.isArray(path) ? path : [path];
    const { error } = await supabase.storage
      .from(bucket)
      .remove(paths);

    if (error) {
      return { error: new Error(error.message) };
    }
    return { error: null };
  } catch (error) {
    return { error: error as Error };
  }
}

/**
 * List files in a bucket/folder
 */
export async function listFiles(
  bucket: StorageBucket,
  folder?: string,
  options?: {
    limit?: number;
    offset?: number;
    sortBy?: { column: string; order: 'asc' | 'desc' };
  }
): Promise<StorageResult<FileObject[]>> {
  try {
    const supabase = await getSupabase();
    const { data, error } = await supabase.storage
      .from(bucket)
      .list(folder, {
        limit: options?.limit || 100,
        offset: options?.offset || 0,
        sortBy: options?.sortBy || { column: 'name', order: 'asc' },
      });

    if (error) {
      return { data: null, error: new Error(error.message) };
    }
    return { data: data as FileObject[], error: null };
  } catch (error) {
    return { data: null, error: error as Error };
  }
}

/**
 * Move/rename a file
 */
export async function moveFile(
  bucket: StorageBucket,
  fromPath: string,
  toPath: string
): Promise<{ error: Error | null }> {
  try {
    const supabase = await getSupabase();
    const { error } = await supabase.storage
      .from(bucket)
      .move(fromPath, toPath);

    if (error) {
      return { error: new Error(error.message) };
    }
    return { error: null };
  } catch (error) {
    return { error: error as Error };
  }
}

/**
 * Copy a file
 */
export async function copyFile(
  bucket: StorageBucket,
  fromPath: string,
  toPath: string
): Promise<{ error: Error | null }> {
  try {
    const supabase = await getSupabase();
    const { error } = await supabase.storage
      .from(bucket)
      .copy(fromPath, toPath);

    if (error) {
      return { error: new Error(error.message) };
    }
    return { error: null };
  } catch (error) {
    return { error: error as Error };
  }
}

/**
 * Generate a unique file path with timestamp
 */
export function generateFilePath(
  userId: string,
  fileName: string,
  folder?: string
): string {
  const timestamp = Date.now();
  const sanitizedName = fileName.replace(/[^a-zA-Z0-9.-]/g, '_');
  const basePath = folder ? `${folder}/${userId}` : userId;
  return `${basePath}/${timestamp}_${sanitizedName}`;
}
