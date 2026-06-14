import React, { useRef, useState, useMemo } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, Animated,
  Modal, Pressable, ScrollView, PanResponder,
} from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import * as Haptics from 'expo-haptics';
import { Ionicons } from '@expo/vector-icons';
import { Stop } from '../../../store/stopsStore';
import { stopPinNumber, buildLateFreightLabels } from '../../../utils/stopPinNumber';
import { navColors, navShelfColors } from './navTheme';
import { NavSettings } from './useNavSettings';

interface NavCardProps {
  settings: NavSettings;
  stops: Stop[];
  currentLeg: any;
  currentLegIndex: number;
  etaToNextStop: string;
  speedKmh: number;
  insets: { top: number; bottom: number };
  legs?: any[];
  canPreviewNext?: boolean;
  canPreviewPrev?: boolean;
  onOpenSettings: () => void;
  onMarkDelivered: () => void;
  onMarkFailed: () => void;
  onSkipStop: () => void;
  onShowDetails?: () => void;
  onJumpToStop?: (index: number) => void;
  onPreviewNextStop?: () => void;
  onPreviewPrevStop?: () => void;
}

export const NavCard: React.FC<NavCardProps> = ({
  settings,
  stops,
  currentLeg,
  currentLegIndex,
  etaToNextStop,
  speedKmh,
  insets,
  legs,
  canPreviewNext = true,
  canPreviewPrev = true,
  onOpenSettings,
  onMarkDelivered,
  onMarkFailed,
  onSkipStop,
  onShowDetails,
  onJumpToStop,
  onPreviewNextStop,
  onPreviewPrevStop,
}) => {
  // ── Stop metadata ──────────────────────────────────────────────────────────
  const realStops = useMemo(
    () => (stops as any[]).filter((s: any) => !s.is_current_location),
    [stops],
  );
  const totalStops = realStops.length || stops.length;
  const currentStop = currentLeg?.to_stop;

  const lateFreightLabels = useMemo(() => buildLateFreightLabels(stops as any), [stops]);
  const currentStopNumber = stopPinNumber(currentStop);
  const currentStopLabel = useMemo(() => {
    if (currentStopNumber != null) return String(currentStopNumber);
    const id = (currentStop as any)?.id;
    if (id && lateFreightLabels[id]) return lateFreightLabels[id];
    return '?';
  }, [currentStopNumber, currentStop, lateFreightLabels]);

  const isCurrentLateFreight = currentStopNumber == null &&
    !!((currentStop as any)?.id && lateFreightLabels[(currentStop as any)?.id]);

  const badgeGrad = isCurrentLateFreight
    ? (['#7c3aed', '#a855f7'] as const)
    : (navColors.blueGrad as unknown as readonly [string, string]);

  // ── Speed display ──────────────────────────────────────────────────────────
  const speedDisplay = settings.speedUnits === 'mph'
    ? `${Math.round(speedKmh * 0.621371)}`
    : `${speedKmh}`;
  const speedUnit = settings.speedUnits === 'mph' ? 'mph' : 'km/h';

  // ── Address split ──────────────────────────────────────────────────────────
  const fullAddress: string = currentStop?.address || '';
  const commaIdx = fullAddress.indexOf(',');
  const streetLine = commaIdx > -1 ? fullAddress.slice(0, commaIdx) : fullAddress;
  const suburbLine = commaIdx > -1 ? fullAddress.slice(commaIdx + 1).trim() : '';

  // ── Jump-to-stop modal ─────────────────────────────────────────────────────
  const [isJumpOpen, setIsJumpOpen] = useState(false);
  const openJumpMenu = () => {
    if (!legs || legs.length <= 1 || !onJumpToStop) return;
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
    setIsJumpOpen(true);
  };
  const handleJump = (idx: number) => {
    setIsJumpOpen(false);
    if (onJumpToStop && idx !== currentLegIndex) {
      Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
      onJumpToStop(idx);
    }
  };

  // ── Swipe between stops ───────────────────────────────────────────────────
  const swipeX = useRef(new Animated.Value(0)).current;
  const swipeResponder = useMemo(
    () => PanResponder.create({
      onMoveShouldSetPanResponder: (_e, g) =>
        Math.abs(g.dx) > 20 && Math.abs(g.dx) > Math.abs(g.dy) * 1.4,
      onPanResponderMove: (_e, g) => {
        const atEdge = (g.dx > 0 && !canPreviewPrev) || (g.dx < 0 && !canPreviewNext);
        swipeX.setValue(atEdge ? g.dx * 0.4 : g.dx);
      },
      onPanResponderRelease: (_e, g) => {
        const committed = Math.abs(g.vx) > 0.4 || Math.abs(g.dx) > 70;
        if (committed && g.dx < 0 && canPreviewNext && onPreviewNextStop) {
          Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
          Animated.timing(swipeX, { toValue: -400, duration: 160, useNativeDriver: true })
            .start(() => { onPreviewNextStop(); swipeX.setValue(0); });
          return;
        }
        if (committed && g.dx > 0 && canPreviewPrev && onPreviewPrevStop) {
          Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
          Animated.timing(swipeX, { toValue: 400, duration: 160, useNativeDriver: true })
            .start(() => { onPreviewPrevStop(); swipeX.setValue(0); });
          return;
        }
        if (committed) Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
        Animated.spring(swipeX, { toValue: 0, useNativeDriver: true, bounciness: 6 }).start();
      },
      onPanResponderTerminate: () => {
        Animated.spring(swipeX, { toValue: 0, useNativeDriver: true }).start();
      },
    }),
    [swipeX, canPreviewNext, canPreviewPrev, onPreviewNextStop, onPreviewPrevStop],
  );

  return (
    <View style={[styles.card, { paddingBottom: insets.bottom + 8 }]}>
      {/* Info row */}
      <Animated.View
        style={[styles.infoRow, { transform: [{ translateX: swipeX }] }]}
        {...swipeResponder.panHandlers}
      >
        {/* Stop badge */}
        <TouchableOpacity onPress={onShowDetails} onLongPress={openJumpMenu} delayLongPress={400} activeOpacity={0.85}>
          <LinearGradient colors={badgeGrad} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={styles.badge}>
            <Text style={styles.badgeNum}>{currentStopLabel}</Text>
            <Text style={styles.badgeOf}>{totalStops}</Text>
          </LinearGradient>
        </TouchableOpacity>

        {/* Address */}
        <View style={styles.addressBlock}>
          <Text style={styles.street} numberOfLines={1}>{streetLine}</Text>
          {!!suburbLine && <Text style={styles.suburb} numberOfLines={1}>{suburbLine}</Text>}
        </View>

        {/* ETA */}
        <View style={styles.etaPill}>
          <Text style={styles.etaText}>{etaToNextStop}</Text>
        </View>

        {/* Speed */}
        <View style={styles.speedBlock}>
          <Text style={styles.speedNum}>{speedDisplay}</Text>
          <Text style={styles.speedUnit}>{speedUnit}</Text>
        </View>

        {/* Gear */}
        <TouchableOpacity onPress={onOpenSettings} hitSlop={10} style={styles.gearBtn}>
          <Ionicons name="settings-outline" size={20} color={navColors.textMuted} />
        </TouchableOpacity>
      </Animated.View>

      {/* Divider */}
      <View style={styles.divider} />

      {/* Action buttons */}
      <View style={styles.actionsRow}>
        <TouchableOpacity
          style={styles.actionBtn}
          onPress={() => {
            Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
            onMarkDelivered();
          }}
          activeOpacity={0.8}
        >
          <LinearGradient colors={navColors.greenGrad} start={{ x: 0, y: 0 }} end={{ x: 1, y: 0 }} style={styles.deliveredGrad}>
            <Ionicons name="checkmark" size={20} color="#fff" />
            <Text style={styles.deliveredLabel}>Delivered</Text>
          </LinearGradient>
        </TouchableOpacity>

        <TouchableOpacity
          style={[styles.actionBtn, styles.failedBtn]}
          onPress={() => {
            Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
            onMarkFailed();
          }}
          activeOpacity={0.8}
        >
          <Ionicons name="close" size={19} color={navColors.failedFg} />
          <Text style={[styles.actionLabel, { color: navColors.failedFg }]}>Failed</Text>
        </TouchableOpacity>

        <TouchableOpacity
          style={[styles.actionBtn, styles.skipBtn]}
          onPress={() => {
            Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
            onSkipStop();
          }}
          activeOpacity={0.8}
        >
          <Ionicons name="play-skip-forward" size={16} color={navColors.skipFg} />
          <Text style={[styles.actionLabel, { color: navColors.skipFg }]}>Skip</Text>
        </TouchableOpacity>
      </View>

      {/* Jump-to-stop modal */}
      <Modal visible={isJumpOpen} transparent animationType="fade" onRequestClose={() => setIsJumpOpen(false)}>
        <Pressable style={styles.jumpOverlay} onPress={() => setIsJumpOpen(false)}>
          <View style={styles.jumpSheet}>
            <Text style={styles.jumpTitle}>Jump to stop</Text>
            <ScrollView bounces={false} showsVerticalScrollIndicator={false}>
              {(legs || []).map((leg: any, idx: number) => {
                const s = leg.to_stop;
                const num = stopPinNumber(s);
                const label = num != null ? String(num) : lateFreightLabels[s?.id] || `${idx + 1}`;
                const done = !!s?.completed;
                const isCurrent = idx === currentLegIndex;
                return (
                  <TouchableOpacity
                    key={idx}
                    style={[styles.jumpRow, isCurrent && styles.jumpRowCurrent]}
                    onPress={() => handleJump(idx)}
                  >
                    <View style={[styles.jumpBadge, done && styles.jumpBadgeDone, isCurrent && styles.jumpBadgeCurrent]}>
                      <Text style={styles.jumpBadgeText}>{label}</Text>
                    </View>
                    <Text style={[styles.jumpAddr, done && { color: '#475569' }]} numberOfLines={1}>
                      {s?.address || `Stop ${idx + 1}`}
                    </Text>
                    {done && <Ionicons name="checkmark-circle" size={14} color="#10b981" />}
                  </TouchableOpacity>
                );
              })}
            </ScrollView>
          </View>
        </Pressable>
      </Modal>
    </View>
  );
};

const styles = StyleSheet.create({
  card: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    backgroundColor: 'rgba(15, 23, 42, 0.92)',
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    borderTopWidth: 1,
    borderTopColor: navColors.hairline,
  },

  // Info row
  infoRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingHorizontal: 14,
    paddingTop: 14,
    paddingBottom: 10,
  },
  badge: {
    width: 44,
    height: 44,
    borderRadius: 13,
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0,
  },
  badgeNum: { fontSize: 18, fontWeight: '900', color: '#fff', lineHeight: 20 },
  badgeOf: { fontSize: 9, color: 'rgba(255,255,255,0.70)', fontWeight: '600' },

  addressBlock: { flex: 1, gap: 1 },
  street: { fontSize: 13, fontWeight: '700', color: '#e2e8f0' },
  suburb: { fontSize: 11, color: '#64748b' },

  etaPill: {
    backgroundColor: navColors.etaPillBg,
    borderRadius: 8,
    paddingHorizontal: 8,
    paddingVertical: 4,
    flexShrink: 0,
  },
  etaText: { fontSize: 12, fontWeight: '800', color: navColors.etaPillText },

  speedBlock: { alignItems: 'center', flexShrink: 0 },
  speedNum: { fontSize: 16, fontWeight: '900', color: '#34d399', lineHeight: 18 },
  speedUnit: { fontSize: 8, color: '#6ee7b7', fontWeight: '600' },

  gearBtn: { padding: 4, flexShrink: 0 },

  divider: { height: 1, backgroundColor: navColors.divider, marginHorizontal: 14 },

  // Action buttons
  actionsRow: {
    flexDirection: 'row',
    gap: 8,
    paddingHorizontal: 14,
    paddingTop: 10,
  },
  actionBtn: {
    flex: 1,
    height: 52,
    borderRadius: 16,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    overflow: 'hidden',
  },
  deliveredGrad: {
    flex: 1,
    width: '100%',
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
  },
  deliveredLabel: { fontSize: 13, fontWeight: '700', color: '#fff' },
  failedBtn: {
    backgroundColor: navColors.failedBg,
    borderWidth: 1,
    borderColor: navColors.failedBorder,
  },
  skipBtn: {
    backgroundColor: navColors.skipBg,
    borderWidth: 1,
    borderColor: navColors.skipBorder,
  },
  actionLabel: { fontSize: 13, fontWeight: '700' },

  // Jump modal
  jumpOverlay: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.60)',
    justifyContent: 'center',
    padding: 24,
  },
  jumpSheet: {
    backgroundColor: 'rgba(15,23,42,0.98)',
    borderRadius: 20,
    borderWidth: 1,
    borderColor: navColors.hairline,
    maxHeight: 420,
    paddingBottom: 16,
  },
  jumpTitle: {
    fontSize: 14,
    fontWeight: '700',
    color: '#fff',
    paddingHorizontal: 20,
    paddingVertical: 14,
    borderBottomWidth: 1,
    borderBottomColor: navColors.divider,
  },
  jumpRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: navColors.divider,
  },
  jumpRowCurrent: { backgroundColor: 'rgba(37,99,235,0.12)' },
  jumpBadge: {
    width: 28, height: 28, borderRadius: 14,
    backgroundColor: '#1e293b',
    justifyContent: 'center', alignItems: 'center',
    flexShrink: 0,
  },
  jumpBadgeDone: { backgroundColor: 'rgba(16,185,129,0.15)' },
  jumpBadgeCurrent: { backgroundColor: 'rgba(37,99,235,0.30)' },
  jumpBadgeText: { fontSize: 11, fontWeight: '700', color: '#fff' },
  jumpAddr: { flex: 1, fontSize: 12, color: '#cbd5e1' },
});
