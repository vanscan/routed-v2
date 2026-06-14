import React, { useEffect, useRef, useState, useMemo } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, Animated, Modal, Pressable,
  ScrollView, Linking, Share,
} from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import * as Haptics from 'expo-haptics';
import { Ionicons } from '@expo/vector-icons';
import { Stop } from '../../../store/stopsStore';
import { stopPinNumber, buildLateFreightLabels } from '../../../utils/stopPinNumber';
import {
  navColors, navShelfColors, ShelfState, RIGHT_PANEL_WIDTH,
} from './navTheme';
import { parseStopNotes } from './parseStopNotes';

interface RightPanelProps {
  shelfState: ShelfState;
  stops: Stop[];
  currentLeg: any;
  currentLegIndex: number;
  etaToNextStop: string;
  completedCount: number;
  insets: { top: number; bottom: number };
  legs?: any[];
  onOpenSettings: () => void;
  onExpandRequest: () => void;
  onMarkDelivered: () => void;
  onMarkFailed: () => void;
  onSkipStop: () => void;
  onCallCustomer: () => void;
  onJumpToStop?: (index: number) => void;
}

export const RightPanel: React.FC<RightPanelProps> = ({
  shelfState,
  stops,
  currentLeg,
  currentLegIndex,
  etaToNextStop,
  completedCount,
  insets,
  legs,
  onOpenSettings,
  onExpandRequest,
  onMarkDelivered,
  onMarkFailed,
  onSkipStop,
  onCallCustomer,
  onJumpToStop,
}) => {
  // ── Animated values ───────────────────────────────────────────────────────
  const chipsOpacity = useRef(new Animated.Value(shelfState !== 'CRUISE' ? 1 : 0)).current;
  const notesOpacity = useRef(new Animated.Value(shelfState === 'ARRIVAL' ? 1 : 0)).current;
  const actionsOpacity = useRef(new Animated.Value(shelfState !== 'CRUISE' ? 1 : 0)).current;

  useEffect(() => {
    const dur = 180;
    Animated.timing(chipsOpacity, { toValue: shelfState !== 'CRUISE' ? 1 : 0, duration: dur, useNativeDriver: true }).start();
    Animated.timing(notesOpacity, { toValue: shelfState === 'ARRIVAL' ? 1 : 0, duration: dur, useNativeDriver: true }).start();
    Animated.timing(actionsOpacity, { toValue: shelfState !== 'CRUISE' ? 1 : 0, duration: dur, useNativeDriver: true }).start();
  }, [shelfState, chipsOpacity, notesOpacity, actionsOpacity]);

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
    return '';
  }, [currentStopNumber, currentStop, lateFreightLabels]);
  const isCurrentLateFreight = currentStopNumber == null &&
    !!((currentStop as any)?.id && lateFreightLabels[(currentStop as any)?.id]);

  const colocatedInfo = useMemo(() => {
    const cur = currentLeg?.to_stop;
    if (!cur) return { count: 1, index: 1, doneCount: 0, group: [] as any[] };
    const key = `${Number(cur.latitude).toFixed(5)},${Number(cur.longitude).toFixed(5)}`;
    const group = realStops.filter(
      (s: any) => `${Number(s.latitude).toFixed(5)},${Number(s.longitude).toFixed(5)}` === key,
    );
    const index = Math.max(1, group.findIndex((s: any) => s.id === cur.id) + 1);
    const doneCount = group.filter((s: any) => s.completed).length;
    return { count: group.length || 1, index, doneCount, group };
  }, [currentLeg?.to_stop, realStops]);

  const badgeGrad = isCurrentLateFreight
    ? (['#7c3aed', '#a855f7'] as const)
    : (navColors.blueGrad as unknown as readonly [string, string]);

  // ── Notes parsing ──────────────────────────────────────────────────────────
  const parsed = useMemo(() => parseStopNotes(currentStop?.notes), [currentStop?.notes]);

  const hasChips =
    parsed.propertyType || parsed.safePlace || parsed.physicalKeyAccess ||
    currentStop?.mobile_number || currentStop?.tracking_number;

  const callPhone = () => {
    const num = currentStop?.mobile_number;
    if (!num) return;
    Linking.openURL(`tel:${num}`).catch(() => {});
  };

  const copyTracking = async () => {
    const t = currentStop?.tracking_number;
    if (!t) return;
    try {
      await Share.share({ message: t });
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    } catch {}
  };

  // ── Address parts ──────────────────────────────────────────────────────────
  const fullAddress: string = currentStop?.address || '';
  const commaIdx = fullAddress.indexOf(',');
  const streetLine = commaIdx > -1 ? fullAddress.slice(0, commaIdx) : fullAddress;
  const suburbLine = commaIdx > -1 ? fullAddress.slice(commaIdx + 1).trim() : '';

  return (
    <View style={[styles.panel, { paddingTop: insets.top + 8, paddingBottom: insets.bottom + 6 }]}>
      {/* Stop badge */}
      <TouchableOpacity
        style={styles.badge}
        onPress={onExpandRequest}
        onLongPress={openJumpMenu}
        delayLongPress={400}
        activeOpacity={0.85}
      >
        <LinearGradient
          colors={badgeGrad}
          start={{ x: 0, y: 0 }}
          end={{ x: 1, y: 1 }}
          style={styles.badgeGrad}
        >
          <Text style={styles.badgeNum}>{currentStopLabel || '?'}</Text>
          <Text style={styles.badgeOf}>of {totalStops}</Text>
          {colocatedInfo.count > 1 && (
            <View style={styles.colocDot}>
              <Text style={styles.colocDotText}>{colocatedInfo.index}/{colocatedInfo.count}</Text>
            </View>
          )}
        </LinearGradient>
      </TouchableOpacity>

      {/* ETA pill */}
      <View style={styles.etaPill}>
        <Ionicons name="time-outline" size={10} color="#34d399" />
        <Text style={styles.etaText}>{etaToNextStop}</Text>
      </View>

      <View style={styles.divider} />

      {/* Address */}
      <Text style={styles.streetText} numberOfLines={2}>{streetLine}</Text>
      {!!suburbLine && <Text style={styles.suburbText} numberOfLines={1}>{suburbLine}</Text>}

      {/* Info chips — APPROACH + ARRIVAL */}
      <Animated.View
        style={{ opacity: chipsOpacity }}
        pointerEvents={shelfState !== 'CRUISE' ? 'auto' : 'none'}
      >
        {hasChips && (
          <View style={styles.chipsWrap}>
            {parsed.propertyType && (
              <View style={styles.chip}>
                <Text style={styles.chipText}>
                  {propertyIcon(parsed.propertyType)} {shortLabel(parsed.propertyType)}
                </Text>
              </View>
            )}
            {parsed.safePlace && (
              <View style={styles.chip}>
                <Text style={styles.chipText}>🚪 {shortLabel(parsed.safePlace)}</Text>
              </View>
            )}
            {parsed.physicalKeyAccess && (
              <View style={[styles.chip, styles.chipKey]}>
                <Text style={[styles.chipText, { color: navShelfColors.chipKeyFg }]}>
                  🔑 {parsed.physicalKeyAccess}
                </Text>
              </View>
            )}
            {currentStop?.mobile_number && (
              <TouchableOpacity style={[styles.chip, styles.chipPhone]} onPress={callPhone} hitSlop={4}>
                <Text style={[styles.chipText, { color: navShelfColors.chipPhoneFg }]}>
                  📞
                </Text>
              </TouchableOpacity>
            )}
            {currentStop?.tracking_number && (
              <TouchableOpacity style={styles.chip} onLongPress={copyTracking} hitSlop={4}>
                <Text style={styles.chipText}>📦</Text>
              </TouchableOpacity>
            )}
          </View>
        )}
      </Animated.View>

      {/* Free-text notes — ARRIVAL only */}
      <Animated.View
        style={{ opacity: notesOpacity }}
        pointerEvents={shelfState === 'ARRIVAL' ? 'auto' : 'none'}
      >
        {!!parsed.freeText && (
          <View style={styles.notesBox}>
            <Text style={styles.notesText} numberOfLines={3}>{parsed.freeText}</Text>
          </View>
        )}
        {colocatedInfo.count > 1 && (
          <View style={styles.multiParcelRow}>
            <Ionicons name="warning" size={10} color={navColors.warnTitle} />
            <Text style={styles.multiParcelText}>
              {colocatedInfo.index}/{colocatedInfo.count} parcels
            </Text>
          </View>
        )}
      </Animated.View>

      {/* Spacer */}
      <View style={{ flex: 1 }} />

      {/* Gear */}
      <TouchableOpacity style={styles.gearBtn} onPress={onOpenSettings} hitSlop={6}>
        <Ionicons name="settings-outline" size={18} color="#94a3b8" />
      </TouchableOpacity>

      {/* Action buttons — APPROACH + ARRIVAL */}
      <Animated.View
        style={[styles.actions, { opacity: actionsOpacity }]}
        pointerEvents={shelfState !== 'CRUISE' ? 'auto' : 'none'}
      >
        <TouchableOpacity
          style={[styles.actionBtn, styles.deliveredBtn]}
          onPress={onMarkDelivered}
          activeOpacity={0.8}
        >
          <LinearGradient
            colors={navColors.greenGrad}
            start={{ x: 0, y: 0 }}
            end={{ x: 0, y: 1 }}
            style={styles.actionBtnGrad}
          >
            <Ionicons name="checkmark" size={22} color="#fff" />
            <Text style={styles.deliveredLabel}>Done</Text>
          </LinearGradient>
        </TouchableOpacity>

        <TouchableOpacity
          style={[styles.actionBtn, styles.failedBtn]}
          onPress={onMarkFailed}
          activeOpacity={0.8}
        >
          <Ionicons name="close" size={20} color={navColors.failedFg} />
          <Text style={[styles.actionBtnLabel, { color: navColors.failedFg }]}>Fail</Text>
        </TouchableOpacity>

        <TouchableOpacity
          style={[styles.actionBtn, styles.skipBtn]}
          onPress={onSkipStop}
          activeOpacity={0.8}
        >
          <Ionicons name="play-skip-forward" size={16} color={navColors.skipFg} />
          <Text style={[styles.actionBtnLabel, { color: navColors.skipFg }]}>Skip</Text>
        </TouchableOpacity>
      </Animated.View>

      {/* Jump-to-stop modal */}
      <Modal
        visible={isJumpOpen}
        transparent
        animationType="fade"
        onRequestClose={() => setIsJumpOpen(false)}
      >
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

function propertyIcon(type: string): string {
  const t = type.toUpperCase();
  if (t === 'HOUSE') return '🏠';
  if (t === 'UNIT' || t === 'APARTMENT') return '🏢';
  if (t === 'BUSINESS') return '🏪';
  return '📦';
}

function shortLabel(text: string): string {
  return text.length > 10 ? `${text.slice(0, 9)}…` : text;
}

const styles = StyleSheet.create({
  panel: {
    position: 'absolute',
    top: 0,
    right: 0,
    bottom: 0,
    width: RIGHT_PANEL_WIDTH,
    backgroundColor: 'rgba(15, 23, 42, 0.94)',
    borderLeftWidth: 1,
    borderLeftColor: navColors.hairline,
    zIndex: 50,
    flexDirection: 'column',
    alignItems: 'stretch',
  },

  // Stop badge
  badge: { marginHorizontal: 10, marginBottom: 0 },
  badgeGrad: {
    borderRadius: 14,
    paddingVertical: 10,
    paddingHorizontal: 8,
    alignItems: 'center',
  },
  badgeNum: { fontSize: 28, fontWeight: '900', color: '#fff', lineHeight: 30 },
  badgeOf: { fontSize: 10, color: 'rgba(255,255,255,0.70)', fontWeight: '600' },
  colocDot: {
    marginTop: 3,
    backgroundColor: 'rgba(0,0,0,0.25)',
    borderRadius: 6,
    paddingHorizontal: 5,
    paddingVertical: 1,
  },
  colocDotText: { fontSize: 9, color: '#fff', fontWeight: '700' },

  // ETA
  etaPill: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    marginHorizontal: 10,
    marginTop: 7,
    backgroundColor: navColors.etaPillBg,
    borderWidth: 1,
    borderColor: 'rgba(16,185,129,0.30)',
    borderRadius: 8,
    paddingVertical: 5,
    paddingHorizontal: 6,
    justifyContent: 'center',
  },
  etaText: { fontSize: 11, fontWeight: '800', color: navColors.etaPillText },

  divider: { height: 1, backgroundColor: navColors.divider, marginHorizontal: 10, marginVertical: 8 },

  // Address
  streetText: {
    paddingHorizontal: 10,
    fontSize: 11,
    fontWeight: '700',
    color: '#e2e8f0',
    lineHeight: 15,
  },
  suburbText: {
    paddingHorizontal: 10,
    marginTop: 2,
    fontSize: 10,
    color: '#64748b',
  },

  // Chips
  chipsWrap: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 4,
    paddingHorizontal: 10,
    paddingTop: 7,
  },
  chip: {
    backgroundColor: navShelfColors.chipBg,
    borderWidth: 1,
    borderColor: navShelfColors.chipBorder,
    borderRadius: 999,
    paddingVertical: 3,
    paddingHorizontal: 6,
  },
  chipKey: {
    backgroundColor: navShelfColors.chipKeyBg,
    borderColor: navShelfColors.chipKeyBorder,
  },
  chipPhone: {
    backgroundColor: navShelfColors.chipPhoneBg,
    borderColor: navShelfColors.chipPhoneBorder,
  },
  chipText: { fontSize: 10, fontWeight: '600', color: '#cbd5e1' },

  // Notes
  notesBox: {
    marginHorizontal: 10,
    marginTop: 7,
    backgroundColor: 'rgba(255,255,255,0.05)',
    borderRadius: 8,
    paddingHorizontal: 8,
    paddingVertical: 6,
  },
  notesText: { fontSize: 10, color: '#94a3b8', lineHeight: 14 },
  multiParcelRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: 10,
    marginTop: 5,
  },
  multiParcelText: { fontSize: 10, color: navColors.warnTitle, fontWeight: '700' },

  // Gear
  gearBtn: {
    marginHorizontal: 10,
    marginBottom: 6,
    height: 32,
    borderRadius: 10,
    backgroundColor: 'rgba(255,255,255,0.06)',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.10)',
    justifyContent: 'center',
    alignItems: 'center',
  },

  // Action buttons
  actions: {
    paddingHorizontal: 10,
    paddingBottom: 4,
    gap: 6,
  },
  actionBtn: {
    borderRadius: 14,
    height: 50,
    justifyContent: 'center',
    alignItems: 'center',
    overflow: 'hidden',
  },
  actionBtnGrad: {
    flex: 1,
    width: '100%',
    justifyContent: 'center',
    alignItems: 'center',
    gap: 2,
  },
  deliveredBtn: {},
  deliveredLabel: { fontSize: 11, fontWeight: '700', color: '#fff' },
  failedBtn: {
    backgroundColor: navColors.failedBg,
    borderWidth: 1,
    borderColor: navColors.failedBorder,
    gap: 2,
  },
  skipBtn: {
    backgroundColor: navColors.skipBg,
    borderWidth: 1,
    borderColor: navColors.skipBorder,
    gap: 2,
  },
  actionBtnLabel: { fontSize: 11, fontWeight: '700' },

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
    width: 28,
    height: 28,
    borderRadius: 14,
    backgroundColor: '#1e293b',
    justifyContent: 'center',
    alignItems: 'center',
    flexShrink: 0,
  },
  jumpBadgeDone: { backgroundColor: 'rgba(16,185,129,0.15)' },
  jumpBadgeCurrent: { backgroundColor: 'rgba(37,99,235,0.30)' },
  jumpBadgeText: { fontSize: 11, fontWeight: '700', color: '#fff' },
  jumpAddr: { flex: 1, fontSize: 12, color: '#cbd5e1' },
});
