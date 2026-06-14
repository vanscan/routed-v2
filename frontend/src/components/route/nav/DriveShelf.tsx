import React, { useEffect, useRef, useState, useMemo } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, ScrollView, Animated,
  PanResponder, Modal, Pressable,
} from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import * as Haptics from 'expo-haptics';
import { Ionicons } from '@expo/vector-icons';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { Stop } from '../../../store/stopsStore';
import { formatDistance, getManeuverIcon } from '../../../utils/route';
import { stopPinNumber, buildLateFreightLabels } from '../../../utils/stopPinNumber';
import {
  navColors, navRadii, navShelfColors, ShelfState,
  SHELF_HEIGHT_CRUISE, SHELF_HEIGHT_APPROACH, SHELF_HEIGHT_ARRIVAL_MIN,
} from './navTheme';
import { PropertyCard } from './PropertyCard';
import { NavSettings } from './useNavSettings';
import { parseStopNotes } from './parseStopNotes';

interface DriveShelfProps {
  shelfState: ShelfState;
  settings: NavSettings;
  currentStep: any;
  currentLeg: any;
  stops: Stop[];
  currentLegIndex: number;
  speedKmh: number;
  etaToNextStop: string;
  completedCount: number;
  insets: { top: number; bottom: number };
  liveRoute: any;
  legs?: any[];
  canPreviewNext?: boolean;
  canPreviewPrev?: boolean;
  onOpenSettings: () => void;
  onMarkDelivered: () => void;
  onMarkFailed: () => void;
  onSkipStop: () => void;
  onStopNavigation: () => void;
  onCallCustomer: () => void;
  onShareETA: () => void;
  onShowDetails?: () => void;
  onJumpToStop?: (index: number) => void;
  onPreviewNextStop?: () => void;
  onPreviewPrevStop?: () => void;
}

const ApproachPropStrip: React.FC<{ notes?: string | null }> = ({ notes }) => {
  const { propertyType, safePlace } = React.useMemo(() => parseStopNotes(notes), [notes]);
  if (!propertyType && !safePlace) return null;
  return (
    <>
      {propertyType && (
        <View style={approachChipStyle}>
          <Text style={approachChipTextStyle}>🏠 {propertyType}</Text>
        </View>
      )}
      {safePlace && (
        <View style={approachChipStyle}>
          <Text style={approachChipTextStyle}>🚪 {safePlace}</Text>
        </View>
      )}
    </>
  );
};

// Defined outside StyleSheet so ApproachPropStrip can reference them before styles block.
const approachChipStyle = {
  backgroundColor: 'rgba(255,255,255,0.07)',
  borderWidth: 1,
  borderColor: 'rgba(255,255,255,0.12)',
  borderRadius: 999,
  paddingVertical: 4,
  paddingHorizontal: 9,
} as const;
const approachChipTextStyle = { fontSize: 11, fontWeight: '600' as const, color: '#cbd5e1' };

const SHELF_HEIGHTS: Record<ShelfState, number> = {
  CRUISE: SHELF_HEIGHT_CRUISE,
  APPROACH: SHELF_HEIGHT_APPROACH,
  ARRIVAL: SHELF_HEIGHT_ARRIVAL_MIN,
};

export const DriveShelf: React.FC<DriveShelfProps> = ({
  shelfState,
  settings,
  currentStep,
  currentLeg,
  stops,
  currentLegIndex,
  speedKmh,
  etaToNextStop,
  completedCount,
  insets,
  liveRoute,
  legs,
  canPreviewNext = true,
  canPreviewPrev = true,
  onOpenSettings,
  onMarkDelivered,
  onMarkFailed,
  onSkipStop,
  onStopNavigation,
  onCallCustomer,
  onShareETA,
  onShowDetails,
  onJumpToStop,
  onPreviewNextStop,
  onPreviewPrevStop,
}) => {
  const shelfHeight = useRef(new Animated.Value(SHELF_HEIGHTS[shelfState])).current;
  const cruiseOpacity = useRef(new Animated.Value(shelfState === 'CRUISE' ? 1 : 0)).current;
  const approachOpacity = useRef(new Animated.Value(shelfState === 'APPROACH' ? 1 : 0)).current;
  const arrivalOpacity = useRef(new Animated.Value(shelfState === 'ARRIVAL' ? 1 : 0)).current;

  useEffect(() => {
    Animated.spring(shelfHeight, {
      toValue: SHELF_HEIGHTS[shelfState],
      useNativeDriver: false,
      friction: 9,
      tension: 60,
    }).start();

    const dur = 180;
    Animated.timing(cruiseOpacity, { toValue: shelfState === 'CRUISE' ? 1 : 0, duration: dur, useNativeDriver: true }).start();
    Animated.timing(approachOpacity, { toValue: shelfState === 'APPROACH' ? 1 : 0, duration: dur, useNativeDriver: true }).start();
    Animated.timing(arrivalOpacity, { toValue: shelfState === 'ARRIVAL' ? 1 : 0, duration: dur, useNativeDriver: true }).start();
  }, [shelfState, shelfHeight, cruiseOpacity, approachOpacity, arrivalOpacity]);

  // ── Jump-to-stop modal ────────────────────────────────────────────────────
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

  // ── Stop metadata ─────────────────────────────────────────────────────────
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
    : navColors.blueGrad;

  const speedDisplay = settings.speedUnits === 'mph'
    ? `${Math.round(speedKmh * 0.621371)}`
    : `${speedKmh}`;
  const speedUnit = settings.speedUnits === 'mph' ? 'mph' : 'km/h';

  const maneuverIcon = currentStep
    ? (getManeuverIcon(currentStep.type, currentStep.modifier) as any)
    : 'arrow-up';

  // ── Swipe between stops ───────────────────────────────────────────────────
  const swipeX = useRef(new Animated.Value(0)).current;
  const [showTeachSwipe, setShowTeachSwipe] = useState(false);
  const teachOpacity = useRef(new Animated.Value(0)).current;
  const teachSlideLeft = useRef(new Animated.Value(-14)).current;
  const teachSlideRight = useRef(new Animated.Value(14)).current;

  useEffect(() => {
    let alive = true;
    AsyncStorage.getItem('nav_swipe_taught').then((v) => {
      if (v || !alive) return;
      const t = setTimeout(() => {
        if (!alive) return;
        setShowTeachSwipe(true);
        Animated.sequence([
          Animated.parallel([
            Animated.timing(teachOpacity, { toValue: 1, duration: 400, useNativeDriver: true }),
            Animated.timing(teachSlideLeft, { toValue: 0, duration: 400, useNativeDriver: true }),
            Animated.timing(teachSlideRight, { toValue: 0, duration: 400, useNativeDriver: true }),
          ]),
          Animated.delay(1200),
          Animated.parallel([
            Animated.timing(teachOpacity, { toValue: 0, duration: 400, useNativeDriver: true }),
            Animated.timing(teachSlideLeft, { toValue: -14, duration: 400, useNativeDriver: true }),
            Animated.timing(teachSlideRight, { toValue: 14, duration: 400, useNativeDriver: true }),
          ]),
        ]).start(() => {
          if (!alive) return;
          setShowTeachSwipe(false);
          AsyncStorage.setItem('nav_swipe_taught', '1');
        });
      }, 1500);
      return () => clearTimeout(t);
    });
    return () => { alive = false; };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const swipeResponder = useMemo(
    () => PanResponder.create({
      onMoveShouldSetPanResponder: (_e, g) =>
        Math.abs(g.dx) > 20 && Math.abs(g.dx) > Math.abs(g.dy) * 1.4,
      onPanResponderMove: (_e, g) => {
        const atEdge = (g.dx > 0 && !canPreviewPrev) || (g.dx < 0 && !canPreviewNext);
        swipeX.setValue(atEdge ? g.dx * 0.4 : g.dx);
      },
      onPanResponderRelease: (_e, g) => {
        const fastFlick = Math.abs(g.vx) > 0.4 && Math.abs(g.dx) > 20;
        const farDrag = Math.abs(g.dx) > 70;
        const committed = fastFlick || farDrag;
        if (committed && g.dx < 0 && canPreviewNext && onPreviewNextStop) {
          Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
          Animated.timing(swipeX, { toValue: -500, duration: 160, useNativeDriver: true })
            .start(() => {
              onPreviewNextStop();
              swipeX.setValue(500);
              Animated.timing(swipeX, { toValue: 0, duration: 160, useNativeDriver: true }).start();
            });
          return;
        }
        if (committed && g.dx > 0 && canPreviewPrev && onPreviewPrevStop) {
          Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
          Animated.timing(swipeX, { toValue: 500, duration: 160, useNativeDriver: true })
            .start(() => {
              onPreviewPrevStop();
              swipeX.setValue(-500);
              Animated.timing(swipeX, { toValue: 0, duration: 160, useNativeDriver: true }).start();
            });
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

  const swipeHintLeftOpacity = swipeX.interpolate({ inputRange: [0, 60], outputRange: [0, 1], extrapolate: 'clamp' });
  const swipeHintRightOpacity = swipeX.interpolate({ inputRange: [-60, 0], outputRange: [1, 0], extrapolate: 'clamp' });

  return (
    <>
      <Animated.View
        style={[styles.shelf, { height: shelfHeight, paddingBottom: insets.bottom + 4 }]}
        {...swipeResponder.panHandlers}
      >
        <View style={styles.handle} />

        {/* ── CRUISE BAR (always rendered; fades in CRUISE state) ── */}
        <Animated.View
          style={[styles.cruiseBar, { opacity: cruiseOpacity }]}
          pointerEvents={shelfState === 'CRUISE' ? 'auto' : 'none'}
        >
          <View style={styles.speedBadge}>
            <Text style={styles.speedNum}>{speedDisplay}</Text>
            <Text style={styles.speedUnit}>{speedUnit}</Text>
          </View>
          <Pressable style={styles.cruiseCenter} onLongPress={openJumpMenu} delayLongPress={400}>
            <Text style={styles.cruiseAddr} numberOfLines={1}>
              {currentStop?.address || 'Next stop'}
            </Text>
            <Text style={styles.cruiseSub} numberOfLines={1}>
              Stop {currentStopLabel || '—'} of {totalStops}
            </Text>
          </Pressable>
          {!!etaToNextStop && (
            <View style={styles.etaPill}>
              <Text style={styles.etaPillText}>{etaToNextStop}</Text>
            </View>
          )}
          <TouchableOpacity style={styles.gearBtn} onPress={onOpenSettings} hitSlop={8} testID="nav-gear-btn">
            <Ionicons name="settings-outline" size={16} color="#94a3b8" />
          </TouchableOpacity>
        </Animated.View>

        {/* ── APPROACH CONTENT ── */}
        <Animated.View
          style={[styles.approachContent, { opacity: approachOpacity }]}
          pointerEvents={shelfState === 'APPROACH' ? 'auto' : 'none'}
        >
          <View style={styles.turnCard}>
            <LinearGradient
              colors={navColors.blueGrad}
              start={{ x: 0, y: 0 }}
              end={{ x: 1, y: 1 }}
              style={styles.turnCardIcon}
            >
              <Ionicons name={maneuverIcon} size={22} color="#fff" />
            </LinearGradient>
            <View style={styles.turnCardText}>
              <Text style={styles.turnCardInstr} numberOfLines={1}>
                {currentStep?.instruction || 'Continue'}
              </Text>
              <Text style={styles.turnCardDist}>
                {currentStep?.distance ? `In ${formatDistance(currentStep.distance)}` : ''}
              </Text>
            </View>
          </View>
          <View style={styles.propStripRow}>
            <ApproachPropStrip notes={currentStop?.notes} />
            <Text style={styles.stopCounter}>{currentStopLabel || '—'} / {totalStops}</Text>
          </View>
        </Animated.View>

        {/* ── ARRIVAL CONTENT ── */}
        <Animated.View
          style={[styles.arrivalContent, { opacity: arrivalOpacity }]}
          pointerEvents={shelfState === 'ARRIVAL' ? 'auto' : 'none'}
        >
          {/* Swipe chevrons */}
          {canPreviewPrev && (
            <Animated.View style={[styles.swipeHintLeft, { opacity: swipeHintLeftOpacity }]} pointerEvents="none">
              <Ionicons name="chevron-back" size={22} color="#60a5fa" />
            </Animated.View>
          )}
          {canPreviewNext && (
            <Animated.View style={[styles.swipeHintRight, { opacity: swipeHintRightOpacity }]} pointerEvents="none">
              <Ionicons name="chevron-forward" size={22} color="#60a5fa" />
            </Animated.View>
          )}
          {showTeachSwipe && (
            <>
              <Animated.View style={[styles.swipeHintLeft, { opacity: teachOpacity, transform: [{ translateX: teachSlideLeft }] }]} pointerEvents="none">
                <Ionicons name="chevron-back" size={22} color="#60a5fa" />
              </Animated.View>
              <Animated.View style={[styles.swipeHintRight, { opacity: teachOpacity, transform: [{ translateX: teachSlideRight }] }]} pointerEvents="none">
                <Ionicons name="chevron-forward" size={22} color="#60a5fa" />
              </Animated.View>
            </>
          )}

          {/* Header row */}
          <View style={styles.arrivalHeader}>
            <Pressable style={styles.arrivalAddrBlock} onLongPress={openJumpMenu} delayLongPress={400}>
              <Text style={styles.arrivalAddr} numberOfLines={1}>
                {currentStop?.address?.split(',')[0] || 'Destination'}
              </Text>
              <Text style={styles.arrivalSuburb} numberOfLines={1}>
                {[currentStop?.suburb, currentStop?.name].filter(Boolean).join('  ·  ')}
              </Text>
            </Pressable>
            <LinearGradient
              colors={badgeGrad}
              start={{ x: 0, y: 0 }}
              end={{ x: 1, y: 1 }}
              style={styles.arrivalBadge}
            >
              <Text style={styles.arrivalBadgeText}>{currentStopLabel || '—'}</Text>
              <Text style={styles.arrivalBadgeOf}>/{totalStops}</Text>
            </LinearGradient>
          </View>

          <View style={styles.divider} />

          {/* Property card */}
          <PropertyCard stop={currentStop} colocatedInfo={colocatedInfo} />

          <View style={styles.divider} />

          {/* Quick actions */}
          <View style={styles.quickRow}>
            {currentStop?.mobile_number ? (
              <TouchableOpacity style={styles.quickBtn} onPress={onCallCustomer} testID="nav-quick-call">
                <Ionicons name="call-outline" size={13} color="#60a5fa" />
                <Text style={styles.quickLabel}>Call</Text>
              </TouchableOpacity>
            ) : null}
            <TouchableOpacity style={styles.quickBtn} onPress={onShareETA} testID="nav-quick-eta">
              <Ionicons name="share-outline" size={13} color="#60a5fa" />
              <Text style={styles.quickLabel}>Share ETA</Text>
            </TouchableOpacity>
            {onShowDetails && (
              <TouchableOpacity style={styles.quickBtn} onPress={onShowDetails} testID="nav-quick-details">
                <Ionicons name="information-circle-outline" size={13} color="#60a5fa" />
                <Text style={styles.quickLabel}>Details</Text>
              </TouchableOpacity>
            )}
          </View>

          {/* Main actions */}
          <View style={styles.actions}>
            <TouchableOpacity style={styles.failedBtn} onPress={onMarkFailed} testID="nav-main-failed">
              <Ionicons name="close" size={22} color={navColors.failedFg} />
              <Text style={styles.sideBtnLabel}>Failed</Text>
            </TouchableOpacity>

            <Pressable
              key={currentStop?.id ?? 'no-stop'}
              style={({ pressed }) => [styles.deliveredBtn, styles.deliveredHardenedHitbox, pressed && styles.deliveredPressed]}
              onStartShouldSetResponderCapture={() => true}
              hitSlop={{ top: 12, bottom: 12, left: 12, right: 12 }}
              onPressIn={() => console.log('[deliver-btn:onPressIn]')}
              onPress={() => {
                try { Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Heavy); } catch {}
                onMarkDelivered();
              }}
              testID="nav-main-delivered"
              accessibilityRole="button"
              accessibilityLabel="Mark stop as delivered"
            >
              <LinearGradient
                colors={navColors.greenGrad}
                start={{ x: 0, y: 0 }}
                end={{ x: 1, y: 1 }}
                style={styles.deliveredFill}
              >
                <Ionicons name="checkmark" size={26} color="#fff" />
                <Text style={styles.deliveredLabel}>Delivered</Text>
              </LinearGradient>
            </Pressable>

            <TouchableOpacity style={styles.skipBtn} onPress={onSkipStop} testID="nav-main-skip">
              <Ionicons name="play-skip-forward" size={22} color={navColors.skipFg} />
              <Text style={styles.sideBtnLabel}>Skip</Text>
            </TouchableOpacity>
          </View>
        </Animated.View>
      </Animated.View>

      {/* Jump-to-stop modal */}
      <Modal visible={isJumpOpen} transparent animationType="fade" onRequestClose={() => setIsJumpOpen(false)}>
        <Pressable style={styles.jumpBackdrop} onPress={() => setIsJumpOpen(false)} testID="jump-menu-backdrop">
          <Pressable style={styles.jumpCard} onPress={(e) => e.stopPropagation()}>
            <View style={styles.jumpHeader}>
              <Text style={styles.jumpTitle}>Jump to stop</Text>
              <TouchableOpacity onPress={() => setIsJumpOpen(false)} hitSlop={12}>
                <Ionicons name="close" size={22} color="#9ca3af" />
              </TouchableOpacity>
            </View>
            <ScrollView style={{ maxHeight: 360 }}>
              {(legs || []).map((lg: any, idx: number) => {
                const s = lg?.to_stop;
                if (!s) return null;
                const isCurrent = idx === currentLegIndex;
                const isDone = !!s.completed || s.delivery_status === 'delivered';
                const isFailed = s.delivery_status === 'failed';
                const isLF = stopPinNumber(s) == null && !!(s?.id && lateFreightLabels[s.id]);
                return (
                  <TouchableOpacity
                    key={`${s.id || idx}-${idx}`}
                    style={[styles.jumpRow, isCurrent && styles.jumpRowCurrent]}
                    onPress={() => handleJump(idx)}
                    testID={`jump-menu-row-${idx}`}
                  >
                    <View style={[
                      styles.jumpNum,
                      isDone && styles.jumpNumDone,
                      isFailed && styles.jumpNumFailed,
                      isCurrent && styles.jumpNumCurrent,
                      !isDone && !isFailed && !isCurrent && isLF && styles.jumpNumLF,
                    ]}>
                      <Text style={styles.jumpNumText}>
                        {stopPinNumber(s) != null ? stopPinNumber(s) : (s?.id && lateFreightLabels[s.id]) || '—'}
                      </Text>
                    </View>
                    <View style={{ flex: 1, marginLeft: 12 }}>
                      <Text style={styles.jumpName} numberOfLines={1}>{s.name || s.address || 'Unnamed stop'}</Text>
                      {!!s.address && !!s.name && (
                        <Text style={styles.jumpAddr} numberOfLines={1}>{s.address}</Text>
                      )}
                    </View>
                    {isDone && <Ionicons name="checkmark-circle" size={18} color="#22c55e" />}
                    {isFailed && <Ionicons name="close-circle" size={18} color="#ef4444" />}
                    {isCurrent && !isDone && !isFailed && <Ionicons name="radio-button-on" size={18} color="#3b82f6" />}
                  </TouchableOpacity>
                );
              })}
            </ScrollView>
          </Pressable>
        </Pressable>
      </Modal>
    </>
  );
};

const styles = StyleSheet.create({
  shelf: {
    position: 'absolute',
    bottom: 0, left: 0, right: 0,
    backgroundColor: navColors.surface,
    borderTopLeftRadius: navRadii.card,
    borderTopRightRadius: navRadii.card,
    borderTopWidth: 1,
    borderColor: navColors.hairline,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: -4 },
    shadowOpacity: 0.30,
    shadowRadius: 16,
    elevation: 12,
    zIndex: 100,
    overflow: 'hidden',
  },
  handle: {
    width: 36, height: 4, borderRadius: 2,
    backgroundColor: 'rgba(255,255,255,0.20)',
    alignSelf: 'center',
    marginTop: 8, marginBottom: 2,
  },

  // CRUISE
  cruiseBar: {
    position: 'absolute',
    top: 14, left: 0, right: 0,
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 16,
    gap: 0,
  },
  speedBadge: {
    backgroundColor: 'rgba(5, 150, 105, 0.20)',
    borderWidth: 1,
    borderColor: 'rgba(16, 185, 129, 0.30)',
    borderRadius: 12,
    paddingHorizontal: 10,
    paddingVertical: 6,
    alignItems: 'center',
    flexShrink: 0,
  },
  speedNum: { fontSize: 18, fontWeight: '800', color: '#34d399', lineHeight: 20, fontVariant: ['tabular-nums'] },
  speedUnit: { fontSize: 8, fontWeight: '600', color: '#10b981', marginTop: 0 },
  cruiseCenter: { flex: 1, paddingHorizontal: 12 },
  cruiseAddr: { fontSize: 14, fontWeight: '700', color: navColors.textPrimary },
  cruiseSub: { fontSize: 11, fontWeight: '500', color: navColors.textFaint, marginTop: 1 },
  etaPill: {
    backgroundColor: navColors.etaPillBg,
    borderRadius: navRadii.pill,
    paddingHorizontal: 10,
    paddingVertical: 4,
    marginRight: 8,
    flexShrink: 0,
  },
  etaPillText: { fontSize: 12, fontWeight: '800', color: navColors.etaPillText, fontVariant: ['tabular-nums'] },
  gearBtn: {
    width: 32, height: 32, borderRadius: 16,
    backgroundColor: navColors.ghost,
    borderWidth: 1,
    borderColor: navColors.hairline,
    justifyContent: 'center',
    alignItems: 'center',
    flexShrink: 0,
  },

  // APPROACH
  approachContent: {
    position: 'absolute',
    top: 16, left: 16, right: 16,
  },
  turnCard: {
    backgroundColor: navShelfColors.approachAccent,
    borderWidth: 1,
    borderColor: navShelfColors.approachBorder,
    borderRadius: 16,
    padding: 14,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    marginBottom: 10,
  },
  turnCardIcon: { width: 44, height: 44, borderRadius: 12, justifyContent: 'center', alignItems: 'center', flexShrink: 0 },
  turnCardText: { flex: 1 },
  turnCardInstr: { fontSize: 14, fontWeight: '700', color: '#fff' },
  turnCardDist: { fontSize: 12, color: '#93c5fd', marginTop: 2 },
  propStripRow: { flexDirection: 'row', alignItems: 'center', gap: 7, flexWrap: 'wrap' },
  propChip: {
    backgroundColor: navShelfColors.chipBg,
    borderWidth: 1,
    borderColor: navShelfColors.chipBorder,
    borderRadius: 999,
    paddingVertical: 4,
    paddingHorizontal: 9,
  },
  propChipText: { fontSize: 11, fontWeight: '600', color: '#cbd5e1' },
  stopCounter: { marginLeft: 'auto' as any, fontSize: 11, color: navColors.textFaint, fontWeight: '600' },

  // ARRIVAL
  arrivalContent: {
    position: 'absolute',
    top: 16, left: 16, right: 16,
  },
  arrivalHeader: { flexDirection: 'row', alignItems: 'flex-start', gap: 10, marginBottom: 8 },
  arrivalAddrBlock: { flex: 1, minWidth: 0 },
  arrivalAddr: { fontSize: 15, fontWeight: '800', color: navColors.textPrimary },
  arrivalSuburb: { fontSize: 11, color: navColors.textFaint, marginTop: 2 },
  arrivalBadge: { borderRadius: 10, paddingHorizontal: 10, paddingVertical: 5, flexShrink: 0, flexDirection: 'row', alignItems: 'baseline', gap: 2 },
  arrivalBadgeText: { fontSize: 13, fontWeight: '900', color: '#fff' },
  arrivalBadgeOf: { fontSize: 10, fontWeight: '600', color: 'rgba(255,255,255,0.75)' },

  divider: { height: 1, backgroundColor: navColors.divider, marginVertical: 8 },

  quickRow: { flexDirection: 'row', gap: 7, marginBottom: 10 },
  quickBtn: {
    flex: 1, height: 30,
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 4,
    backgroundColor: navColors.ghostSoft,
    borderWidth: 1, borderColor: navColors.hairline,
    borderRadius: 9,
  },
  quickLabel: { fontSize: 10, fontWeight: '600', color: navColors.textMuted },

  actions: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  failedBtn: {
    width: 64, height: 60, borderRadius: navRadii.button,
    backgroundColor: navColors.failedBg,
    borderWidth: 1, borderColor: navColors.failedBorder,
    justifyContent: 'center', alignItems: 'center', gap: 3,
  },
  skipBtn: {
    width: 64, height: 60, borderRadius: navRadii.button,
    backgroundColor: navColors.skipBg,
    borderWidth: 1, borderColor: navColors.skipBorder,
    justifyContent: 'center', alignItems: 'center', gap: 3,
  },
  sideBtnLabel: { fontSize: 10, fontWeight: '700', color: '#cbd5e1', letterSpacing: 0.3 },
  deliveredBtn: { flex: 1, height: 60, borderRadius: navRadii.buttonLg, overflow: 'hidden' },
  deliveredHardenedHitbox: { zIndex: 9999, elevation: 24, position: 'relative' },
  deliveredPressed: { opacity: 0.85, transform: [{ scale: 0.97 }] },
  deliveredFill: { flex: 1, flexDirection: 'row', justifyContent: 'center', alignItems: 'center', gap: 9 },
  deliveredLabel: { fontSize: 16, fontWeight: '800', color: '#fff', letterSpacing: 0.3 },

  swipeHintLeft: { position: 'absolute', left: 4, top: 0, bottom: 0, justifyContent: 'center', zIndex: 2 },
  swipeHintRight: { position: 'absolute', right: 4, top: 0, bottom: 0, justifyContent: 'center', zIndex: 2 },

  // Jump modal
  jumpBackdrop: { flex: 1, backgroundColor: 'rgba(0,0,0,0.5)', justifyContent: 'flex-end', padding: 16 },
  jumpCard: { backgroundColor: '#0f172a', borderRadius: 20, padding: 12, marginBottom: 20, borderWidth: 1, borderColor: navColors.hairline },
  jumpHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 4, paddingBottom: 10 },
  jumpTitle: { color: '#fff', fontSize: 15, fontWeight: '700' },
  jumpRow: { flexDirection: 'row', alignItems: 'center', paddingVertical: 11, paddingHorizontal: 4, borderRadius: 10 },
  jumpRowCurrent: { backgroundColor: 'rgba(59,130,246,0.12)' },
  jumpNum: { width: 30, height: 30, borderRadius: 15, backgroundColor: '#374151', alignItems: 'center', justifyContent: 'center' },
  jumpNumCurrent: { backgroundColor: '#3b82f6' },
  jumpNumDone: { backgroundColor: '#16a34a' },
  jumpNumFailed: { backgroundColor: '#ef4444' },
  jumpNumLF: { backgroundColor: '#7c3aed' },
  jumpNumText: { color: '#fff', fontSize: 12, fontWeight: '800' },
  jumpName: { color: '#f3f4f6', fontSize: 14, fontWeight: '600' },
  jumpAddr: { color: '#9ca3af', fontSize: 12, marginTop: 1 },
});
