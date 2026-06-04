/**
 * TelepathyCard
 * ─────────────────────────────────────────────────────────────────────────
 * Driver-facing surface for Route Telepathy (Phase A + B).
 *
 * Shows:
 *   - Sequence stats (Phase A): how many stop-order preferences learned
 *   - Road stats (Phase B): total + frequent OSM edges traversed
 *   - "Prefer familiar roads when navigating" toggle (persisted to
 *     AsyncStorage, read by startSingleStopNavigation before calling
 *     /api/route/preferred-polyline)
 *   - Reset buttons (per-user wipe — privacy)
 *
 * Backend endpoints:
 *   - GET  /api/learn/sequence-stats
 *   - GET  /api/learn/road-stats
 *   - POST /api/learn/sequence-reset
 *   - POST /api/learn/road-reset
 *
 * Both ML modules are gated server-side to the owner account until
 * rollout widens; `enabled_for_user` in the response flips the UI
 * between "Active" and "Coming soon".
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, ActivityIndicator,
  Switch, Alert,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { getAuthToken } from '../utils/authTokenBridge';

const BACKEND_URL = process.env.EXPO_PUBLIC_BACKEND_URL || '';
// Single source of truth for the toggle key — read at runtime by
// startSingleStopNavigation in app/(tabs)/index.tsx so the cockpit
// only calls the preferred-polyline endpoint when the driver opts in.
export const TELEPATHY_PREFER_KEY = 'telepathy.prefer_familiar_roads';

interface SequenceStats {
  total_rules?: number;
  high_confidence_rules?: number;
  enabled_for_user?: boolean;
  ready?: boolean;
}
interface RoadStats {
  total_edges?: number;
  frequent_edges?: number;
  enabled_for_user?: boolean;
  ready?: boolean;
}

export const TelepathyCard: React.FC = () => {
  const [seq, setSeq] = useState<SequenceStats | null>(null);
  const [road, setRoad] = useState<RoadStats | null>(null);
  const [prefer, setPrefer] = useState<boolean>(true);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [resetting, setResetting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const token = await getAuthToken();
      if (!token) { setError('Sign in to view Telepathy'); return; }

      // Pull both stat endpoints + the toggle in parallel — none of them
      // depend on each other, and the card stays responsive even on a
      // slow 3G fix.
      const [seqRes, roadRes, savedPref] = await Promise.all([
        fetch(`${BACKEND_URL}/api/learn/sequence-stats`, {
          headers: { Authorization: `Bearer ${token}` },
        }),
        fetch(`${BACKEND_URL}/api/learn/road-stats`, {
          headers: { Authorization: `Bearer ${token}` },
        }),
        AsyncStorage.getItem(TELEPATHY_PREFER_KEY),
      ]);

      if (seqRes.ok) setSeq(await seqRes.json());
      if (roadRes.ok) setRoad(await roadRes.json());
      // Default to ON — drivers who installed the app for routing
      // probably want the familiar-roads boost without hunting through
      // settings. Opt-out is one tap.
      setPrefer(savedPref === null ? true : savedPref === 'true');
    } catch (e: any) {
      setError(e?.message || 'Network error');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const togglePrefer = useCallback(async (next: boolean) => {
    setPrefer(next);
    try {
      await AsyncStorage.setItem(TELEPATHY_PREFER_KEY, next ? 'true' : 'false');
    } catch {
      // Storage failures are non-fatal: the in-memory toggle still
      // works for this session, just won't persist across app launches.
    }
  }, []);

  const handleReset = useCallback(async (kind: 'sequence' | 'road') => {
    Alert.alert(
      'Reset learned preferences?',
      `This wipes all ${kind === 'sequence' ? 'stop-order' : 'road-segment'} preferences learned for your account. Cannot be undone.`,
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Reset',
          style: 'destructive',
          onPress: async () => {
            setResetting(true);
            try {
              const token = await getAuthToken();
              if (!token) return;
              await fetch(`${BACKEND_URL}/api/learn/${kind}-reset`, {
                method: 'POST',
                headers: { Authorization: `Bearer ${token}` },
              });
              await load();
            } finally {
              setResetting(false);
            }
          },
        },
      ],
    );
  }, [load]);

  if (loading) {
    return (
      <View style={styles.card} testID="telepathy-card-loading">
        <View style={styles.headerRow}>
          <Ionicons name="sparkles-outline" size={18} color="#7c3aed" />
          <Text style={styles.title}>Route Telepathy</Text>
        </View>
        <View style={styles.loadingBlock}>
          <ActivityIndicator size="small" color="#94a3b8" />
        </View>
      </View>
    );
  }
  if (error) {
    return (
      <View style={styles.card} testID="telepathy-card-error">
        <View style={styles.headerRow}>
          <Ionicons name="sparkles-outline" size={18} color="#7c3aed" />
          <Text style={styles.title}>Route Telepathy</Text>
          <TouchableOpacity onPress={load} hitSlop={8} testID="telepathy-card-retry">
            <Ionicons name="refresh-outline" size={18} color="#64748b" />
          </TouchableOpacity>
        </View>
        <Text style={styles.errorLine}>{error}</Text>
      </View>
    );
  }

  // Show "Coming soon" copy when the feature gate is closed for this
  // user; everyone else sees live numbers (zeroes are fine — they go up
  // as routes get archived).
  const featureOn = (seq?.enabled_for_user || road?.enabled_for_user) === true;
  const totalEdges = road?.total_edges ?? 0;
  const frequentEdges = road?.frequent_edges ?? 0;
  const seqRules = seq?.total_rules ?? 0;
  const seqHC = seq?.high_confidence_rules ?? 0;
  const ready = (road?.ready || seq?.ready) === true;

  return (
    <View style={styles.card} testID="telepathy-card">
      <View style={styles.headerRow}>
        <Ionicons name="sparkles" size={18} color="#7c3aed" />
        <Text style={styles.title}>Route Telepathy</Text>
        <View style={[styles.statusPill, ready ? styles.statusOn : styles.statusOff]}>
          <Text style={[styles.statusText, ready ? styles.statusTextOn : styles.statusTextOff]}>
            {ready ? 'Active' : featureOn ? 'Learning' : 'Coming soon'}
          </Text>
        </View>
        <TouchableOpacity onPress={load} hitSlop={8} testID="telepathy-card-refresh">
          <Ionicons name="refresh-outline" size={18} color="#64748b" />
        </TouchableOpacity>
      </View>

      <Text style={styles.subline}>
        Learns your preferred stop order and the roads you actually drive — then steers future routes through them.
      </Text>

      <View style={styles.gridRow}>
        <View style={styles.gridCell}>
          <Text style={styles.gridValue} testID="telepathy-total-edges">{totalEdges}</Text>
          <Text style={styles.gridLabel}>edges driven</Text>
        </View>
        <View style={styles.gridCell}>
          <Text style={styles.gridValue} testID="telepathy-frequent-edges">{frequentEdges}</Text>
          <Text style={styles.gridLabel}>favourites</Text>
        </View>
        <View style={styles.gridCell}>
          <Text style={styles.gridValue} testID="telepathy-seq-rules">{seqRules}</Text>
          <Text style={styles.gridLabel}>sequences</Text>
        </View>
        <View style={styles.gridCell}>
          <Text style={styles.gridValue} testID="telepathy-seq-hc">{seqHC}</Text>
          <Text style={styles.gridLabel}>locked-in</Text>
        </View>
      </View>

      <View style={styles.toggleRow}>
        <View style={styles.toggleLabel}>
          <Text style={styles.toggleTitle}>Prefer familiar roads</Text>
          <Text style={styles.toggleHint}>
            When navigating, pick the alternative route you’ve driven before (within +15% of fastest).
          </Text>
        </View>
        <Switch
          value={prefer}
          onValueChange={togglePrefer}
          trackColor={{ false: '#e2e8f0', true: '#c4b5fd' }}
          thumbColor={prefer ? '#7c3aed' : '#94a3b8'}
          testID="telepathy-prefer-switch"
        />
      </View>

      <View style={styles.resetRow}>
        <TouchableOpacity
          style={styles.resetBtn}
          onPress={() => handleReset('sequence')}
          disabled={resetting}
          testID="telepathy-reset-sequence"
        >
          <Ionicons name="trash-outline" size={14} color="#7c3aed" />
          <Text style={styles.resetBtnText}>Reset sequences</Text>
        </TouchableOpacity>
        <TouchableOpacity
          style={styles.resetBtn}
          onPress={() => handleReset('road')}
          disabled={resetting}
          testID="telepathy-reset-road"
        >
          <Ionicons name="trash-outline" size={14} color="#7c3aed" />
          <Text style={styles.resetBtnText}>Reset roads</Text>
        </TouchableOpacity>
      </View>
    </View>
  );
};

const styles = StyleSheet.create({
  card: {
    backgroundColor: '#fff',
    marginHorizontal: 16,
    marginTop: 12,
    borderRadius: 12,
    paddingHorizontal: 16,
    paddingVertical: 14,
    borderWidth: 1,
    borderColor: '#e2e8f0',
  },
  headerRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  title: { fontSize: 14, fontWeight: '700', color: '#0f172a', flex: 1 },
  subline: { fontSize: 12, color: '#64748b', lineHeight: 17, marginTop: 6 },
  loadingBlock: { paddingVertical: 12, alignItems: 'center' },
  errorLine: { fontSize: 13, color: '#94a3b8', paddingVertical: 8 },
  statusPill: {
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 10,
  },
  statusOn: { backgroundColor: '#ede9fe' },
  statusOff: { backgroundColor: '#f1f5f9' },
  statusText: { fontSize: 10, fontWeight: '700', letterSpacing: 0.4 },
  statusTextOn: { color: '#6d28d9' },
  statusTextOff: { color: '#64748b' },
  gridRow: {
    flexDirection: 'row',
    gap: 8,
    marginVertical: 12,
  },
  gridCell: {
    flex: 1,
    paddingVertical: 10,
    backgroundColor: '#faf5ff',
    borderRadius: 8,
    alignItems: 'center',
  },
  gridValue: { fontSize: 18, fontWeight: '800', color: '#581c87' },
  gridLabel: { fontSize: 10, color: '#64748b', marginTop: 2, letterSpacing: 0.3, textAlign: 'center' },
  toggleRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    paddingVertical: 8,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: '#e2e8f0',
    marginTop: 4,
  },
  toggleLabel: { flex: 1 },
  toggleTitle: { fontSize: 13, fontWeight: '600', color: '#0f172a' },
  toggleHint: { fontSize: 11, color: '#64748b', lineHeight: 15, marginTop: 2 },
  resetRow: {
    flexDirection: 'row',
    gap: 8,
    marginTop: 8,
  },
  resetBtn: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    paddingVertical: 8,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: '#ddd6fe',
    backgroundColor: '#f5f3ff',
  },
  resetBtnText: { fontSize: 12, color: '#7c3aed', fontWeight: '600' },
});

export default TelepathyCard;
