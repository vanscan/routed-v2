import React, { useMemo, useRef, useState } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, ActivityIndicator, ScrollView, Animated, PanResponder, Modal, Pressable } from 'react-native';
import * as Haptics from 'expo-haptics';
import { Ionicons } from '@expo/vector-icons';
import { Stop } from '../../store/stopsStore';
import { ViewMode } from '../../types/route';
import { formatDistance, getManeuverIcon, getGeocodeMetadataEntries } from '../../utils/route';
import { stopPinNumber, buildLateFreightLabels } from '../../utils/stopPinNumber';
// SwipeToDeliver retired on 2026-05-11 per driver request — see comment
// in the Main Actions row below for the rationale and how to restore.
// Component file `./SwipeToDeliver.tsx` is intentionally kept on disk
// so a future revert is a single import line + JSX swap.

interface NavigationPanelProps {
  viewMode: ViewMode;
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
  currentMapStyle,
  cycleMapStyle,
  speedKmh,
  etaToNextStop,
  insets,
  isRerouting,
  canUndo,
  liveRoute,

  onStopNavigation,
  onMarkDelivered,
  onMarkFailed,
  onSkipStop,
  onUndoStop,
  onReroute,
  onShowRouteOverview,
  onOpenSidebar,
  onShareETA,
  onCallCustomer,
  onPreviewNextStop,
  onPreviewPrevStop,
  canPreviewNext = true,
  canPreviewPrev = true,
  legs,
  onJumpToStop,
}) => {
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
  const realStops = (stops as any[]).filter((s: any) => !s.is_current_location);
  const totalStops = realStops.length || stops.length;
  const currentStop = currentLeg?.to_stop;
  const currentStopNumber = stopPinNumber(currentStop);
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
  const isCurrentLateFreight = currentStopNumber == null &&
    !!((currentStop as any)?.id && lateFreightLabels[(currentStop as any)?.id]);
  const geocodeMetaEntries = useMemo(
    () => getGeocodeMetadataEntries(currentLeg?.to_stop?.geocode_metadata),
    [currentLeg?.to_stop?.geocode_metadata]
  );

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

  return (
    <>
      {/* Minimal Floating Turn Instruction */}
      <TouchableOpacity 
        style={[styles.immersiveTurnBanner, { top: insets.top + 8 }]}
        onPress={() => setImmersiveMode(!immersiveMode)}
        activeOpacity={0.9}
      >
        <View style={styles.immersiveTurnRow}>
          <View style={styles.immersiveTurnIconBox}>
            <Ionicons 
              name={currentStep ? getManeuverIcon(currentStep.type, currentStep.modifier) as any : 'arrow-up'} 
              size={28} 
              color="#fff" 
            />
          </View>
          <View style={styles.immersiveTurnDetails}>
            <Text style={styles.immersiveTurnDist}>
              {currentStep?.distance ? formatDistance(currentStep.distance) : '--'}
            </Text>
            <Text style={styles.immersiveTurnText} numberOfLines={1}>
              {currentStep?.instruction || 'Continue'}
            </Text>
          </View>
        </View>
        <TouchableOpacity style={styles.immersiveExitBtn} onPress={onStopNavigation}>
          <Ionicons name="close" size={22} color="#ef4444" />
        </TouchableOpacity>
      </TouchableOpacity>

      {/* Floating Speed Display */}
      <View style={[styles.immersiveSpeedDisplay, { top: insets.top + 80 }]}>
        <Text style={styles.immersiveSpeedValue}>{speedKmh}</Text>
        <Text style={styles.immersiveSpeedUnit}>km/h</Text>
      </View>

      {/* Compact Stats Row */}
      <View style={[styles.immersiveStatsRow, { top: insets.top + 80 }]}>
        <View style={styles.immersiveStatChip}>
          <Ionicons name="time-outline" size={14} color="#10b981" />
          <Text style={styles.immersiveStatText}>{etaToNextStop}</Text>
        </View>
        <View style={styles.immersiveStatChip}>
          <Ionicons name="navigate-outline" size={14} color="#3b82f6" />
          <Text style={styles.immersiveStatText}>
            {liveRoute ? formatDistance(liveRoute.distance) : '--'}
          </Text>
        </View>
      </View>

      {/* Expandable Bottom Panel */}
      {!immersiveMode ? (
        <Animated.View
          style={[
            styles.immersiveBottomFull,
            { paddingBottom: insets.bottom + 8 },
            { transform: [{ translateX: swipeX }], opacity: swipeOpacity },
          ]}
          {...swipeResponder.panHandlers}
          testID="nav-stop-card"
        >
          {canPreviewPrev && (
            <Animated.View
              style={[
                styles.swipeHintLeft,
                { opacity: swipeX.interpolate({ inputRange: [0, 60], outputRange: [0, 1], extrapolate: 'clamp' }) },
              ]}
              pointerEvents="none"
            >
              <Ionicons name="chevron-back" size={22} color="#60a5fa" />
            </Animated.View>
          )}
          {canPreviewNext && (
            <Animated.View
              style={[
                styles.swipeHintRight,
                { opacity: swipeX.interpolate({ inputRange: [-60, 0], outputRange: [1, 0], extrapolate: 'clamp' }) },
              ]}
              pointerEvents="none"
            >
              <Ionicons name="chevron-forward" size={22} color="#60a5fa" />
            </Animated.View>
          )}
          {colocatedCount > 1 && currentLeg?.to_stop && (
            <View style={styles.colocatedWarn} data-testid="nav-colocated-warn">
              <View style={styles.colocatedWarnHeader}>
                <Ionicons name="warning" size={16} color="#7c2d12" />
                <Text style={styles.colocatedWarnTitle}>
                  MULTIPLE PARCELS AT THIS ADDRESS
                </Text>
              </View>
              <View style={styles.colocatedWarnBody}>
                <Text style={styles.colocatedWarnLine}>
                  Parcel <Text style={styles.colocatedWarnBold}>{colocatedInfo.index}</Text> of{' '}
                  <Text style={styles.colocatedWarnBold}>{colocatedCount}</Text>
                  {currentLeg.to_stop.weight ? `  ·  ${currentLeg.to_stop.weight} kg` : ''}
                  {currentLeg.to_stop.id ? `  ·  #${String(currentLeg.to_stop.id).slice(0, 6)}` : ''}
                </Text>
                <View style={styles.colocatedDotsRow}>
                  {colocatedInfo.group.map((s: any, i: number) => {
                    const isCurrent = s.id === currentLeg.to_stop.id;
                    const done = !!s.completed;
                    return (
                      <View
                        key={s.id || i}
                        style={[
                          styles.colocatedDot,
                          done && styles.colocatedDotDone,
                          isCurrent && styles.colocatedDotCurrent,
                        ]}
                      />
                    );
                  })}
                </View>
              </View>
            </View>
          )}
          {/* Stop Info */}
          <View style={styles.immersiveStopRow}>
            <Pressable
              onLongPress={openJumpMenu}
              delayLongPress={400}
              style={[styles.immersiveStopBadge, isCurrentLateFreight && styles.immersiveStopBadgeLate]}
              testID="nav-stop-badge"
            >
              <Text style={styles.immersiveStopNum}>{currentStopLabel}</Text>
              <Text style={styles.immersiveStopOf}>/{totalStops}</Text>
            </Pressable>
            {colocatedCount > 1 && (
              <View style={styles.navMultiplierBadge} data-testid="nav-multiplier-badge">
                <Text style={styles.navMultiplierText}>x{colocatedCount}</Text>
              </View>
            )}
            <View style={styles.immersiveStopInfo}>
              <Text style={styles.immersiveStopName} numberOfLines={1}>
                {currentLeg?.to_stop?.name || currentLeg?.to_stop?.address?.split(',')[0] || 'Next Stop'}
              </Text>
              <Text style={styles.immersiveStopAddress} numberOfLines={1}>
                {currentLeg?.to_stop?.address || ''}
              </Text>
            </View>
            <TouchableOpacity 
              style={styles.immersiveVoiceBtn}
              onPress={onShowRouteOverview}
              testID="immersive-route-overview-toggle"
            >
              <Ionicons
                name="locate"
                size={20}
                color="#3b82f6"
              />
            </TouchableOpacity>
            <TouchableOpacity 
              style={styles.immersiveVoiceBtn}
              onPress={() => setIsVoiceEnabled(!isVoiceEnabled)}
              testID="immersive-voice-toggle"
            >
              <Ionicons 
                name={isVoiceEnabled ? "volume-high" : "volume-mute"} 
                size={20} 
                color={isVoiceEnabled ? "#3b82f6" : "#64748b"} 
              />
            </TouchableOpacity>
          </View>

          {/* Weight & Quantity Info */}
          <View style={styles.immersiveDetailsRow}>
            {currentLeg?.to_stop?.weight ? (
              <View style={styles.immersiveDetailChip}>
                <Ionicons name="cube-outline" size={14} color="#f59e0b" />
                <Text style={styles.immersiveDetailText}>{currentLeg.to_stop.weight} kg</Text>
              </View>
            ) : null}
            {currentLeg?.to_stop?.quantity ? (
              <View style={styles.immersiveDetailChip}>
                <Ionicons name="layers-outline" size={14} color="#8b5cf6" />
                <Text style={styles.immersiveDetailText}>x{currentLeg.to_stop.quantity}</Text>
              </View>
            ) : null}
          </View>

          {/* Notes */}
          {currentLeg?.to_stop?.notes ? (
            <View style={styles.immersiveNotesBox}>
              <Ionicons name="document-text-outline" size={14} color="#94a3b8" style={{ marginTop: 2 }} />
              <Text style={styles.immersiveNotesText}>{currentLeg.to_stop.notes}</Text>
            </View>
          ) : null}

          {/* Quick Actions */}
          <View style={styles.immersiveQuickRow}>
            <TouchableOpacity style={styles.immersiveQuickBtn} onPress={onCallCustomer} testID="nav-quick-call">
              <Ionicons name="call" size={18} color="#10b981" />
              <Text style={styles.immersiveQuickLabel}>Call</Text>
            </TouchableOpacity>
            <TouchableOpacity style={styles.immersiveQuickBtn} onPress={onShareETA} testID="nav-quick-share">
              <Ionicons name="share-outline" size={18} color="#3b82f6" />
              <Text style={styles.immersiveQuickLabel}>Share</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={styles.immersiveQuickBtn}
              onPress={onReroute}
              disabled={isRerouting}
              testID="nav-quick-reroute"
            >
              {isRerouting ? (
                <ActivityIndicator size="small" color="#f59e0b" />
              ) : (
                <Ionicons name="refresh" size={18} color="#f59e0b" />
              )}
              <Text style={styles.immersiveQuickLabel}>Reroute</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.immersiveQuickBtn, !canUndo && { opacity: 0.4 }]}
              onPress={onUndoStop}
              disabled={!canUndo}
              testID="nav-quick-undo"
            >
              <Ionicons name="arrow-undo" size={18} color="#8b5cf6" />
              <Text style={styles.immersiveQuickLabel}>Undo</Text>
            </TouchableOpacity>
          </View>

          {/* Main Actions */}
          <View style={styles.immersiveMainActions}>
            <TouchableOpacity style={styles.immersiveFailedBtn} onPress={onMarkFailed} testID="nav-main-failed">
              <Ionicons name="close" size={22} color="#ef4444" />
              <Text style={styles.immersiveSideBtnLabel}>Failed</Text>
            </TouchableOpacity>

            <TouchableOpacity
              key={currentStop?.id ?? 'no-stop'}
              style={styles.immersiveDeliveredBtn}
              activeOpacity={0.85}
              onPress={() => {
                try { Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Heavy); } catch {}
                console.log('[deliver-btn:onPress] invoking onMarkDelivered');
                onMarkDelivered();
              }}
              testID="nav-main-delivered"
            >
              <Ionicons name="checkmark" size={26} color="#10b981" />
              <Text style={styles.immersiveDeliveredBtnLabel}>Delivered</Text>
            </TouchableOpacity>

            <TouchableOpacity style={styles.immersiveSkipBtn} onPress={onSkipStop} testID="nav-main-skip">
              <Ionicons name="play-skip-forward" size={22} color="#f59e0b" />
              <Text style={styles.immersiveSideBtnLabel}>Skip</Text>
            </TouchableOpacity>
          </View>
        </Animated.View>
      ) : (
        <Animated.View
          style={[
            styles.immersiveBottomMinimal,
            { paddingBottom: insets.bottom + 8 },
            { transform: [{ translateX: swipeX }], opacity: swipeOpacity },
          ]}
          {...swipeResponder.panHandlers}
          testID="immersive-bottom-minimal"
        >
          <TouchableOpacity 
            style={styles.immersiveMinimalInfoExpanded}
            onPress={() => setImmersiveMode(false)}
            activeOpacity={0.8}
            testID="immersive-expand-button"
          >
            <View style={[styles.immersiveMinimalBadge, isCurrentLateFreight && styles.immersiveStopBadgeLate]}>
              <Pressable
                onLongPress={openJumpMenu}
                delayLongPress={400}
                style={StyleSheet.absoluteFill}
                testID="nav-stop-badge-minimal"
              />
              <Text style={styles.immersiveMinimalBadgeText}>{currentStopLabel}</Text>
            </View>
            {colocatedCount > 1 && (
              <View style={styles.navMultiplierBadgeSmall} data-testid="nav-multiplier-badge-minimal">
                <Text style={styles.navMultiplierTextSmall}>{colocatedInfo.index}/{colocatedCount}</Text>
              </View>
            )}
            <View style={styles.immersiveMinimalDetails}>
              <Text style={styles.immersiveMinimalName} numberOfLines={1} testID="immersive-waypoint-name">
                {currentLeg?.to_stop?.name || currentLeg?.to_stop?.address?.split(',')[0] || 'Next Stop'}
              </Text>
              <Text style={styles.immersiveMinimalAddress} numberOfLines={1} testID="immersive-waypoint-address">
                {currentLeg?.to_stop?.address || `Stop ${currentStopLabel} of ${totalStops}`}
              </Text>
            </View>
            <Ionicons name="chevron-up" size={16} color="#64748b" />
          </TouchableOpacity>

          <View style={styles.immersiveMinimalActions}>
            <Pressable
              style={({ pressed }) => [
                styles.immersiveMinimalDelivered,
                styles.deliveredHardenedHitbox,
                pressed && styles.deliveredPressed,
              ]}
              onPressIn={() => console.log('[deliver-btn:onPressIn] minimal')}
              onPress={() => {
                console.log('[deliver-btn:onPress] minimal → invoking onMarkDelivered');
                onMarkDelivered();
              }}
              onStartShouldSetResponderCapture={() => true}
              hitSlop={{ top: 12, bottom: 12, left: 12, right: 12 }}
              pointerEvents="auto"
              testID="immersive-delivered-button"
              accessibilityRole="button"
              accessibilityLabel="Mark stop as delivered"
            >
              <Ionicons name="checkmark" size={26} color="#fff" />
            </Pressable>
          </View>
        </Animated.View>
      )}

      {/* Jump-to-stop modal */}
      <Modal
        visible={isJumpOpen}
        transparent
        animationType="fade"
        onRequestClose={() => setIsJumpOpen(false)}
      >
        <Pressable
          style={styles.jumpMenuBackdrop}
          onPress={() => setIsJumpOpen(false)}
          testID="jump-menu-backdrop"
        >
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
  immersiveTurnBanner: { position: 'absolute', left: 16, right: 16, backgroundColor: '#1e293b', borderRadius: 16, padding: 12, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', zIndex: 100, shadowColor: '#000', shadowOffset: { width: 0, height: 4 }, shadowOpacity: 0.3, shadowRadius: 8, elevation: 8 },
  immersiveTurnRow: { flexDirection: 'row', alignItems: 'center', flex: 1 },
  immersiveTurnIconBox: { width: 44, height: 44, borderRadius: 12, backgroundColor: '#3b82f6', justifyContent: 'center', alignItems: 'center', marginRight: 12 },
  immersiveTurnDetails: { flex: 1 },
  immersiveTurnDist: { fontSize: 18, fontWeight: '700', color: '#fff' },
  immersiveTurnText: { fontSize: 13, color: '#94a3b8', marginTop: 2 },
  immersiveExitBtn: { width: 36, height: 36, borderRadius: 18, backgroundColor: 'rgba(239, 68, 68, 0.15)', justifyContent: 'center', alignItems: 'center', marginLeft: 8 },
  immersiveSpeedDisplay: { position: 'absolute', right: 16, backgroundColor: '#1e293b', borderRadius: 12, paddingHorizontal: 12, paddingVertical: 6, alignItems: 'center', zIndex: 99, borderWidth: 1, borderColor: 'rgba(255,255,255,0.1)' },
  immersiveSpeedValue: { fontSize: 22, fontWeight: '800', color: '#fff' },
  immersiveSpeedUnit: { fontSize: 10, color: '#64748b', marginTop: -2 },
  immersiveStatsRow: { position: 'absolute', left: 16, flexDirection: 'row', gap: 8, zIndex: 99 },
  immersiveStatChip: { flexDirection: 'row', alignItems: 'center', gap: 4, backgroundColor: 'rgba(30, 41, 59, 0.9)', paddingHorizontal: 10, paddingVertical: 6, borderRadius: 20 },
  immersiveStatText: { fontSize: 12, color: '#e2e8f0', fontWeight: '600' },
  immersiveBottomFull: { position: 'absolute', bottom: 0, left: 0, right: 0, backgroundColor: 'rgba(30, 41, 59, 0.78)', borderTopLeftRadius: 20, borderTopRightRadius: 20, paddingHorizontal: 16, paddingTop: 14, zIndex: 100 },
  immersiveStopRow: { flexDirection: 'row', alignItems: 'center', marginBottom: 10 },
  immersiveStopBadge: { width: 44, height: 44, borderRadius: 12, backgroundColor: '#3b82f6', justifyContent: 'center', alignItems: 'center', marginRight: 12 },
  immersiveStopNum: { fontSize: 18, fontWeight: '800', color: '#fff' },
  immersiveStopOf: { fontSize: 10, color: 'rgba(255,255,255,0.6)', marginTop: -4 },
  immersiveStopInfo: { flex: 1 },
  immersiveStopName: { fontSize: 16, fontWeight: '700', color: '#fff' },
  immersiveStopAddress: { fontSize: 12, color: '#94a3b8', marginTop: 2 },
  immersiveVoiceBtn: { width: 36, height: 36, borderRadius: 18, backgroundColor: 'rgba(255,255,255,0.1)', justifyContent: 'center', alignItems: 'center', marginLeft: 6 },
  immersiveDetailsRow: { flexDirection: 'row', gap: 8, marginBottom: 10, flexWrap: 'wrap' },
  immersiveDetailChip: { flexDirection: 'row', alignItems: 'center', gap: 4, backgroundColor: 'rgba(255,255,255,0.08)', paddingHorizontal: 10, paddingVertical: 6, borderRadius: 8 },
  immersiveDetailText: { fontSize: 12, color: '#cbd5e1' },
  immersiveNotesBox: { flexDirection: 'row', alignItems: 'flex-start', gap: 6, backgroundColor: 'rgba(255,255,255,0.08)', paddingHorizontal: 12, paddingVertical: 10, borderRadius: 10, marginBottom: 10 },
  immersiveNotesText: { fontSize: 13, color: '#e2e8f0', lineHeight: 18, flex: 1 },
  immersiveMetaBox: { backgroundColor: 'rgba(15, 23, 42, 0.45)', borderWidth: 1, borderColor: 'rgba(96, 165, 250, 0.25)', borderRadius: 10, padding: 10, marginBottom: 12 },
  immersiveMetaHeader: { flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 8 },
  immersiveMetaTitle: { color: '#bfdbfe', fontSize: 12, fontWeight: '700', letterSpacing: 0.3 },
  immersiveMetaList: { maxHeight: 120 },
  immersiveMetaRow: { marginBottom: 8 },
  immersiveMetaLabel: { color: '#93c5fd', fontSize: 11, fontWeight: '700' },
  immersiveMetaValue: { color: '#dbeafe', fontSize: 11, marginTop: 2 },
  immersiveQuickRow: { flexDirection: 'row', justifyContent: 'space-around', marginBottom: 10, paddingHorizontal: 4 },
  immersiveQuickBtn: { flex: 1, height: 38, borderRadius: 10, backgroundColor: 'rgba(255,255,255,0.06)', justifyContent: 'center', alignItems: 'center', gap: 1, marginHorizontal: 3, flexDirection: 'row' },
  immersiveQuickLabel: { fontSize: 11, fontWeight: '600', color: '#94a3b8', letterSpacing: 0.2, marginLeft: 6 },
  immersiveMainActions: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  immersiveSkipBtn: { width: 64, height: 56, borderRadius: 14, backgroundColor: 'rgba(245, 158, 11, 0.12)', justifyContent: 'center', alignItems: 'center', gap: 2 },
  immersiveDeliveredBtn: { flex: 1, height: 56, borderRadius: 14, backgroundColor: '#10b981', flexDirection: 'row', justifyContent: 'center', alignItems: 'center', gap: 8 },
  immersiveDeliveredBtnLabel: { fontSize: 14, fontWeight: '800', color: '#ffffff', letterSpacing: 0.3 },
  deliveredHardenedHitbox: { zIndex: 9999, elevation: 24, position: 'relative' },
  deliveredPressed: { backgroundColor: '#047857', transform: [{ scale: 0.96 }] },
  immersiveDeliveredText: { fontSize: 16, fontWeight: '700', color: '#fff' },
  immersiveFailedBtn: { width: 64, height: 56, borderRadius: 14, backgroundColor: 'rgba(239, 68, 68, 0.12)', justifyContent: 'center', alignItems: 'center', gap: 2 },
  immersiveSideBtnLabel: { fontSize: 10, fontWeight: '700', color: '#cbd5e1', letterSpacing: 0.3 },
  immersiveBottomMinimal: { position: 'absolute', bottom: 0, left: 0, right: 0, backgroundColor: 'rgba(30, 41, 59, 0.95)', flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 12, paddingVertical: 10, zIndex: 100, borderTopLeftRadius: 16, borderTopRightRadius: 16 },
  immersiveMinimalInfoExpanded: { flex: 1, flexDirection: 'row', alignItems: 'center', gap: 10, marginRight: 12 },
  immersiveMinimalBadge: { width: 36, height: 36, borderRadius: 10, backgroundColor: '#3b82f6', justifyContent: 'center', alignItems: 'center' },
  immersiveMinimalBadgeText: { fontSize: 16, fontWeight: '800', color: '#fff' },
  immersiveMinimalDetails: { flex: 1 },
  immersiveMinimalName: { fontSize: 14, fontWeight: '700', color: '#fff' },
  immersiveMinimalAddress: { fontSize: 11, color: '#94a3b8', marginTop: 1 },
  immersiveMinimalActions: { flexDirection: 'row', alignItems: 'center' },
  immersiveMinimalDelivered: { width: 52, height: 52, borderRadius: 26, backgroundColor: '#10b981', justifyContent: 'center', alignItems: 'center' },
  navMultiplierBadge: { backgroundColor: '#3b82f6', borderRadius: 8, paddingHorizontal: 7, paddingVertical: 2, marginRight: 8 },
  navMultiplierText: { color: '#fff', fontSize: 12, fontWeight: '800' },
  navMultiplierBadgeSmall: { backgroundColor: '#3b82f6', borderRadius: 6, paddingHorizontal: 5, paddingVertical: 1, marginRight: 4 },
  navMultiplierTextSmall: { color: '#fff', fontSize: 10, fontWeight: '800' },
  colocatedWarn: {
    backgroundColor: 'rgba(251, 191, 36, 0.14)',
    borderLeftWidth: 4,
    borderLeftColor: '#f59e0b',
    borderRadius: 8,
    paddingVertical: 8,
    paddingHorizontal: 10,
    marginBottom: 8,
  },
  colocatedWarnHeader: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  colocatedWarnTitle: { color: '#fbbf24', fontSize: 12, fontWeight: '900', letterSpacing: 0.4 },
  colocatedWarnBody: { marginTop: 4, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  colocatedWarnLine: { color: '#fde68a', fontSize: 13, fontWeight: '600', flexShrink: 1 },
  colocatedWarnBold: { color: '#fff', fontWeight: '900' },
  colocatedDotsRow: { flexDirection: 'row', alignItems: 'center', gap: 4, marginLeft: 8 },
  colocatedDot: { width: 8, height: 8, borderRadius: 4, backgroundColor: 'rgba(255,255,255,0.25)' },
  colocatedDotDone: { backgroundColor: '#10b981' },
  colocatedDotCurrent: { backgroundColor: '#fbbf24', width: 10, height: 10, borderRadius: 5 },
  swipeHintLeft:  { position: 'absolute', left: 6,  top: 0, bottom: 0, justifyContent: 'center', zIndex: 2 },
  swipeHintRight: { position: 'absolute', right: 6, top: 0, bottom: 0, justifyContent: 'center', zIndex: 2 },
  jumpMenuBackdrop: { flex: 1, backgroundColor: 'rgba(0,0,0,0.5)', justifyContent: 'flex-end', padding: 16 },
  jumpMenuCard: { backgroundColor: '#111827', borderRadius: 14, padding: 12, marginBottom: 20,
                  borderWidth: 1, borderColor: 'rgba(255,255,255,0.08)' },
  jumpMenuHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 4, paddingBottom: 10 },
  jumpMenuTitle: { color: '#fff', fontSize: 15, fontWeight: '700' },
  jumpMenuRow: { flexDirection: 'row', alignItems: 'center', paddingVertical: 11, paddingHorizontal: 4, borderRadius: 10 },
  jumpMenuRowCurrent: { backgroundColor: 'rgba(59,130,246,0.12)' },
  jumpMenuNum: { width: 30, height: 30, borderRadius: 15, backgroundColor: '#374151', alignItems: 'center', justifyContent: 'center' },
  jumpMenuNumCurrent:     { backgroundColor: '#3b82f6' },
  jumpMenuNumDone:        { backgroundColor: '#16a34a' },
  jumpMenuNumFailed:      { backgroundColor: '#ef4444' },
  jumpMenuNumLateFreight: { backgroundColor: '#7c3aed' },
  immersiveStopBadgeLate: { backgroundColor: '#7c3aed' },
  jumpMenuNumText: { color: '#fff', fontSize: 12, fontWeight: '800' },
  jumpMenuName: { color: '#f3f4f6', fontSize: 14, fontWeight: '600' },
  jumpMenuAddress: { color: '#9ca3af', fontSize: 12, marginTop: 1 },
});

export default NavigationPanel;
