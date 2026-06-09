/**
 * Supabase Storage Integration for Routr
 * 
 * Features:
 * - Proof photo uploads (delivery confirmation images)
 * - Route exports/imports
 * - User profile images
 * - Signature capture storage
 */

import { supabase, getSupabase } from './supabase';
import * as FileSystem from 'expo-file-system';
import { Platform } from 'react-native';

// Storage bucket names
export const BUCKETS = {
  PROOF_PHOTOS: 'proof-photos',
  SIGNATURES: 'signatures',
  EXPORTS: 'route-exports',
  PROFILES: 'profile-images',
} as const;

export type BucketName = typeof BUCKETS[keyof typeof BUCKETS];

// File size limits (in bytes)
const MAX_IMAGE_SIZE = 5 * 1024 * 1024; // 5MB
const MAX_EXPORT_SIZE = 10 * 1024 * 1024; // 10MB

/**
 * Initialize storage buckets (call once on app startup if needed)
 * Note: Buckets should be created in Supabase Dashboard for production
 */
export const initializeStorageBuckets = async (): Promise<void> => {
  const client = getSupabase();
  
  for (const bucket of Object.values(BUCKETS)) {
    try {
      const { data, error } = await client.storage.getBucket(bucket);
      if (error && error.message.includes('not found')) {
        // Bucket doesn't exist - would need admin key to create
        console.log(`[Storage] Bucket "${bucket}" not found - create in Supabase Dashboard`);
      }
    } catch (e) {
      console.warn(`[Storage] Error checking bucket ${bucket}:`, e);
    }
  }
};

/**
 * Upload a proof photo for a delivery
 */
export const uploadProofPhoto = async (
  stopId: string,
  imageUri: string,
  userId: string
): Promise<{ url: string | null; error: string | null }> => {
  try {
    const client = getSupabase();
    const timestamp = Date.now();
    const fileName = `${userId}/${stopId}_${timestamp}.jpg`;
    
    // Read the file as base64
    let base64Data: string;
    
    if (Platform.OS === 'web') {
      // Web: fetch the blob and convert
      const response = await fetch(imageUri);
      const blob = await response.blob();
      base64Data = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onloadend = () => {
          const result = reader.result as string;
          resolve(result.split(',')[1]); // Remove data:image/jpeg;base64, prefix
        };
        reader.onerror = reject;
        reader.readAsDataURL(blob);
      });
    } else {
      // Native: use FileSystem
      base64Data = await FileSystem.readAsStringAsync(imageUri, {
        encoding: FileSystem.EncodingType.Base64,
      });
    }
    
    // Check file size
    const fileSize = (base64Data.length * 3) / 4; // Approximate decoded size
    if (fileSize > MAX_IMAGE_SIZE) {
      return { url: null, error: 'Image too large. Max size is 5MB.' };
    }
    
    // Convert base64 to ArrayBuffer
    const binaryString = atob(base64Data);
    const bytes = new Uint8Array(binaryString.length);
    for (let i = 0; i < binaryString.length; i++) {
      bytes[i] = binaryString.charCodeAt(i);
    }
    
    // Upload to Supabase Storage
    const { data, error } = await client.storage
      .from(BUCKETS.PROOF_PHOTOS)
      .upload(fileName, bytes.buffer, {
        contentType: 'image/jpeg',
        upsert: true,
      });
    
    if (error) {
      console.error('[Storage] Upload error:', error);
      return { url: null, error: error.message };
    }
    
    // Get public URL
    const { data: urlData } = client.storage
      .from(BUCKETS.PROOF_PHOTOS)
      .getPublicUrl(fileName);
    
    console.log('[Storage] Proof photo uploaded:', urlData.publicUrl);
    return { url: urlData.publicUrl, error: null };
  } catch (e: any) {
    console.error('[Storage] Upload exception:', e);
    return { url: null, error: e.message || 'Upload failed' };
  }
};

/**
 * Upload a signature image
 */
export const uploadSignature = async (
  stopId: string,
  signatureBase64: string,
  userId: string
): Promise<{ url: string | null; error: string | null }> => {
  try {
    const client = getSupabase();
    const timestamp = Date.now();
    const fileName = `${userId}/${stopId}_sig_${timestamp}.png`;
    
    // Remove data URL prefix if present
    const base64Data = signatureBase64.replace(/^data:image\/\w+;base64,/, '');
    
    // Convert base64 to ArrayBuffer
    const binaryString = atob(base64Data);
    const bytes = new Uint8Array(binaryString.length);
    for (let i = 0; i < binaryString.length; i++) {
      bytes[i] = binaryString.charCodeAt(i);
    }
    
    const { data, error } = await client.storage
      .from(BUCKETS.SIGNATURES)
      .upload(fileName, bytes.buffer, {
        contentType: 'image/png',
        upsert: true,
      });
    
    if (error) {
      return { url: null, error: error.message };
    }
    
    const { data: urlData } = client.storage
      .from(BUCKETS.SIGNATURES)
      .getPublicUrl(fileName);
    
    return { url: urlData.publicUrl, error: null };
  } catch (e: any) {
    return { url: null, error: e.message || 'Signature upload failed' };
  }
};

/**
 * Upload a route export file (JSON/CSV)
 */
export const uploadRouteExport = async (
  fileName: string,
  content: string,
  userId: string,
  contentType: 'application/json' | 'text/csv' = 'application/json'
): Promise<{ url: string | null; error: string | null }> => {
  try {
    const client = getSupabase();
    const timestamp = Date.now();
    const fullPath = `${userId}/${timestamp}_${fileName}`;
    
    const encoder = new TextEncoder();
    const data = encoder.encode(content);
    
    if (data.length > MAX_EXPORT_SIZE) {
      return { url: null, error: 'Export file too large. Max size is 10MB.' };
    }
    
    const { error } = await client.storage
      .from(BUCKETS.EXPORTS)
      .upload(fullPath, data.buffer, {
        contentType,
        upsert: true,
      });
    
    if (error) {
      return { url: null, error: error.message };
    }
    
    const { data: urlData } = client.storage
      .from(BUCKETS.EXPORTS)
      .getPublicUrl(fullPath);
    
    return { url: urlData.publicUrl, error: null };
  } catch (e: any) {
    return { url: null, error: e.message || 'Export upload failed' };
  }
};

/**
 * Upload a profile image
 */
export const uploadProfileImage = async (
  imageUri: string,
  userId: string
): Promise<{ url: string | null; error: string | null }> => {
  try {
    const client = getSupabase();
    const fileName = `${userId}/avatar.jpg`;
    
    let base64Data: string;
    
    if (Platform.OS === 'web') {
      const response = await fetch(imageUri);
      const blob = await response.blob();
      base64Data = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onloadend = () => {
          const result = reader.result as string;
          resolve(result.split(',')[1]);
        };
        reader.onerror = reject;
        reader.readAsDataURL(blob);
      });
    } else {
      base64Data = await FileSystem.readAsStringAsync(imageUri, {
        encoding: FileSystem.EncodingType.Base64,
      });
    }
    
    const binaryString = atob(base64Data);
    const bytes = new Uint8Array(binaryString.length);
    for (let i = 0; i < binaryString.length; i++) {
      bytes[i] = binaryString.charCodeAt(i);
    }
    
    const { error } = await client.storage
      .from(BUCKETS.PROFILES)
      .upload(fileName, bytes.buffer, {
        contentType: 'image/jpeg',
        upsert: true, // Overwrite existing avatar
      });
    
    if (error) {
      return { url: null, error: error.message };
    }
    
    // Add cache buster to URL
    const { data: urlData } = client.storage
      .from(BUCKETS.PROFILES)
      .getPublicUrl(fileName);
    
    return { url: `${urlData.publicUrl}?t=${Date.now()}`, error: null };
  } catch (e: any) {
    return { url: null, error: e.message || 'Profile image upload failed' };
  }
};

/**
 * Delete a file from storage
 */
export const deleteFile = async (
  bucket: BucketName,
  filePath: string
): Promise<{ success: boolean; error: string | null }> => {
  try {
    const client = getSupabase();
    const { error } = await client.storage.from(bucket).remove([filePath]);
    
    if (error) {
      return { success: false, error: error.message };
    }
    
    return { success: true, error: null };
  } catch (e: any) {
    return { success: false, error: e.message || 'Delete failed' };
  }
};

/**
 * List files in a user's folder
 */
export const listUserFiles = async (
  bucket: BucketName,
  userId: string
): Promise<{ files: string[]; error: string | null }> => {
  try {
    const client = getSupabase();
    const { data, error } = await client.storage
      .from(bucket)
      .list(userId, { limit: 100, sortBy: { column: 'created_at', order: 'desc' } });
    
    if (error) {
      return { files: [], error: error.message };
    }
    
    const files = (data || []).map(f => `${userId}/${f.name}`);
    return { files, error: null };
  } catch (e: any) {
    return { files: [], error: e.message || 'List failed' };
  }
};

/**
 * Get a signed URL for private file access (expires in 1 hour)
 */
export const getSignedUrl = async (
  bucket: BucketName,
  filePath: string,
  expiresIn: number = 3600
): Promise<{ url: string | null; error: string | null }> => {
  try {
    const client = getSupabase();
    const { data, error } = await client.storage
      .from(bucket)
      .createSignedUrl(filePath, expiresIn);
    
    if (error) {
      return { url: null, error: error.message };
    }
    
    return { url: data.signedUrl, error: null };
  } catch (e: any) {
    return { url: null, error: e.message || 'Failed to get signed URL' };
  }
};

export default {
  BUCKETS,
  uploadProofPhoto,
  uploadSignature,
  uploadRouteExport,
  uploadProfileImage,
  deleteFile,
  listUserFiles,
  getSignedUrl,
  initializeStorageBuckets,
};
