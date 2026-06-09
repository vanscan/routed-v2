-- ============================================
-- Routr Supabase Storage Buckets
-- Run this in Supabase Dashboard > SQL Editor
-- Requires service_role access
-- ============================================

-- Create storage buckets for Routr
-- Note: You may need to create these via Dashboard UI instead

-- Proof photos bucket (public - for delivery confirmation images)
INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES (
    'proof-photos',
    'proof-photos',
    true,
    5242880, -- 5MB limit
    ARRAY['image/jpeg', 'image/png', 'image/webp', 'image/heic']
)
ON CONFLICT (id) DO UPDATE SET
    public = true,
    file_size_limit = 5242880,
    allowed_mime_types = ARRAY['image/jpeg', 'image/png', 'image/webp', 'image/heic'];

-- Signatures bucket (public - for signature captures)
INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES (
    'signatures',
    'signatures',
    true,
    1048576, -- 1MB limit
    ARRAY['image/png', 'image/svg+xml']
)
ON CONFLICT (id) DO UPDATE SET
    public = true,
    file_size_limit = 1048576,
    allowed_mime_types = ARRAY['image/png', 'image/svg+xml'];

-- Route exports bucket (private - for JSON/CSV exports)
INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES (
    'route-exports',
    'route-exports',
    false,
    10485760, -- 10MB limit
    ARRAY['application/json', 'text/csv', 'application/vnd.ms-excel']
)
ON CONFLICT (id) DO UPDATE SET
    public = false,
    file_size_limit = 10485760,
    allowed_mime_types = ARRAY['application/json', 'text/csv', 'application/vnd.ms-excel'];

-- Profile images bucket (public - for user avatars)
INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES (
    'profile-images',
    'profile-images',
    true,
    2097152, -- 2MB limit
    ARRAY['image/jpeg', 'image/png', 'image/webp']
)
ON CONFLICT (id) DO UPDATE SET
    public = true,
    file_size_limit = 2097152,
    allowed_mime_types = ARRAY['image/jpeg', 'image/png', 'image/webp'];

-- ============================================
-- STORAGE POLICIES
-- Allow users to manage their own files
-- ============================================

-- Proof Photos: Users can upload/view their own photos
CREATE POLICY "Users can upload proof photos" ON storage.objects
    FOR INSERT
    TO authenticated
    WITH CHECK (
        bucket_id = 'proof-photos' AND
        (storage.foldername(name))[1] = auth.uid()::text
    );

CREATE POLICY "Users can view proof photos" ON storage.objects
    FOR SELECT
    TO authenticated
    USING (
        bucket_id = 'proof-photos' AND
        (storage.foldername(name))[1] = auth.uid()::text
    );

CREATE POLICY "Public can view proof photos" ON storage.objects
    FOR SELECT
    TO public
    USING (bucket_id = 'proof-photos');

-- Signatures: Users can upload/view their own signatures
CREATE POLICY "Users can upload signatures" ON storage.objects
    FOR INSERT
    TO authenticated
    WITH CHECK (
        bucket_id = 'signatures' AND
        (storage.foldername(name))[1] = auth.uid()::text
    );

CREATE POLICY "Users can view signatures" ON storage.objects
    FOR SELECT
    TO authenticated
    USING (
        bucket_id = 'signatures' AND
        (storage.foldername(name))[1] = auth.uid()::text
    );

CREATE POLICY "Public can view signatures" ON storage.objects
    FOR SELECT
    TO public
    USING (bucket_id = 'signatures');

-- Route Exports: Users can upload/download their own exports
CREATE POLICY "Users can upload route exports" ON storage.objects
    FOR INSERT
    TO authenticated
    WITH CHECK (
        bucket_id = 'route-exports' AND
        (storage.foldername(name))[1] = auth.uid()::text
    );

CREATE POLICY "Users can view route exports" ON storage.objects
    FOR SELECT
    TO authenticated
    USING (
        bucket_id = 'route-exports' AND
        (storage.foldername(name))[1] = auth.uid()::text
    );

CREATE POLICY "Users can delete route exports" ON storage.objects
    FOR DELETE
    TO authenticated
    USING (
        bucket_id = 'route-exports' AND
        (storage.foldername(name))[1] = auth.uid()::text
    );

-- Profile Images: Users can manage their own avatar
CREATE POLICY "Users can upload profile image" ON storage.objects
    FOR INSERT
    TO authenticated
    WITH CHECK (
        bucket_id = 'profile-images' AND
        (storage.foldername(name))[1] = auth.uid()::text
    );

CREATE POLICY "Users can update profile image" ON storage.objects
    FOR UPDATE
    TO authenticated
    USING (
        bucket_id = 'profile-images' AND
        (storage.foldername(name))[1] = auth.uid()::text
    );

CREATE POLICY "Public can view profile images" ON storage.objects
    FOR SELECT
    TO public
    USING (bucket_id = 'profile-images');

-- ============================================
-- VERIFICATION
-- ============================================
-- SELECT * FROM storage.buckets;
-- SELECT * FROM storage.objects LIMIT 10;
