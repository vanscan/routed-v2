import React from 'react';
import { Animated, View, Text, StyleSheet } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { Ionicons } from '@expo/vector-icons';
import { getManeuverIcon, formatDistance } from '../../../utils/route';
import { navColors, navRadii } from './navTheme';

interface TurnPillProps {
  currentStep: any;
  topOffset: number;
}

export const TurnPill: React.FC<TurnPillProps> = ({ currentStep, topOffset }) => {
  const maneuverIcon = currentStep
    ? (getManeuverIcon(currentStep.type, currentStep.modifier) as any)
    : 'arrow-up';

  return (
    <Animated.View
      style={[styles.container, { top: topOffset }]}
      pointerEvents="none"
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
        <Text style={styles.dist}>
          {currentStep?.distance ? formatDistance(currentStep.distance) : '--'}
        </Text>
        <Text style={styles.instr} numberOfLines={1}>
          {currentStep?.instruction || 'Continue'}
        </Text>
      </View>
    </Animated.View>
  );
};

const styles = StyleSheet.create({
  container: {
    position: 'absolute',
    alignSelf: 'center',
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    backgroundColor: navColors.surface,
    borderRadius: navRadii.pill,
    borderWidth: 1,
    borderColor: navColors.hairline,
    paddingVertical: 8,
    paddingHorizontal: 14,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.35,
    shadowRadius: 12,
    elevation: 8,
    zIndex: 40,
    maxWidth: 320,
  },
  iconTile: {
    width: 28,
    height: 28,
    borderRadius: 8,
    justifyContent: 'center',
    alignItems: 'center',
    flexShrink: 0,
  },
  textBlock: {
    flexDirection: 'row',
    alignItems: 'baseline',
    gap: 6,
    flexShrink: 1,
  },
  dist: {
    fontSize: 14,
    fontWeight: '800',
    color: '#fff',
    fontVariant: ['tabular-nums'],
  },
  instr: {
    fontSize: 13,
    fontWeight: '600',
    color: '#cbd5e1',
    flexShrink: 1,
  },
});
