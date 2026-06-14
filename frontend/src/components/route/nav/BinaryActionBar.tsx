import React, { useRef, useState } from 'react';
import {
  View, Text, Pressable, StyleSheet, Animated,
} from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import * as Haptics from 'expo-haptics';
import { Ionicons } from '@expo/vector-icons';
import { navColors } from './navTheme';
import { FailReasonSheet } from './FailReasonSheet';

const HOLD_DURATION_MS = 1000;

interface BinaryActionBarProps {
  onMarkDelivered: () => void;
  onMarkFailed: (reason?: string) => void;
  insets: { bottom: number };
}

export const BinaryActionBar: React.FC<BinaryActionBarProps> = ({
  onMarkDelivered,
  onMarkFailed,
  insets,
}) => {
  const [reasonVisible, setReasonVisible] = useState(false);
  const holdProgress = useRef(new Animated.Value(0)).current;
  const holdAnimRef = useRef<Animated.CompositeAnimation | null>(null);

  const startHold = () => {
    try { Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light); } catch {}
    holdProgress.setValue(0);
    holdAnimRef.current = Animated.timing(holdProgress, {
      toValue: 1,
      duration: HOLD_DURATION_MS,
      useNativeDriver: false,
    });
    holdAnimRef.current.start(({ finished }) => {
      if (finished) {
        try { Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning); } catch {}
        setReasonVisible(true);
      }
    });
  };

  const cancelHold = () => {
    holdAnimRef.current?.stop();
    Animated.timing(holdProgress, {
      toValue: 0,
      duration: 150,
      useNativeDriver: false,
    }).start();
  };

  const handleReasonSelect = (reason: string) => {
    setReasonVisible(false);
    holdProgress.setValue(0);
    try { Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error); } catch {}
    onMarkFailed(reason);
  };

  const handleReasonDismiss = () => {
    setReasonVisible(false);
    holdProgress.setValue(0);
  };

  const holdFillWidth = holdProgress.interpolate({
    inputRange: [0, 1],
    outputRange: ['0%', '100%'],
  });

  return (
    <>
      <View style={[styles.bar, { paddingBottom: Math.max(insets.bottom, 8) + 4 }]}>
        {/* FAIL — hold 1 second */}
        <Pressable
          style={styles.failBtn}
          onPressIn={startHold}
          onPressOut={cancelHold}
          accessibilityRole="button"
          accessibilityLabel="Mark stop as failed — hold one second"
        >
          <Animated.View
            style={[styles.holdFill, { width: holdFillWidth }]}
            pointerEvents="none"
          />
          <View style={styles.failContent}>
            <Ionicons name="close" size={22} color="#fef3c7" />
            <Text style={styles.failLabel}>FAIL</Text>
          </View>
          <Text style={styles.holdHint}>hold 1s</Text>
        </Pressable>

        {/* DELIVERED — single tap */}
        <Pressable
          style={({ pressed }) => [styles.deliveredBtn, pressed && styles.deliveredPressed]}
          onPress={() => {
            try { Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Heavy); } catch {}
            onMarkDelivered();
          }}
          accessibilityRole="button"
          accessibilityLabel="Mark stop as delivered"
          testID="binary-bar-delivered"
        >
          <LinearGradient
            colors={navColors.greenGrad}
            start={{ x: 0, y: 0 }}
            end={{ x: 1, y: 0 }}
            style={styles.deliveredFill}
          >
            <Ionicons name="checkmark" size={26} color="#fff" />
            <Text style={styles.deliveredLabel}>DELIVERED</Text>
          </LinearGradient>
        </Pressable>
      </View>

      <FailReasonSheet
        visible={reasonVisible}
        onSelect={handleReasonSelect}
        onDismiss={handleReasonDismiss}
      />
    </>
  );
};

const styles = StyleSheet.create({
  bar: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    flexDirection: 'row',
    gap: 10,
    paddingHorizontal: 12,
    paddingTop: 12,
    backgroundColor: 'rgba(5, 10, 24, 0.88)',
    borderTopWidth: 1,
    borderTopColor: 'rgba(255,255,255,0.07)',
    zIndex: 100,
  },

  // FAIL button (40% width)
  failBtn: {
    flex: 2,
    height: 72,
    borderRadius: 20,
    backgroundColor: 'rgba(180, 83, 9, 0.82)',
    borderWidth: 1,
    borderColor: 'rgba(245, 158, 11, 0.35)',
    overflow: 'hidden',
    justifyContent: 'center',
    alignItems: 'center',
  },
  holdFill: {
    position: 'absolute',
    top: 0,
    left: 0,
    bottom: 0,
    backgroundColor: 'rgba(245, 158, 11, 0.35)',
  },
  failContent: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  failLabel: {
    fontSize: 17,
    fontWeight: '800',
    color: '#fef3c7',
    letterSpacing: 0.5,
  },
  holdHint: {
    fontSize: 9,
    fontWeight: '600',
    color: 'rgba(254, 243, 199, 0.45)',
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    marginTop: 3,
  },

  // DELIVERED button (60% width)
  deliveredBtn: {
    flex: 3,
    height: 72,
    borderRadius: 20,
    overflow: 'hidden',
    shadowColor: '#10b981',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.45,
    shadowRadius: 12,
    elevation: 8,
  },
  deliveredPressed: {
    opacity: 0.85,
    transform: [{ scale: 0.97 }],
  },
  deliveredFill: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
  },
  deliveredLabel: {
    fontSize: 18,
    fontWeight: '900',
    color: '#fff',
    letterSpacing: 0.4,
  },
});
