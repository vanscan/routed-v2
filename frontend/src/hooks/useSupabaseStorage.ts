// Supabase Storage hook for file uploads
import { useState, useCallback } from 'react';
import { Platform } from 'react-native';
import { getSupabase } from '../lib/supabase';
import { useSupabase } from '../contexts/SupabaseContext';

export interface UploadProgress {
  loaded: number;
  total: number;
  percentage: number;
}

export interface UploadResult {
  success: boolean;
  path?: string;
  publicUrl?: string;
  error?: string;
}

export interface FileInfo {
  name: string;
  size: number;
  type: string;
  uri: string;
}

// Default bucket for route-related files
const DEFAULT_BUCKET = 'route-files';

export function useSupabaseStorage(bucket: string = DEFAULT_BUCKET) {
  const { user } = useSupabase();
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState<UploadProgress | null>(null);
  const [error, setError] = useState<string | null>(null);

  /**
   * Upload a file to Supabase Storage
   * Files are organized by user: {bucket}/{userId}/{filename}
   */
  const uploadFile = useCallback(async (
    file: FileInfo | File | Blob,
    options?: {
      folder?: string;
      fileName?: string;
      contentType?: string;
      upsert?: boolean;
    }
  ): Promise<UploadResult> => {
    if (!user) {
      return { success: false, error: 'User not authenticated' };
    }

    setUploading(true);
    setError(null);
    setProgress({ loaded: 0, total: 0, percentage: 0 });

    try {
      const supabase = getSupabase();
      
      // Determine file name and path
      let fileName: string;
      let fileData: Blob | File;
      let contentType: string;

      if ('uri' in file) {
        // React Native file object
        fileName = options?.fileName || file.name || `file_${Date.now()}`;
        contentType = options?.contentType || file.type || 'application/octet-stream';
        
        // Fetch the file from URI and convert to blob
        const response = await fetch(file.uri);
        fileData = await response.blob();
      } else {
        // Web File/Blob object
        fileName = options?.fileName || (file instanceof File ? file.name : `file_${Date.now()}`);
        contentType = options?.contentType || file.type || 'application/octet-stream';
        fileData = file;
      }

      // Build the storage path: userId/folder/filename
      const folder = options?.folder || 'uploads';
      const filePath = `${user.id}/${folder}/${fileName}`;

      console.log('[Storage] Uploading file:', { bucket, filePath, contentType, size: fileData.size });

      // Upload to Supabase Storage
      const { data, error: uploadError } = await supabase.storage
        .from(bucket)
        .upload(filePath, fileData, {
          contentType,
          upsert: options?.upsert ?? false,
        });

      if (uploadError) {
        console.error('[Storage] Upload error:', uploadError);
        throw new Error(uploadError.message);
      }

      // Get the public URL
      const { data: urlData } = supabase.storage
        .from(bucket)
        .getPublicUrl(data.path);

      setProgress({ loaded: fileData.size, total: fileData.size, percentage: 100 });

      console.log('[Storage] Upload successful:', data.path);

      return {
        success: true,
        path: data.path,
        publicUrl: urlData.publicUrl,
      };
    } catch (err: any) {
      const errorMessage = err.message || 'Upload failed';
      console.error('[Storage] Upload failed:', errorMessage);
      setError(errorMessage);
      return { success: false, error: errorMessage };
    } finally {
      setUploading(false);
    }
  }, [user, bucket]);

  /**
   * Download a file from Supabase Storage
   */
  const downloadFile = useCallback(async (filePath: string): Promise<Blob | null> => {
    try {
      const supabase = getSupabase();
      
      const { data, error: downloadError } = await supabase.storage
        .from(bucket)
        .download(filePath);

      if (downloadError) {
        console.error('[Storage] Download error:', downloadError);
        setError(downloadError.message);
        return null;
      }

      return data;
    } catch (err: any) {
      console.error('[Storage] Download failed:', err);
      setError(err.message || 'Download failed');
      return null;
    }
  }, [bucket]);

  /**
   * Delete a file from Supabase Storage
   */
  const deleteFile = useCallback(async (filePath: string): Promise<boolean> => {
    try {
      const supabase = getSupabase();
      
      const { error: deleteError } = await supabase.storage
        .from(bucket)
        .remove([filePath]);

      if (deleteError) {
        console.error('[Storage] Delete error:', deleteError);
        setError(deleteError.message);
        return false;
      }

      return true;
    } catch (err: any) {
      console.error('[Storage] Delete failed:', err);
      setError(err.message || 'Delete failed');
      return false;
    }
  }, [bucket]);

  /**
   * List files in a folder
   */
  const listFiles = useCallback(async (folder?: string): Promise<any[]> => {
    if (!user) return [];
    
    try {
      const supabase = getSupabase();
      const path = folder ? `${user.id}/${folder}` : user.id;
      
      const { data, error: listError } = await supabase.storage
        .from(bucket)
        .list(path);

      if (listError) {
        console.error('[Storage] List error:', listError);
        setError(listError.message);
        return [];
      }

      return data || [];
    } catch (err: any) {
      console.error('[Storage] List failed:', err);
      setError(err.message || 'List failed');
      return [];
    }
  }, [user, bucket]);

  /**
   * Get a signed URL for temporary access (useful for private files)
   */
  const getSignedUrl = useCallback(async (
    filePath: string,
    expiresIn: number = 3600
  ): Promise<string | null> => {
    try {
      const supabase = getSupabase();
      
      const { data, error: signError } = await supabase.storage
        .from(bucket)
        .createSignedUrl(filePath, expiresIn);

      if (signError) {
        console.error('[Storage] Signed URL error:', signError);
        setError(signError.message);
        return null;
      }

      return data.signedUrl;
    } catch (err: any) {
      console.error('[Storage] Signed URL failed:', err);
      setError(err.message || 'Failed to create signed URL');
      return null;
    }
  }, [bucket]);

  return {
    uploadFile,
    downloadFile,
    deleteFile,
    listFiles,
    getSignedUrl,
    uploading,
    progress,
    error,
    clearError: () => setError(null),
  };
}
