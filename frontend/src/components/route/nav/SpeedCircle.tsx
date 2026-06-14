import React from 'react';
import { View, Text, StyleSheet } from 'react-native';

const ALERT_KMH = 110;
const PILL_BG = 'rgba(10, 15, 30, 0.82)';
const ALERT_COLOR = '#ef4444';

interface SpeedCircleProps {
  speedKmh: number;
  units: 'kmh' | 'mph';
  bottom: number;
}

export const SpeedCircle: React.FC<SpeedCircleProps> = ({ speedKmh, units, bottom }) => {
  const isFast = speedKmh > ALERT_KMH;
  const displaySpeed = units === 'mph' ? Math.round(speedKmh * 0.621371) : speedKmh;
  const unitLabel = units === 'mph' ? 'mph' : 'km/h';

  return (
    <View style={[styles.circle, { bottom }, isFast && styles.circleAlert]}>
      <Text style={[styles.num, isFast && styles.textAlert]}>{displaySpeed}</Text>
      <Text style={[styles.unit, isFast && styles.unitAlert]}>{unitLabel}</Text>
    </View>
  );
};

const styles = StyleSheet.create({
  circle: {
    position: 'absolute',
    left: 16,
    width: 52,
    height: 52,
    borderRadius: 26,
    borderWidth: 3,
    borderColor: '#fff',
    backgroundColor: PILL_BG,
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: 40,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.30,
    shadowRadius: 6,
    elevation: 6,
  },
  circleAlert: {
    borderColor: ALERT_COLOR,
  },
  num: {
    fontSize: 16,
    fontWeight: '900',
    color: '#fff',
    lineHeight: 18,
  },
  textAlert: {
    color: ALERT_COLOR,
  },
  unit: {
    fontSize: 7,
    fontWeight: '600',
    color: 'rgba(255,255,255,0.70)',
  },
  unitAlert: {
    color: ALERT_COLOR,
  },
});
