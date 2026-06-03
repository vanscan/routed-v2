-- =====================================================
-- SUPABASE DATABASE SCHEMA FOR PATHPILOT ROUTING APP
-- =====================================================
-- Run this SQL in your Supabase Dashboard: SQL Editor
-- =====================================================

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "postgis"; -- For geospatial data (optional but recommended)

-- =====================================================
-- 1. USER PROFILES (extends auth.users)
-- =====================================================
CREATE TABLE IF NOT EXISTS public.profiles (
    id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    email TEXT,
    full_name TEXT,
    avatar_url TEXT,
    phone TEXT,
    company_name TEXT,
    
    -- Subscription/Plan info
    plan_type TEXT DEFAULT 'free' CHECK (plan_type IN ('free', 'pro', 'enterprise')),
    plan_expires_at TIMESTAMPTZ,
    
    -- Usage tracking
    routes_optimized_count INTEGER DEFAULT 0,
    last_route_at TIMESTAMPTZ,
    
    -- Preferences
    default_vehicle_type TEXT DEFAULT 'car',
    preferred_units TEXT DEFAULT 'metric' CHECK (preferred_units IN ('metric', 'imperial')),
    timezone TEXT DEFAULT 'UTC',
    
    -- Metadata
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Auto-create profile on user signup
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO public.profiles (id, email, full_name, avatar_url)
    VALUES (
        NEW.id,
        NEW.email,
        COALESCE(NEW.raw_user_meta_data->>'full_name', NEW.raw_user_meta_data->>'name'),
        COALESCE(NEW.raw_user_meta_data->>'avatar_url', NEW.raw_user_meta_data->>'picture')
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Trigger for auto-creating profiles
DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();

-- =====================================================
-- 2. SAVED LOCATIONS (Frequently used stops)
-- =====================================================
CREATE TABLE IF NOT EXISTS public.saved_locations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    
    -- Location details
    name TEXT NOT NULL,
    address TEXT,
    latitude DOUBLE PRECISION NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    
    -- Categorization
    category TEXT DEFAULT 'other' CHECK (category IN ('home', 'work', 'warehouse', 'customer', 'other')),
    tags TEXT[], -- Array of custom tags
    
    -- Additional info
    contact_name TEXT,
    contact_phone TEXT,
    notes TEXT,
    
    -- Service time (for route optimization)
    default_service_time_minutes INTEGER DEFAULT 5,
    
    -- Time windows (optional)
    preferred_time_start TIME,
    preferred_time_end TIME,
    
    -- Metadata
    is_favorite BOOLEAN DEFAULT FALSE,
    use_count INTEGER DEFAULT 0,
    last_used_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for fast user lookups
CREATE INDEX IF NOT EXISTS idx_saved_locations_user_id ON public.saved_locations(user_id);
CREATE INDEX IF NOT EXISTS idx_saved_locations_category ON public.saved_locations(category);

-- =====================================================
-- 3. ROUTE HISTORY (Completed routes)
-- =====================================================
CREATE TABLE IF NOT EXISTS public.route_history (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    
    -- Route identification
    route_name TEXT,
    
    -- Route data (stored as JSONB for flexibility)
    stops JSONB NOT NULL, -- Array of stops with lat/lng/address
    optimized_order JSONB, -- Optimized sequence
    
    -- Route metrics
    total_distance_meters DOUBLE PRECISION,
    total_duration_seconds DOUBLE PRECISION,
    stop_count INTEGER,
    
    -- Optimization details
    solver_used TEXT, -- 'lkh', 'ortools', 'vroom', etc.
    optimization_time_ms INTEGER,
    
    -- Execution status
    status TEXT DEFAULT 'planned' CHECK (status IN ('planned', 'in_progress', 'completed', 'cancelled')),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    
    -- Metadata
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for user route history
CREATE INDEX IF NOT EXISTS idx_route_history_user_id ON public.route_history(user_id);
CREATE INDEX IF NOT EXISTS idx_route_history_created_at ON public.route_history(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_route_history_status ON public.route_history(status);

-- =====================================================
-- 4. ROUTE TEMPLATES (Reusable route patterns)
-- =====================================================
CREATE TABLE IF NOT EXISTS public.route_templates (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    
    -- Template info
    name TEXT NOT NULL,
    description TEXT,
    
    -- Template data
    stops JSONB NOT NULL, -- Base stops (can be location IDs or coordinates)
    
    -- Schedule (for recurring routes)
    is_recurring BOOLEAN DEFAULT FALSE,
    recurrence_days INTEGER[], -- 0=Sunday, 1=Monday, etc.
    preferred_start_time TIME,
    
    -- Metadata
    use_count INTEGER DEFAULT 0,
    last_used_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_route_templates_user_id ON public.route_templates(user_id);

-- =====================================================
-- 5. USER SETTINGS (Detailed preferences)
-- =====================================================
CREATE TABLE IF NOT EXISTS public.user_settings (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID UNIQUE NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    
    -- Map preferences
    map_style TEXT DEFAULT 'streets', -- 'streets', 'satellite', 'dark', 'light'
    show_traffic BOOLEAN DEFAULT TRUE,
    default_zoom_level INTEGER DEFAULT 12,
    
    -- Route optimization preferences
    default_solver TEXT DEFAULT 'auto', -- 'auto', 'lkh', 'ortools', 'vroom'
    optimize_for TEXT DEFAULT 'distance' CHECK (optimize_for IN ('distance', 'time', 'balanced')),
    avoid_tolls BOOLEAN DEFAULT FALSE,
    avoid_highways BOOLEAN DEFAULT FALSE,
    
    -- Vehicle settings
    vehicle_type TEXT DEFAULT 'car',
    vehicle_capacity JSONB, -- {"weight": 1000, "volume": 50}
    
    -- Notification preferences
    email_notifications BOOLEAN DEFAULT TRUE,
    push_notifications BOOLEAN DEFAULT TRUE,
    route_completion_alerts BOOLEAN DEFAULT TRUE,
    
    -- Export preferences
    default_export_format TEXT DEFAULT 'csv' CHECK (default_export_format IN ('csv', 'xlsx', 'json', 'gpx')),
    
    -- Metadata
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_settings_user_id ON public.user_settings(user_id);

-- =====================================================
-- 6. IMPORT HISTORY (Track file imports)
-- =====================================================
CREATE TABLE IF NOT EXISTS public.import_history (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    
    -- File info
    file_name TEXT NOT NULL,
    file_type TEXT, -- 'csv', 'xlsx', 'json'
    file_size_bytes INTEGER,
    storage_path TEXT, -- Path in Supabase Storage
    
    -- Import results
    total_rows INTEGER,
    successful_rows INTEGER,
    failed_rows INTEGER,
    error_details JSONB, -- Array of errors with row numbers
    
    -- Status
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'completed', 'failed')),
    
    -- Metadata
    created_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_import_history_user_id ON public.import_history(user_id);

-- =====================================================
-- ROW LEVEL SECURITY (RLS) POLICIES
-- =====================================================

-- Enable RLS on all tables
ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.saved_locations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.route_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.route_templates ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.import_history ENABLE ROW LEVEL SECURITY;

-- Profiles: Users can only access their own profile
CREATE POLICY "Users can view own profile" ON public.profiles
    FOR SELECT USING (auth.uid() = id);
    
CREATE POLICY "Users can update own profile" ON public.profiles
    FOR UPDATE USING (auth.uid() = id);

-- Saved Locations: Users can only access their own locations
CREATE POLICY "Users can view own locations" ON public.saved_locations
    FOR SELECT USING (auth.uid() = user_id);
    
CREATE POLICY "Users can insert own locations" ON public.saved_locations
    FOR INSERT WITH CHECK (auth.uid() = user_id);
    
CREATE POLICY "Users can update own locations" ON public.saved_locations
    FOR UPDATE USING (auth.uid() = user_id);
    
CREATE POLICY "Users can delete own locations" ON public.saved_locations
    FOR DELETE USING (auth.uid() = user_id);

-- Route History: Users can only access their own routes
CREATE POLICY "Users can view own routes" ON public.route_history
    FOR SELECT USING (auth.uid() = user_id);
    
CREATE POLICY "Users can insert own routes" ON public.route_history
    FOR INSERT WITH CHECK (auth.uid() = user_id);
    
CREATE POLICY "Users can update own routes" ON public.route_history
    FOR UPDATE USING (auth.uid() = user_id);
    
CREATE POLICY "Users can delete own routes" ON public.route_history
    FOR DELETE USING (auth.uid() = user_id);

-- Route Templates: Users can only access their own templates
CREATE POLICY "Users can view own templates" ON public.route_templates
    FOR SELECT USING (auth.uid() = user_id);
    
CREATE POLICY "Users can insert own templates" ON public.route_templates
    FOR INSERT WITH CHECK (auth.uid() = user_id);
    
CREATE POLICY "Users can update own templates" ON public.route_templates
    FOR UPDATE USING (auth.uid() = user_id);
    
CREATE POLICY "Users can delete own templates" ON public.route_templates
    FOR DELETE USING (auth.uid() = user_id);

-- User Settings: Users can only access their own settings
CREATE POLICY "Users can view own settings" ON public.user_settings
    FOR SELECT USING (auth.uid() = user_id);
    
CREATE POLICY "Users can insert own settings" ON public.user_settings
    FOR INSERT WITH CHECK (auth.uid() = user_id);
    
CREATE POLICY "Users can update own settings" ON public.user_settings
    FOR UPDATE USING (auth.uid() = user_id);

-- Import History: Users can only access their own imports
CREATE POLICY "Users can view own imports" ON public.import_history
    FOR SELECT USING (auth.uid() = user_id);
    
CREATE POLICY "Users can insert own imports" ON public.import_history
    FOR INSERT WITH CHECK (auth.uid() = user_id);

-- =====================================================
-- STORAGE BUCKETS
-- =====================================================
-- Run these in the Supabase Dashboard > Storage

-- Create buckets (run separately in SQL or use Dashboard UI):
-- INSERT INTO storage.buckets (id, name, public) VALUES ('imports', 'imports', false);
-- INSERT INTO storage.buckets (id, name, public) VALUES ('exports', 'exports', false);
-- INSERT INTO storage.buckets (id, name, public) VALUES ('user-files', 'user-files', false);
-- INSERT INTO storage.buckets (id, name, public) VALUES ('avatars', 'avatars', true);

-- =====================================================
-- REALTIME SUBSCRIPTIONS
-- =====================================================
-- Enable realtime for specific tables (run in Dashboard > Database > Replication)

-- ALTER PUBLICATION supabase_realtime ADD TABLE public.route_history;
-- ALTER PUBLICATION supabase_realtime ADD TABLE public.saved_locations;

-- =====================================================
-- HELPER FUNCTIONS
-- =====================================================

-- Function to update 'updated_at' timestamp
CREATE OR REPLACE FUNCTION public.update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Apply updated_at trigger to all tables
CREATE TRIGGER update_profiles_updated_at BEFORE UPDATE ON public.profiles
    FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();

CREATE TRIGGER update_saved_locations_updated_at BEFORE UPDATE ON public.saved_locations
    FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();

CREATE TRIGGER update_route_history_updated_at BEFORE UPDATE ON public.route_history
    FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();

CREATE TRIGGER update_route_templates_updated_at BEFORE UPDATE ON public.route_templates
    FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();

CREATE TRIGGER update_user_settings_updated_at BEFORE UPDATE ON public.user_settings
    FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();

-- =====================================================
-- DONE! Your Supabase database is ready.
-- =====================================================
