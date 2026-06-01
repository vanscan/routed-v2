/**
 * native-map-test.tsx — WEB stub for the native map test screen.
 *
 * The actual test harness lives in `native-map-test.native.tsx` and is bundled
 * ONLY on iOS/Android. This web stub prevents Metro's SSR pass from pulling in
 * `@maplibre/maplibre-react-native` (a native module with no web build).
 */
import React from 'react';
import { View, Text, StyleSheet, TouchableOpacity } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Stack, useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';

export default function NativeMapTestWebStub() {
  const router = useRouter();

  return (
    <SafeAreaView style={styles.flex} edges={['top', 'bottom']}>
      <Stack.Screen options={{ title: 'Native Map Test' }} />
      <View style={styles.notice}>
        <Ionicons name="phone-portrait-outline" size={48} color="#0b2545" />
        <Text style={styles.noticeTitle}>Native build required</Text>
        <Text style={styles.noticeBody}>
          The native MapLibre map can&apos;t render in the web preview or Expo Go.
          Install the EAS development build on a real device to test this screen.
        </Text>
        <TouchableOpacity style={styles.backBtn} onPress={() => router.back()}>
          <Text style={styles.backBtnText}>Go Back</Text>
        </TouchableOpacity>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: '#fff' },
  notice: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    padding: 32,
    gap: 12,
  },
  noticeTitle: { fontSize: 20, fontWeight: '700', color: '#0b2545' },
  noticeBody: { fontSize: 14, color: '#475569', textAlign: 'center', lineHeight: 20 },
  backBtn: {
    marginTop: 16,
    backgroundColor: '#0b2545',
    paddingHorizontal: 24,
    paddingVertical: 12,
    borderRadius: 10,
  },
  backBtnText: { color: '#fff', fontWeight: '700' },
});
