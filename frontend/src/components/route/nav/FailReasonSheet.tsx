import React, { useEffect, useRef } from 'react';
import {
  View, Text, Modal, Pressable, TouchableOpacity, StyleSheet, Animated,
} from 'react-native';
import { navColors } from './navTheme';

const REASONS = [
  { key: 'no_access',     label: 'No Access',     icon: '🚧' },
  { key: 'not_home',      label: 'Not Home',       icon: '🏠' },
  { key: 'damaged',       label: 'Damaged',        icon: '📦' },
  { key: 'wrong_address', label: 'Wrong Address',  icon: '📍' },
  { key: 'other',         label: 'Other',          icon: '💬' },
] as const;

const AUTO_DISMISS_MS = 10_000;

interface FailReasonSheetProps {
  visible: boolean;
  onSelect: (reason: string) => void;
  onDismiss: () => void;
}

export const FailReasonSheet: React.FC<FailReasonSheetProps> = ({
  visible,
  onSelect,
  onDismiss,
}) => {
  const slideY = useRef(new Animated.Value(300)).current;
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (visible) {
      Animated.spring(slideY, {
        toValue: 0,
        useNativeDriver: true,
        friction: 10,
        tension: 80,
      }).start();
      timerRef.current = setTimeout(onDismiss, AUTO_DISMISS_MS);
    } else {
      Animated.timing(slideY, {
        toValue: 300,
        duration: 200,
        useNativeDriver: true,
      }).start();
      if (timerRef.current) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    }
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [visible, slideY, onDismiss]);

  return (
    <Modal
      visible={visible}
      transparent
      animationType="none"
      statusBarTranslucent
      onRequestClose={onDismiss}
    >
      <Pressable style={styles.backdrop} onPress={onDismiss}>
        <Animated.View
          style={[styles.sheet, { transform: [{ translateY: slideY }] }]}
        >
          <Pressable onPress={(e) => e.stopPropagation()}>
            <View style={styles.handle} />
            <Text style={styles.title}>Why did this fail?</Text>
            <View style={styles.grid}>
              {REASONS.map((r, i) => (
                <TouchableOpacity
                  key={r.key}
                  style={[styles.chip, i === REASONS.length - 1 && styles.chipFull]}
                  onPress={() => onSelect(r.key)}
                  activeOpacity={0.75}
                >
                  <Text style={styles.chipIcon}>{r.icon}</Text>
                  <Text style={styles.chipLabel}>{r.label}</Text>
                </TouchableOpacity>
              ))}
            </View>
          </Pressable>
        </Animated.View>
      </Pressable>
    </Modal>
  );
};

const styles = StyleSheet.create({
  backdrop: {
    flex: 1,
    justifyContent: 'flex-end',
    backgroundColor: 'rgba(0,0,0,0.5)',
  },
  sheet: {
    backgroundColor: '#111827',
    borderTopLeftRadius: 28,
    borderTopRightRadius: 28,
    borderTopWidth: 1,
    borderColor: navColors.hairline,
    paddingHorizontal: 16,
    paddingBottom: 36,
  },
  handle: {
    width: 36,
    height: 4,
    backgroundColor: 'rgba(255,255,255,0.20)',
    borderRadius: 2,
    alignSelf: 'center',
    marginTop: 12,
    marginBottom: 16,
  },
  title: {
    fontSize: 12,
    fontWeight: '700',
    color: 'rgba(255,255,255,0.45)',
    textTransform: 'uppercase',
    letterSpacing: 1,
    textAlign: 'center',
    marginBottom: 14,
  },
  grid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  chip: {
    width: '47%',
    flexGrow: 1,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    backgroundColor: 'rgba(255,255,255,0.07)',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.12)',
    borderRadius: 14,
    paddingVertical: 16,
    paddingHorizontal: 14,
  },
  chipFull: {
    width: '100%',
    justifyContent: 'center',
  },
  chipIcon: {
    fontSize: 20,
  },
  chipLabel: {
    fontSize: 15,
    fontWeight: '700',
    color: '#e5e7eb',
  },
});
