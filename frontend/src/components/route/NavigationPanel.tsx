import React, { useEffect, useMemo, useRef, useState } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, ScrollView, Animated, PanResponder, Modal, Pressable } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import * as Haptics from 'expo-haptics';
import { Ionicons } from '@expo/vector-icons';
import { Stop } from '../../store/stopsStore';
import { ViewMode } from '../../types/route';
import { formatDistance, getManeuverIcon } from '../../utils/route';
import { stopPinNumber, buildLateFreightLabels } from '../../utils/stopPinNumber';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { navColors, navRadii } from './nav/navTheme';
// SwipeToDeliver retired on 2026-05-11 per driver request — see comment
// in the Main Actions row below for the rationale and how to restore.
// Component file `./SwipeToDeliver.tsx` is intentionally kept on disk
// so a future revert is a single import line + JSX swap.

interface NavigationPanelProps {
  viewMode: ViewMode;
  // Card visibility flag (driven by proximity in index.tsx): true = card HIDDEN
  // (pure map + header while driving), false = action card SHOWN (within 20m of
  // the stop, or manually summoned by tapping the header).
  immersiveMode: boolean;
  setImmersiveMode: (mode: boolean) => void;
  currentStep: any;
  currentLeg: any;
  stops: Stop[];
  currentLegIndex: number;
  showNotesPreview: boolean;
  setShowNotesPreview: (show: boolean) => void;
  isVoiceEnabled: boolean;
  setIsVoiceEnabled: (enabled: boolean) => void;
  currentMapStyle: string;
  cycleMapStyle: () => void;
  speedKmh: number;
  distanceToNextStop: string;
  etaToNextStop: string;
  routeStats: { distance: number; duration: number } | null;
  completedCount: number;
  insets: { top: number; bottom: number };
  isRerouting: boolean;
  canUndo: boolean;
  liveRoute: any;

  onStopNavigation: () => void;
  onMarkDelivered: () => void;
  onMarkFailed: () => void;
  onSkipStop: () => void;
  onUndoStop: () => void;
  onReroute: () => void;
  onShowRouteOverview: () => void;
  onOpenSidebar: () => void;
  onShareETA: () => void;
  onCallCustomer: () => void;
  getSuburbColor: (suburb?: string) => string;
  /** Called when the driver swipes the stop card LEFT — move to the next stop
      (card index + 1) without altering its completion state. */
  onPreviewNextStop?: () => void;
  /** Called when the driver swipes the stop card RIGHT — move to the previous
      stop (card index − 1) without altering its completion state. */
  onPreviewPrevStop?: () => void;
  /** Whether a next/prev stop exists — used to disable the swipe rubber-band
      at the ends of the route. */
  canPreviewNext?: boolean;
  canPreviewPrev?: boolean;
  /** Full list of legs from the navigation data. Used to render the
      long-press "jump to stop" menu. When omitted, the feature is disabled. */
  legs?: any[];
  /** Jump directly to stop index `i` without altering its completion state.
      Same contract as onPreviewNextStop but takes an explicit target. */
  onJumpToStop?: (index: number) => void;
  /** Open the full stop detail sheet for the current navigation stop. */
  onShowDetails?: () => void;
}

export const NavigationPanel: React.FC<NavigationPanelProps> = ({
  immersiveMode,
  setImmersiveMode,
  currentStep,
  currentLeg,
  stops,
  currentLegIndex,
  isVoiceEnabled,
  setIsVoiceEnabled,
  speedKmh,
  etaToNextStop,
  insets,
  liveRoute,
  completedCount,

  onStopNavigation,
  onMarkDelivered,
  onMarkFailed,
  onSkipStop,
  onShowRouteOverview,
  onShareETA,
  onCallCustomer,
  onPreviewNextStop,
  onPreviewPrevStop,
  canPreviewNext = true,
  canPreviewPrev = true,
  legs,
  onJumpToStop,
  onShowDetails,
}) => {
  // Long-press-to-jump menu — opened by holding the big stop-number badge.
  // Gives drivers a way to teleport to any stop without swiping through each one.
  const [isJumpOpen, setIsJumpOpen] = useState(false);
  const [showTeachSwipe, setShowTeachSwipe] = useState(false);
  const teachOpacity = useRef(new Animated.Value(0)).current;
  // Slide offsets for the swipe-hint chevrons — left starts off-left, right
  // starts off-right. Both animate to 0 in parallel with the opacity fade so
  // the chevrons appear to slide in from the edges.
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
          // Fade + slide in simultaneously
          Animated.parallel([
            Animated.timing(teachOpacity, { toValue: 1, duration: 400, useNativeDriver: true }),
            Animated.timing(teachSlideLeft, { toValue: 0, duration: 400, useNativeDriver: true }),
            Animated.timing(teachSlideRight, { toValue: 0, duration: 400, useNativeDriver: true }),
          ]),
          Animated.delay(1200),
          // Fade + slide out simultaneously
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
  const realStops = (stops as any[]).filter((s: any) => !s.is_current_location);
  const totalStops = realStops.length || stops.length;
  const currentStop = currentLeg?.to_stop;
  // Locked Sharpie badge first, then backend planning order. NEVER falls back
  // to the array index — those reshuffle on re-optimise and the badge must
  // stay welded to the physical box. Returns null when the stop has no
  // numeric identity (rare; pre-hydration only).
  const currentStopNumber = stopPinNumber(currentStop);
  // Late-freight stops have no locked `original_sequence`, so `stopPinNumber`
  // returns null. Resolve a human label ("45A", "45B" …) anchored to the
  // nearest preceding locked stop so the driver sees a consistent badge.
  const lateFreightLabels = useMemo(
    () => buildLateFreightLabels(stops as any),
    [stops],
  );
  const currentStopLabel = useMemo(() => {
    if (currentStopNumber != null) return String(currentStopNumber);
    const id = (currentStop as any)?.id;
    if (id && lateFreightLabels[id]) return lateFreightLabels[id];
    return '';
  }, [currentStopNumber, currentStop, lateFreightLabels]);
  // True when the CURRENT stop is a late-freight parcel — colours the badge
  // purple to match the map pin + sidebar.
  const isCurrentLateFreight = currentStopNumber == null &&
    !!((currentStop as any)?.id && lateFreightLabels[(currentStop as any)?.id]);

  // Identify all stops sharing the current stop's coordinates so we can warn
  // the driver about multiple parcels at one doorstep (they used to deliver
  // one and drive off, leaving the rest). Returns the whole group in route
  // order plus the current parcel's position + delivered progress.
  const colocatedInfo = useMemo(() => {
    const cur = currentLeg?.to_stop;
    if (!cur) return { count: 1, index: 1, doneCount: 0, group: [] as any[] };
    const key = `${Number(cur.latitude).toFixed(5)},${Number(cur.longitude).toFixed(5)}`;
    const group = realStops.filter(
      (s: any) =>
        `${Number(s.latitude).toFixed(5)},${Number(s.longitude).toFixed(5)}` === key
    );
    const index = Math.max(1, group.findIndex((s: any) => s.id === cur.id) + 1);
    const doneCount = group.filter((s: any) => s.completed).length;
    return { count: group.length || 1, index, doneCount, group };
  }, [currentLeg?.to_stop, realStops]);
  const colocatedCount = colocatedInfo.count;

  // Header meta line: suburb · customer · weight (only the parts that exist).
  const headerMeta = useMemo(() => {
    const s: any = currentLeg?.to_stop;
    if (!s) return '';
    const parts: string[] = [];
    if (s.suburb) parts.push(String(s.suburb));
    if (s.name) parts.push(String(s.name));
    if (s.weight) parts.push(`${s.weight} kg`);
    return parts.join('  ·  ');
  }, [currentLeg?.to_stop]);

  // ── Horizontal swipe between stops (preview-only, no completion side-effects)
  //   swipe LEFT  → onPreviewNextStop, swipe RIGHT → onPreviewPrevStop.
  //   PanResponder (no extra lib), 20px threshold + 1.4× horizontal ratio gate
  //   so vertical scrolls and jittery taps reach the inner buttons.
  const swipeX = useRef(new Animated.Value(0)).current;
  const swipeResponder = useMemo(
    () => PanResponder.create({
      onMoveShouldSetPanResponder: (_e, g) =>
        Math.abs(g.dx) > 20 && Math.abs(g.dx) > Math.abs(g.dy) * 1.4,
      onPanResponderMove: (_e, g) => {
        const atEdge =
          (g.dx > 0 && !canPreviewPrev) || (g.dx < 0 && !canPreviewNext);
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
        if (committed) {
          Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
        }
        Animated.spring(swipeX, { toValue: 0, useNativeDriver: true, bounciness: 6 }).start();
      },
      onPanResponderTerminate: () => {
        Animated.spring(swipeX, { toValue: 0, useNativeDriver: true }).start();
      },
    }),
    [swipeX, canPreviewNext, canPreviewPrev, onPreviewNextStop, onPreviewPrevStop],
  );

  const swipeOpacity = swipeX.interpolate({
    inputRange: [-200, 0, 200],
    outputRange: [0.6, 1, 0.6],
    extrapolate: 'clamp',
  });

  // Card show/hide slide. cardAnim 0 = off-screen-below/transparent, 1 = resting.
  // Runs when the proximity flag flips the card on (immersiveMode → false).
  const cardAnim = useRef(new Animated.Value(immersiveMode ? 0 : 1)).current;
  useEffect(() => {
    if (!immersiveMode) {
      cardAnim.setValue(0);
      Animated.spring(cardAnim, { toValue: 1, useNativeDriver: true, friction: 8, tension: 65 }).start();
    }
  }, [immersiveMode, cardAnim]);
  const cardTranslateY = cardAnim.interpolate({ inputRange: [0, 1], outputRange: [64, 0] });
  const cardOpacity = Animated.multiply(swipeOpacity, cardAnim);

  const maneuverIcon = currentStep
    ? (getManeuverIcon(currentStep.type, currentStep.modifier) as any)
    : 'arrow-up';
  const badgeGrad = isCurrentLateFreight
    ? (['#7c3aed', '#a855f7'] as const)
    : navColors.blueGrad;

  return (
    <>
      {/* ── Unified navigation header — STOP is the hero, turn is the strip ──── */}
      <View style={[styles.header, { top: insets.top + 8 }]}>
        {/* Row 1 — the stop. Tapping the row toggles the action card. */}
        <TouchableOpacity
          style={styles.row1}
          activeOpacity={0.85}
          onPress={() => setImmersiveMode(!immersiveMode)}
          testID="nav-header-row1"
        >
          <Pressable
            onLongPress={openJumpMenu}
            delayLongPress={400}
            onStartShouldSetResponderCapture={() => true}
            testID="nav-bar-stop-badge"
          >
            <LinearGradient
              colors={badgeGrad}
              start={{ x: 0, y: 0 }}
              end={{ x: 1, y: 1 }}
              style={styles.heroBadge}
            >
              <Text style={styles.heroNum}>{currentStopLabel || '—'}</Text>
              <Text style={styles.heroOf}>of {totalStops}</Text>
            </LinearGradient>
          </Pressable>
          <View style={styles.headerCenter}>
            <Text style={styles.headerAddr} numberOfLines={2}>
              {currentLeg?.to_stop?.address || 'Next Stop'}
            </Text>
            {!!headerMeta && (
              <Text style={styles.headerMeta} numberOfLines={1}>{headerMeta}</Text>
            )}
          </View>
          <View style={styles.headerRight}>
            <TouchableOpacity
              style={styles.headerIconBtn}
              onPress={() => setIsVoiceEnabled(!isVoiceEnabled)}
              onStartShouldSetResponderCapture={() => true}
              hitSlop={8}
              testID="nav-voice-btn"
            >
              <Ionicons
                name={isVoiceEnabled ? 'volume-high' : 'volume-mute'}
                size={18}
                color={isVoiceEnabled ? '#10b981' : '#94a3b8'}
              />
            </TouchableOpacity>
            <TouchableOpacity
              style={styles.headerExitBtn}
              onPress={onStopNavigation}
              onStartShouldSetResponderCapture={() => true}
              hitSlop={8}
              testID="nav-exit-btn"
            >
              <Ionicons name="close" size={18} color={navColors.failedFg} />
            </TouchableOpacity>
          </View>
        </TouchableOpacity>

        {/* Row 2 — compact turn strip + ETA + speed. */}
        <View style={styles.row2}>
          <LinearGradient
            colors={navColors.blueGrad}
            start={{ x: 0, y: 0 }}
            end={{ x: 1, y: 1 }}
            style={styles.maneuverTileSm}
          >
            <Ionicons name={maneuverIcon} size={20} color="#fff" />
          </LinearGradient>
          <View style={styles.turnInfo}>
            <Text style={styles.turnDist}>
              {currentStep?.distance ? formatDistance(currentStep.distance) : '--'}
            </Text>
            <Text style={styles.turnInstr} numberOfLines={1}>
              {currentStep?.instruction || 'Continue'}
            </Text>
          </View>
          {!!etaToNextStop && (
            <View style={styles.etaPill}>
              <Text style={styles.etaPillText}>{etaToNextStop}</Text>
            </View>
          )}
          <Text style={styles.speedText}>
            {speedKmh}
            <Text style={styles.speedUnit}> km/h</Text>
          </Text>
        </View>
      </View>

      {/* ── Action card — appears only within 20m of the stop (or on header tap),
            auto-hides 20m past or on completion. No bottom bar while driving. ── */}
      {!immersiveMode && (
        <Animated.View
          style={[
            styles.card,
            { paddingBottom: insets.bottom + 8 },
            { transform: [{ translateX: swipeX }, { translateY: cardTranslateY }], opacity: cardOpacity },
          ]}
          {...swipeResponder.panHandlers}
          testID="nav-stop-card"
        >
          {/* Swipe-hint chevrons — fade in while dragging. */}
          {canPreviewPrev && (
            <Animated.View
              style={[styles.swipeHintLeft, { opacity: swipeX.interpolate({ inputRange: [0, 60], outputRange: [0, 1], extrapolate: 'clamp' }) }]}
              pointerEvents="none"
            >
              <Ionicons name="chevron-back" size={22} color="#60a5fa" />
            </Animated.View>
          )}
          {canPreviewNext && (
            <Animated.View
              style={[styles.swipeHintRight, { opacity: swipeX.interpolate({ inputRange: [-60, 0], outputRange: [1, 0], extrapolate: 'clamp' }) }]}
              pointerEvents="none"
            >
              <Ionicons name="chevron-forward" size={22} color="#60a5fa" />
            </Animated.View>
          )}
          {/* One-time swipe teach hint. */}
          {showTeachSwipe && (
            <>
              <Animated.View
                style={[styles.swipeHintLeft, { opacity: teachOpacity, transform: [{ translateX: teachSlideLeft }] }]}
                pointerEvents="none"
              >
                <Ionicons name="chevron-back" size={22} color="#60a5fa" />
              </Animated.View>
              <Animated.View
                style={[styles.swipeHintRight, { opacity: teachOpacity, transform: [{ translateX: teachSlideRight }] }]}
                pointerEvents="none"
              >
                <Ionicons name="chevron-forward" size={22} color="#60a5fa" />
              </Animated.View>
            </>
          )}

          {/* Multi-parcel warning — impossible to miss. */}
          {colocatedCount > 1 && currentLeg?.to_stop && (
            <View style={styles.warnBanner} data-testid="nav-colocated-warn">
              <View style={styles.warnHeader}>
                <Ionicons name="warning" size={15} color={navColors.warnTitle} />
                <Text style={styles.warnTitle}>MULTIPLE PARCELS AT THIS ADDRESS</Text>
              </View>
              <View style={styles.warnBody}>
                <Text style={styles.warnLine}>
                  Parcel <Text style={styles.warnBold}>{colocatedInfo.index}</Text> of{' '}
                  <Text style={styles.warnBold}>{colocatedCount}</Text>
                  {currentLeg.to_stop.weight ? `  ·  ${currentLeg.to_stop.weight} kg` : ''}
                </Text>
                <View style={styles.dotsRow}>
                  {colocatedInfo.group.map((s: any, i: number) => {
                    const isCurrent = s.id === currentLeg.to_stop.id;
                    const done = !!s.completed;
                    return (
                      <View
                        key={s.id || i}
                        style={[styles.dot, done && styles.dotDone, isCurrent && styles.dotCurrent]}
                      />
                    );
                  })}
                </View>
              </View>
            </View>
          )}

          {/* Customer + meta + dismiss */}
          <View style={styles.cardHeaderRow}>
            <View style={styles.cardHeaderText}>
              <Text style={styles.cardCustomer} numberOfLines={1}>
                {currentLeg?.to_stop?.name || currentLeg?.to_stop?.address?.split(',')[0] || 'Next Stop'}
              </Text>
              <Text style={styles.cardMeta} numberOfLines={1}>
                {[
                  currentLeg?.to_stop?.weight ? `${currentLeg.to_stop.weight} kg` : null,
                  `${completedCount}/${totalStops} stops`,
                  liveRoute?.distance ? formatDistance(liveRoute.distance) : null,
                ].filter(Boolean).join('  ·  ')}
              </Text>
            </View>
            <TouchableOpacity
              style={styles.dismissBtn}
              onPress={() => {
                if (onShowDetails) onShowDetails();
                else setImmersiveMode(true);
              }}
              onStartShouldSetResponderCapture={() => true}
              testID="nav-more-btn"
            >
              <Ionicons name="chevron-down" size={18} color="#cbd5e1" />
            </TouchableOpacity>
          </View>

          {/* Notes */}
          {currentLeg?.to_stop?.notes ? (
            <View style={styles.notesBox}>
              <Ionicons name="document-text-outline" size={13} color="#94a3b8" style={{ marginTop: 1 }} />
              <Text style={styles.notesText} numberOfLines={2}>{currentLeg.to_stop.notes}</Text>
            </View>
          ) : null}

          {/* Quick actions: Call (if phone) / Share ETA / Overview */}
          <View style={styles.quickRow}>
            {currentLeg?.to_stop?.mobile_number ? (
              <TouchableOpacity style={styles.quickBtn} onPress={onCallCustomer} onStartShouldSetResponderCapture={() => true} testID="nav-quick-call">
                <Ionicons name="call-outline" size={14} color="#60a5fa" />
                <Text style={styles.quickLabel}>Call</Text>
              </TouchableOpacity>
            ) : null}
            <TouchableOpacity style={styles.quickBtn} onPress={onShareETA} onStartShouldSetResponderCapture={() => true} testID="nav-quick-eta">
              <Ionicons name="share-outline" size={14} color="#60a5fa" />
              <Text style={styles.quickLabel}>Share ETA</Text>
            </TouchableOpacity>
            <TouchableOpacity style={styles.quickBtn} onPress={onShowRouteOverview} onStartShouldSetResponderCapture={() => true} testID="nav-quick-overview">
              <Ionicons name="map-outline" size={14} color="#60a5fa" />
              <Text style={styles.quickLabel}>Overview</Text>
            </TouchableOpacity>
          </View>

          {/* Main Actions — Failed | Delivered | Skip.
              2026-05-11 reverted from slide-to-deliver to tap buttons per driver
              feedback; SwipeToDeliver.tsx kept on disk for a one-line restore.
              The Delivered button carries the hardened hitbox (zIndex/elevation +
              responder capture) since it's the only Delivered control now. */}
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
              onPressIn={() => console.log('[deliver-btn:onPressIn] full')}
              onPress={() => {
                try { Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Heavy); } catch {}
                console.log('[deliver-btn:onPress] full → invoking onMarkDelivered');
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
      )}

      {/* Jump-to-stop menu — long-press the stop badge. */}
      <Modal
        visible={isJumpOpen}
        transparent
        animationType="fade"
        onRequestClose={() => setIsJumpOpen(false)}
      >
        <Pressable style={styles.jumpMenuBackdrop} onPress={() => setIsJumpOpen(false)} testID="jump-menu-backdrop">
          <Pressable style={styles.jumpMenuCard} onPress={(e) => e.stopPropagation()}>
            <View style={styles.jumpMenuHeader}>
              <Text style={styles.jumpMenuTitle}>Jump to stop</Text>
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
                return (
                  <TouchableOpacity
                    key={`${s.id || idx}-${idx}`}
                    style={[styles.jumpMenuRow, isCurrent && styles.jumpMenuRowCurrent]}
                    onPress={() => handleJump(idx)}
                    testID={`jump-menu-row-${idx}`}
                  >
                    <View style={[
                      styles.jumpMenuNum,
                      isDone && styles.jumpMenuNumDone,
                      isFailed && styles.jumpMenuNumFailed,
                      isCurrent && styles.jumpMenuNumCurrent,
                      !isDone && !isFailed && !isCurrent && stopPinNumber(s) == null && !!(s?.id && lateFreightLabels[s.id]) && styles.jumpMenuNumLateFreight,
                    ]}>
                      <Text style={styles.jumpMenuNumText}>{stopPinNumber(s) != null ? stopPinNumber(s) : (s?.id && lateFreightLabels[s.id]) || '—'}</Text>
                    </View>
                    <View style={{ flex: 1, marginLeft: 12 }}>
                      <Text style={styles.jumpMenuName} numberOfLines={1}>
                        {s.name || s.address || 'Unnamed stop'}
                      </Text>
                      {!!s.address && !!s.name && (
                        <Text style={styles.jumpMenuAddress} numberOfLines={1}>{s.address}</Text>
                      )}
                    </View>
                    {isDone && <Ionicons name="checkmark-circle" size={18} color="#22c55e" />}
                    {isFailed && <Ionicons name="close-circle" size={18} color="#ef4444" />}
                    {isCurrent && !isDone && !isFailed && (
                      <Ionicons name="radio-button-on" size={18} color="#3b82f6" />
                    )}
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
  // ── Unified header ──────────────────────────────────────────────────────
  header: {
    position: 'absolute', left: 12, right: 12, zIndex: 100,
    backgroundColor: navColors.surface, borderRadius: navRadii.header,
    borderWidth: 1, borderColor: navColors.hairline,
    shadowColor: '#000', shadowOffset: { width: 0, height: 8 }, shadowOpacity: 0.35, shadowRadius: 24, elevation: 10,
    overflow: 'hidden',
  },
  row1: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 12, paddingTop: 13, paddingBottom: 11 },
  heroBadge: { width: 54, height: 54, borderRadius: navRadii.button, justifyContent: 'center', alignItems: 'center' },
  heroNum: { color: '#fff', fontWeight: '900', fontSize: 20, lineHeight: 22 },
  heroOf: { color: 'rgba(255,255,255,0.78)', fontWeight: '700', fontSize: 9, marginTop: 1 },
  headerCenter: { flex: 1, minWidth: 0 },
  headerAddr: { fontSize: 19, fontWeight: '800', color: navColors.textPrimary, lineHeight: 23, letterSpacing: -0.2 },
  headerMeta: { fontSize: 12, fontWeight: '600', color: navColors.textMuted, marginTop: 3 },
  headerRight: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  headerIconBtn: { width: 32, height: 32, borderRadius: 16, backgroundColor: navColors.ghost, justifyContent: 'center', alignItems: 'center' },
  headerExitBtn: { width: 32, height: 32, borderRadius: 16, backgroundColor: 'rgba(239,68,68,0.18)', justifyContent: 'center', alignItems: 'center' },
  row2: { flexDirection: 'row', alignItems: 'center', gap: 9, paddingHorizontal: 12, paddingVertical: 9, borderTopWidth: 1, borderTopColor: navColors.divider },
  maneuverTileSm: { width: 32, height: 32, borderRadius: navRadii.tileSm, justifyContent: 'center', alignItems: 'center' },
  turnInfo: { flex: 1, minWidth: 0, flexDirection: 'row', alignItems: 'baseline', gap: 7 },
  turnDist: { fontSize: 16, fontWeight: '800', color: navColors.textPrimary, fontVariant: ['tabular-nums'] },
  turnInstr: { flex: 1, fontSize: 13, fontWeight: '600', color: '#cbd5e1' },
  etaPill: { backgroundColor: navColors.etaPillBg, borderRadius: navRadii.pill, paddingHorizontal: 10, paddingVertical: 3 },
  etaPillText: { fontSize: 12, fontWeight: '800', color: navColors.etaPillText, fontVariant: ['tabular-nums'] },
  speedText: { fontSize: 12, fontWeight: '800', color: navColors.textPrimary, fontVariant: ['tabular-nums'] },
  speedUnit: { fontSize: 9, fontWeight: '600', color: navColors.textFaint },

  // ── Action card ─────────────────────────────────────────────────────────
  card: {
    position: 'absolute', bottom: 0, left: 0, right: 0, zIndex: 100,
    backgroundColor: navColors.surface,
    borderTopLeftRadius: navRadii.card, borderTopRightRadius: navRadii.card,
    borderTopWidth: 1, borderColor: navColors.hairline,
    paddingHorizontal: 16, paddingTop: 16,
    shadowColor: '#000', shadowOffset: { width: 0, height: -4 }, shadowOpacity: 0.30, shadowRadius: 16, elevation: 12,
  },
  warnBanner: { backgroundColor: navColors.warnBg, borderWidth: 1, borderColor: navColors.warnBorder, borderRadius: 14, paddingVertical: 10, paddingHorizontal: 12, marginBottom: 12 },
  warnHeader: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  warnTitle: { color: navColors.warnTitle, fontSize: 11, fontWeight: '900', letterSpacing: 0.5 },
  warnBody: { marginTop: 4, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  warnLine: { color: navColors.warnBody, fontSize: 13, fontWeight: '600', flexShrink: 1 },
  warnBold: { color: '#fff', fontWeight: '900' },
  dotsRow: { flexDirection: 'row', alignItems: 'center', gap: 5, marginLeft: 8 },
  dot: { width: 8, height: 8, borderRadius: 4, backgroundColor: 'rgba(255,255,255,0.25)' },
  dotDone: { backgroundColor: '#10b981' },
  dotCurrent: { backgroundColor: '#fbbf24', width: 10, height: 10, borderRadius: 5 },
  cardHeaderRow: { flexDirection: 'row', alignItems: 'flex-start', gap: 10 },
  cardHeaderText: { flex: 1, minWidth: 0 },
  cardCustomer: { fontSize: 16, fontWeight: '800', color: navColors.textPrimary, lineHeight: 20 },
  cardMeta: { fontSize: 12, fontWeight: '600', color: navColors.textMuted, marginTop: 3 },
  dismissBtn: { width: 32, height: 32, borderRadius: 16, backgroundColor: navColors.ghost, justifyContent: 'center', alignItems: 'center' },
  notesBox: { flexDirection: 'row', alignItems: 'flex-start', gap: 7, backgroundColor: navColors.ghostSoft, borderRadius: 12, paddingHorizontal: 11, paddingVertical: 8, marginTop: 10 },
  notesText: { fontSize: 12, color: '#cbd5e1', flex: 1, lineHeight: 17 },
  quickRow: { flexDirection: 'row', gap: 7, marginTop: 10 },
  quickBtn: { flex: 1, height: 32, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 4, backgroundColor: navColors.ghostSoft, borderWidth: 1, borderColor: navColors.hairline, borderRadius: 9 },
  quickLabel: { fontSize: 10, fontWeight: '600', color: navColors.textMuted },
  actions: { flexDirection: 'row', alignItems: 'center', gap: 10, marginTop: 14 },
  failedBtn: { width: 64, height: 60, borderRadius: navRadii.button, backgroundColor: navColors.failedBg, borderWidth: 1, borderColor: navColors.failedBorder, justifyContent: 'center', alignItems: 'center', gap: 3 },
  skipBtn: { width: 64, height: 60, borderRadius: navRadii.button, backgroundColor: navColors.skipBg, borderWidth: 1, borderColor: navColors.skipBorder, justifyContent: 'center', alignItems: 'center', gap: 3 },
  sideBtnLabel: { fontSize: 10, fontWeight: '700', color: '#cbd5e1', letterSpacing: 0.3 },
  deliveredBtn: { flex: 1, height: 60, borderRadius: navRadii.buttonLg, overflow: 'hidden' },
  deliveredFill: { flex: 1, flexDirection: 'row', justifyContent: 'center', alignItems: 'center', gap: 9 },
  deliveredLabel: { fontSize: 16, fontWeight: '800', color: '#fff', letterSpacing: 0.3 },
  // Defensive layering — lifts the Delivered tap target above any invisible
  // overlay sibling (gesture-tracking Animated.View, debug/perf overlays) that
  // could intercept touches. zIndex (iOS) + elevation (Android) both raised.
  deliveredHardenedHitbox: { zIndex: 9999, elevation: 24, position: 'relative' },
  deliveredPressed: { opacity: 0.85, transform: [{ scale: 0.97 }] },

  // ── Swipe-hint chevrons ───────────────────────────────────────────────────
  swipeHintLeft: { position: 'absolute', left: 6, top: 0, bottom: 0, justifyContent: 'center', zIndex: 2 },
  swipeHintRight: { position: 'absolute', right: 6, top: 0, bottom: 0, justifyContent: 'center', zIndex: 2 },

  // ── Jump-to-stop modal ────────────────────────────────────────────────────
  jumpMenuBackdrop: { flex: 1, backgroundColor: 'rgba(0,0,0,0.5)', justifyContent: 'flex-end', padding: 16 },
  jumpMenuCard: { backgroundColor: '#0f172a', borderRadius: 20, padding: 12, marginBottom: 20, borderWidth: 1, borderColor: navColors.hairline },
  jumpMenuHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 4, paddingBottom: 10 },
  jumpMenuTitle: { color: '#fff', fontSize: 15, fontWeight: '700' },
  jumpMenuRow: { flexDirection: 'row', alignItems: 'center', paddingVertical: 11, paddingHorizontal: 4, borderRadius: 10 },
  jumpMenuRowCurrent: { backgroundColor: 'rgba(59,130,246,0.12)' },
  jumpMenuNum: { width: 30, height: 30, borderRadius: 15, backgroundColor: '#374151', alignItems: 'center', justifyContent: 'center' },
  jumpMenuNumCurrent: { backgroundColor: '#3b82f6' },
  jumpMenuNumDone: { backgroundColor: '#16a34a' },
  jumpMenuNumFailed: { backgroundColor: '#ef4444' },
  jumpMenuNumLateFreight: { backgroundColor: '#7c3aed' },
  jumpMenuNumText: { color: '#fff', fontSize: 12, fontWeight: '800' },
  jumpMenuName: { color: '#f3f4f6', fontSize: 14, fontWeight: '600' },
  jumpMenuAddress: { color: '#9ca3af', fontSize: 12, marginTop: 1 },
});

export default NavigationPanel;
