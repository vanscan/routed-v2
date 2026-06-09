-- ============================================
-- Routr Supabase Database Schema
-- Run this in Supabase Dashboard > SQL Editor
-- ============================================

-- Enable UUID extension if not already enabled
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================
-- 1. USER_PREFERENCES TABLE
-- Stores user settings that sync across devices
-- ============================================
CREATE TABLE IF NOT EXISTS user_preferences (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id TEXT UNIQUE NOT NULL,
    prefer_familiar_roads BOOLEAN DEFAULT true,
    voice_enabled BOOLEAN DEFAULT true,
    auto_advance BOOLEAN DEFAULT true,
    map_style TEXT DEFAULT 'colorful',
    units TEXT DEFAULT 'metric' CHECK (units IN ('metric', 'imperial')),
    theme TEXT DEFAULT 'system' CHECK (theme IN ('light', 'dark', 'system')),
    notification_sounds BOOLEAN DEFAULT true,
    haptic_feedback BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for fast lookups by user_id
CREATE INDEX IF NOT EXISTS idx_user_preferences_user_id ON user_preferences(user_id);

-- ============================================
-- 2. DRIVER_LOCATIONS TABLE
-- Real-time driver tracking for fleet view
-- ============================================
CREATE TABLE IF NOT EXISTS driver_locations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id TEXT UNIQUE NOT NULL,
    latitude DOUBLE PRECISION NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    heading DOUBLE PRECISION DEFAULT 0,
    speed DOUBLE PRECISION DEFAULT 0,
    accuracy DOUBLE PRECISION DEFAULT 0,
    altitude DOUBLE PRECISION,
    route_id TEXT,
    stop_id TEXT,
    status TEXT DEFAULT 'active' CHECK (status IN ('active', 'idle', 'offline')),
    battery_level INTEGER,
    timestamp TIMESTAMPTZ DEFAULT NOW()
);

-- Index for fast lookups and geo queries
CREATE INDEX IF NOT EXISTS idx_driver_locations_user_id ON driver_locations(user_id);
CREATE INDEX IF NOT EXISTS idx_driver_locations_timestamp ON driver_locations(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_driver_locations_route_id ON driver_locations(route_id);

-- ============================================
-- 3. DELIVERY_EVENTS TABLE
-- Audit trail for all delivery actions
-- ============================================
CREATE TABLE IF NOT EXISTS delivery_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id TEXT NOT NULL,
    stop_id TEXT NOT NULL,
    route_id TEXT,
    event_type TEXT NOT NULL CHECK (event_type IN ('started', 'arrived', 'completed', 'skipped', 'failed', 'photo_taken', 'signature_captured', 'note_added')),
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    accuracy DOUBLE PRECISION,
    notes TEXT,
    proof_photo_url TEXT,
    signature_url TEXT,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_delivery_events_user_id ON delivery_events(user_id);
CREATE INDEX IF NOT EXISTS idx_delivery_events_stop_id ON delivery_events(stop_id);
CREATE INDEX IF NOT EXISTS idx_delivery_events_route_id ON delivery_events(route_id);
CREATE INDEX IF NOT EXISTS idx_delivery_events_created_at ON delivery_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_delivery_events_type ON delivery_events(event_type);

-- ============================================
-- 4. NOTIFICATIONS TABLE
-- Push notifications and in-app alerts
-- ============================================
CREATE TABLE IF NOT EXISTS notifications (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    type TEXT DEFAULT 'info' CHECK (type IN ('info', 'warning', 'success', 'error', 'route_update', 'delivery_reminder')),
    read BOOLEAN DEFAULT false,
    action_url TEXT,
    data JSONB DEFAULT '{}',
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for notification queries
CREATE INDEX IF NOT EXISTS idx_notifications_user_id ON notifications(user_id);
CREATE INDEX IF NOT EXISTS idx_notifications_unread ON notifications(user_id, read) WHERE read = false;
CREATE INDEX IF NOT EXISTS idx_notifications_created_at ON notifications(created_at DESC);

-- ============================================
-- 5. AUDIT_LOGS TABLE
-- Security and compliance audit trail
-- ============================================
CREATE TABLE IF NOT EXISTS audit_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id TEXT NOT NULL,
    action TEXT NOT NULL,
    resource_type TEXT,
    resource_id TEXT,
    details JSONB DEFAULT '{}',
    ip_address INET,
    user_agent TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for audit queries
CREATE INDEX IF NOT EXISTS idx_audit_logs_user_id ON audit_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_action ON audit_logs(action);
CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at ON audit_logs(created_at DESC);

-- ============================================
-- 6. FLEET_MEMBERS TABLE (Optional)
-- For multi-driver fleet management
-- ============================================
CREATE TABLE IF NOT EXISTS fleet_members (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    fleet_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    role TEXT DEFAULT 'driver' CHECK (role IN ('owner', 'admin', 'dispatcher', 'driver')),
    status TEXT DEFAULT 'active' CHECK (status IN ('active', 'invited', 'inactive')),
    invited_by TEXT,
    joined_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(fleet_id, user_id)
);

-- Indexes for fleet queries
CREATE INDEX IF NOT EXISTS idx_fleet_members_fleet_id ON fleet_members(fleet_id);
CREATE INDEX IF NOT EXISTS idx_fleet_members_user_id ON fleet_members(user_id);

-- ============================================
-- ROW LEVEL SECURITY (RLS) POLICIES
-- ============================================

-- Enable RLS on all tables
ALTER TABLE user_preferences ENABLE ROW LEVEL SECURITY;
ALTER TABLE driver_locations ENABLE ROW LEVEL SECURITY;
ALTER TABLE delivery_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE notifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE fleet_members ENABLE ROW LEVEL SECURITY;

-- User Preferences: Users can only access their own preferences
CREATE POLICY "Users can view own preferences" ON user_preferences
    FOR SELECT USING (auth.uid()::text = user_id OR auth.jwt() ->> 'sub' = user_id);

CREATE POLICY "Users can insert own preferences" ON user_preferences
    FOR INSERT WITH CHECK (auth.uid()::text = user_id OR auth.jwt() ->> 'sub' = user_id);

CREATE POLICY "Users can update own preferences" ON user_preferences
    FOR UPDATE USING (auth.uid()::text = user_id OR auth.jwt() ->> 'sub' = user_id);

-- Driver Locations: Fleet members can view each other's locations
CREATE POLICY "Users can view own location" ON driver_locations
    FOR SELECT USING (auth.uid()::text = user_id OR auth.jwt() ->> 'sub' = user_id);

CREATE POLICY "Users can update own location" ON driver_locations
    FOR ALL USING (auth.uid()::text = user_id OR auth.jwt() ->> 'sub' = user_id);

-- Allow fleet admins to view driver locations (requires fleet_members check)
CREATE POLICY "Fleet admins can view driver locations" ON driver_locations
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM fleet_members fm1
            JOIN fleet_members fm2 ON fm1.fleet_id = fm2.fleet_id
            WHERE fm1.user_id = (auth.uid()::text)
            AND fm2.user_id = driver_locations.user_id
            AND fm1.role IN ('owner', 'admin', 'dispatcher')
        )
    );

-- Delivery Events: Users can only access their own events
CREATE POLICY "Users can view own delivery events" ON delivery_events
    FOR SELECT USING (auth.uid()::text = user_id OR auth.jwt() ->> 'sub' = user_id);

CREATE POLICY "Users can insert own delivery events" ON delivery_events
    FOR INSERT WITH CHECK (auth.uid()::text = user_id OR auth.jwt() ->> 'sub' = user_id);

-- Notifications: Users can only access their own notifications
CREATE POLICY "Users can view own notifications" ON notifications
    FOR SELECT USING (auth.uid()::text = user_id OR auth.jwt() ->> 'sub' = user_id);

CREATE POLICY "Users can update own notifications" ON notifications
    FOR UPDATE USING (auth.uid()::text = user_id OR auth.jwt() ->> 'sub' = user_id);

-- System can insert notifications for any user (for backend use)
CREATE POLICY "Service role can insert notifications" ON notifications
    FOR INSERT WITH CHECK (true);

-- Audit Logs: Users can view their own logs, admins can view all
CREATE POLICY "Users can view own audit logs" ON audit_logs
    FOR SELECT USING (auth.uid()::text = user_id OR auth.jwt() ->> 'sub' = user_id);

CREATE POLICY "Service role can insert audit logs" ON audit_logs
    FOR INSERT WITH CHECK (true);

-- Fleet Members: Users can view their fleet memberships
CREATE POLICY "Users can view own fleet memberships" ON fleet_members
    FOR SELECT USING (auth.uid()::text = user_id OR auth.jwt() ->> 'sub' = user_id);

CREATE POLICY "Fleet owners can manage members" ON fleet_members
    FOR ALL USING (
        EXISTS (
            SELECT 1 FROM fleet_members fm
            WHERE fm.fleet_id = fleet_members.fleet_id
            AND fm.user_id = (auth.uid()::text)
            AND fm.role = 'owner'
        )
    );

-- ============================================
-- REAL-TIME PUBLICATION
-- Enable real-time for specific tables
-- ============================================

-- Note: Run this after creating the tables
-- This enables Supabase Realtime for these tables

-- First, check if publication exists
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_publication WHERE pubname = 'supabase_realtime'
    ) THEN
        CREATE PUBLICATION supabase_realtime;
    END IF;
END
$$;

-- Add tables to realtime publication
ALTER PUBLICATION supabase_realtime ADD TABLE driver_locations;
ALTER PUBLICATION supabase_realtime ADD TABLE notifications;
ALTER PUBLICATION supabase_realtime ADD TABLE delivery_events;

-- ============================================
-- FUNCTIONS & TRIGGERS
-- ============================================

-- Auto-update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Apply trigger to user_preferences
DROP TRIGGER IF EXISTS update_user_preferences_updated_at ON user_preferences;
CREATE TRIGGER update_user_preferences_updated_at
    BEFORE UPDATE ON user_preferences
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Clean up old driver locations (older than 24 hours)
CREATE OR REPLACE FUNCTION cleanup_old_driver_locations()
RETURNS void AS $$
BEGIN
    DELETE FROM driver_locations
    WHERE timestamp < NOW() - INTERVAL '24 hours';
END;
$$ LANGUAGE plpgsql;

-- Clean up expired notifications
CREATE OR REPLACE FUNCTION cleanup_expired_notifications()
RETURNS void AS $$
BEGIN
    DELETE FROM notifications
    WHERE expires_at IS NOT NULL AND expires_at < NOW();
END;
$$ LANGUAGE plpgsql;

-- ============================================
-- STORAGE BUCKETS
-- Run these in Storage section of Dashboard
-- OR use SQL (requires service role)
-- ============================================

-- Note: Storage buckets are typically created via Dashboard
-- These commands require service_role access

-- INSERT INTO storage.buckets (id, name, public)
-- VALUES 
--     ('proof-photos', 'proof-photos', true),
--     ('signatures', 'signatures', true),
--     ('route-exports', 'route-exports', false),
--     ('profile-images', 'profile-images', true)
-- ON CONFLICT (id) DO NOTHING;

-- ============================================
-- VERIFICATION QUERIES
-- Run these to verify tables were created
-- ============================================

-- SELECT table_name FROM information_schema.tables 
-- WHERE table_schema = 'public' 
-- AND table_name IN ('user_preferences', 'driver_locations', 'delivery_events', 'notifications', 'audit_logs', 'fleet_members');

-- SELECT * FROM pg_publication_tables WHERE pubname = 'supabase_realtime';

COMMENT ON TABLE user_preferences IS 'User settings that sync across devices';
COMMENT ON TABLE driver_locations IS 'Real-time driver tracking for fleet view';
COMMENT ON TABLE delivery_events IS 'Audit trail for all delivery actions';
COMMENT ON TABLE notifications IS 'Push notifications and in-app alerts';
COMMENT ON TABLE audit_logs IS 'Security and compliance audit trail';
COMMENT ON TABLE fleet_members IS 'Multi-driver fleet management';
