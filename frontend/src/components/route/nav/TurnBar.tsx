import React, { useEffect, useMemo, useRef } from 'react';
import { Animated, View, Text, StyleSheet, PanResponder } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { Ionicons } from '@expo/vector-icons';
import * as Haptics from 'expo-haptics';
import { getManeuverIcon, formatDistance } from '../../../utils/route';
import { navColors, navShelfColors, ShelfState, RIGHT_PANEL_WIDTH } from './navTheme';

interface TurnBarProps {
  currentStep: any;
  shelfState: ShelfState;
  speedKmh: number;
  speedUnits: 'kmh' | 'mph';
  topOffset: number;
  canPreviewNext?: boolean;
  canPreviewPrev?: boolean;
  onPreviewNextStop?: () => void;
  onPreviewPrevStop?: () => void;
}

export const TurnBar: React.FC<TurnBarProps> = ({
  currentStep,
  shelfState,
  speedKmh,
  speedUnits,
  topOffset,
  canPreviewNext = true,
  canPreviewPrev = true,
  onPreviewNextStop,
  onPreviewPrevStop,
}) => {
  const barOpacity = useRef(new Animated.Value(1)).current;
  const swipeX = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    Animated.timing(barOpacity, {
      toValue: shelfState === 'ARRIVAL' ? 0.35 : 1,
      duration: 180,
      useNativeDriver: true,
    }).start();
  }, [shelfState, barOpacity]);

  const maneuverIcon = currentStep
    ? (getManeuverIcon(currentStep.type, currentStep.modifier) as any)
    : 'arrow-up';

  const speedDisplay = speedUnits === 'mph'
    ? `${Math.round(speedKmh * 0.621371)}`
    : `${speedKmh}`;
  const speedLabel = speedUnits === 'mph' ? 'mph' : 'km/h';

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
    <Animated.View
      style={[styles.bar, { top: topOffset, opacity: barOpacity }]}
      {...swipeResponder.panHandlers}
    >
      <LinearGradient
        colors={navColors.blueGrad}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 1 }}
        style={styles.iconTile}
      >
        <Ionicons name={maneuverIcon} size={18} color="#fff" />
      </LinearGradient>
      <View style={styles.textBlock}>
        <Text style={styles.dist} numberOfLines={1}>
          {currentStep?.distance ? formatDistance(currentStep.distance) : '--'}
        </Text>
        <Text style={styles.instr} numberOfLines={1}>
          {currentStep?.instruction || 'Continue'}
        </Text>
      </View>
      <View style={styles.speedTag}>
        <Text style={styles.speedNum}>{speedDisplay}</Text>
        <Text style={styles.speedUnit}>{speedLabel}</Text>
      </View>
    </Animated.View>
  );
};

const styles = StyleSheet.create({
  bar: {
    position: 'absolute',
    left: 0,
    right: RIGHT_PANEL_WIDTH,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    backgroundColor: navColors.surface,
    borderBottomWidth: 1,
    borderBottomColor: navColors.hairline,
    paddingHorizontal: 14,
    paddingVertical: 10,
    zIndex: 40,
  },
  iconTile: {
    width: 32,
    height: 32,
    borderRadius: 9,
    justifyContent: 'center',
    alignItems: 'center',
    flexShrink: 0,
  },
  textBlock: { flex: 1, gap: 2 },
  dist: {
    fontSize: 15,
    fontWeight: '800',
    color: '#fff',
    fontVariant: ['tabular-nums'],
  },
  instr: { fontSize: 11, color: '#93c5fd' },
  speedTag: {
    backgroundColor: navShelfColors.approachAccent,
    borderWidth: 1,
    borderColor: navShelfColors.approachBorder,
    borderRadius: 8,
    paddingHorizontal: 8,
    paddingVertical: 4,
    alignItems: 'center',
    flexShrink: 0,
  },
  speedNum: { fontSize: 14, fontWeight: '800', color: '#34d399' },
  speedUnit: { fontSize: 8, color: '#6ee7b7', fontWeight: '600' },
});
