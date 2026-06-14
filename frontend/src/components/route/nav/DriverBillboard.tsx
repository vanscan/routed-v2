import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { Ionicons } from '@expo/vector-icons';
import { navColors } from './navTheme';
import { parseStopNotes } from './parseStopNotes';
import { formatDistance, getManeuverIcon } from '../../../utils/route';

interface DriverBillboardProps {
  stop: any | null;
  currentStep?: any | null;
  insets: { top: number };
}

export const DriverBillboard: React.FC<DriverBillboardProps> = ({ stop, currentStep, insets }) => {
  const fullAddress: string = stop?.address || '';
  const commaIdx = fullAddress.indexOf(',');
  const streetLine = commaIdx > -1 ? fullAddress.slice(0, commaIdx) : fullAddress;
  const suburbLine = commaIdx > -1 ? fullAddress.slice(commaIdx + 1).trim() : '';

  const parsed = parseStopNotes(stop?.notes);
  const noteChips = [parsed.propertyType, parsed.safePlace, parsed.freeText]
    .filter(Boolean)
    .join('  •  ');

  const maneuverIcon = currentStep
    ? (getManeuverIcon(currentStep.type, currentStep.modifier) as any)
    : 'arrow-up';

  return (
    <View style={[styles.container, { paddingTop: insets.top + 8 }]}>
      <Text style={styles.street} numberOfLines={1}>
        {streetLine || 'Loading…'}
      </Text>
      {!!suburbLine && (
        <Text style={styles.suburb} numberOfLines={1}>{suburbLine}</Text>
      )}
      {!!noteChips && (
        <Text style={styles.notes} numberOfLines={1}>{noteChips}</Text>
      )}
      {currentStep && (
        <View style={styles.turnRow}>
          <LinearGradient
            colors={navColors.blueGrad}
            start={{ x: 0, y: 0 }}
            end={{ x: 1, y: 1 }}
            style={styles.turnIcon}
          >
            <Ionicons name={maneuverIcon} size={12} color="#fff" />
          </LinearGradient>
          <Text style={styles.turnText} numberOfLines={1}>
            {currentStep.distance
              ? `${formatDistance(currentStep.distance)} — `
              : ''}
            {currentStep.instruction || 'Continue'}
          </Text>
        </View>
      )}
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    zIndex: 40,
    backgroundColor: 'rgba(5, 10, 24, 0.88)',
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(255,255,255,0.07)',
    paddingHorizontal: 18,
    paddingBottom: 12,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.4,
    shadowRadius: 12,
    elevation: 10,
  },
  street: {
    fontSize: 26,
    fontWeight: '800',
    color: '#fff',
    letterSpacing: -0.4,
    lineHeight: 32,
  },
  suburb: {
    fontSize: 13,
    fontWeight: '500',
    color: navColors.textMuted,
    marginTop: 1,
  },
  notes: {
    fontSize: 14,
    fontWeight: '600',
    color: '#f59e0b',
    marginTop: 4,
  },
  turnRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginTop: 10,
    backgroundColor: 'rgba(255,255,255,0.07)',
    borderRadius: 999,
    alignSelf: 'flex-start',
    paddingRight: 12,
    paddingLeft: 4,
    paddingVertical: 4,
    maxWidth: '100%',
  },
  turnIcon: {
    width: 22,
    height: 22,
    borderRadius: 11,
    justifyContent: 'center',
    alignItems: 'center',
    flexShrink: 0,
  },
  turnText: {
    fontSize: 12,
    fontWeight: '600',
    color: 'rgba(255,255,255,0.80)',
    flexShrink: 1,
  },
});
