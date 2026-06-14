import React from 'react';
import { View, Text, StyleSheet, TouchableOpacity } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { Ionicons } from '@expo/vector-icons';
import { getManeuverIcon, formatDistance } from '../../../utils/route';
import { navColors } from './navTheme';

const PILL_BG = 'rgba(10, 15, 30, 0.82)';

interface NavHeaderProps {
  currentStep: any;
  insets: { top: number };
  onOpenSettings: () => void;
}

export const NavHeader: React.FC<NavHeaderProps> = ({ currentStep, insets, onOpenSettings }) => {
  const maneuverIcon = currentStep
    ? (getManeuverIcon(currentStep.type, currentStep.modifier) as any)
    : 'arrow-up';

  return (
    <View style={[styles.pill, { top: insets.top + 8 }]}>
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
      <TouchableOpacity onPress={onOpenSettings} hitSlop={10} style={styles.gearBtn}>
        <Ionicons name="settings-outline" size={18} color={navColors.textMuted} />
      </TouchableOpacity>
    </View>
  );
};

const styles = StyleSheet.create({
  pill: {
    position: 'absolute',
    left: 16,
    right: 16,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    backgroundColor: PILL_BG,
    borderRadius: 999,
    paddingHorizontal: 14,
    paddingVertical: 10,
    zIndex: 40,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.35,
    shadowRadius: 12,
    elevation: 8,
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
    flex: 1,
    flexDirection: 'row',
    alignItems: 'baseline',
    gap: 6,
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
    flex: 1,
  },
  gearBtn: { padding: 4, flexShrink: 0 },
});
