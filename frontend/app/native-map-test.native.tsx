/**
 * native-map-test.tsx — Phase 0 isolated harness for the NATIVE MapLibre map.
 *
 * Renders `DeliveryMapNative` (the `@maplibre/maplibre-react-native` build)
 * against the user's real stops (or a small QLD demo set) so the native map,
 * Liberty style, numbered pins, and the native location puck can be validated
 * on a real device WITHOUT touching the production index.tsx flow.
 *
 * ⚠️ Native module — this screen only works in an EAS development/production
 * build. In Expo Go or the web preview it shows a build-required notice.
 */
import React, { useEffect, useMemo, useRef, useState, useCallback } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  Platform,
  ActivityIndicator,
  Linking,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Stack, useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import * as Location from 'expo-location';
import { useStopsStore } from '../src/store/stopsStore';
import { DeliveryMapNative } from '../src/components/map/DeliveryMapNative';
import { getUseNativeMapSync, setUseNativeMap } from '../src/utils/featureFlags';
import type {
  DeliveryMapRef,
  DeliveryStop,
  DriverLocation,
} from '../src/components/DeliveryMap';

// Small QLD demo manifest used when the account has no stops loaded.
const DEMO_STOPS: DeliveryStop[] = [
  { id: 'demo-1', latitude: -26.6531, longitude: 153.0905, name: 'Maroochydore', address: 'Maroochydore QLD', order: 0 },
  { id: 'demo-2', latitude: -26.6786, longitude: 153.0908, name: 'Alexandra Hdl', address: 'Alexandra Headland QLD', order: 1 },
  { id: 'demo-3', latitude: -26.7016, longitude: 153.1168, name: 'Mooloolaba', address: 'Mooloolaba QLD', order: 2 },
  { id: 'demo-4', latitude: -26.6925, longitude: 153.0680, name: 'Buderim', address: 'Buderim QLD', order: 3 },
  { id: 'demo-5', latitude: -26.6402, longitude: 153.0890, name: 'Cotton Tree', address: 'Cotton Tree QLD', order: 4, completed: true },
];

export default function NativeMapTestScreen() {
  const router = useRouter();
  const mapRef = useRef<DeliveryMapRef>(null);
  const storeStops = useStopsStore((s) => s.stops);
  const fetchStops = useStopsStore((s) => s.fetchStops);

  const [mapReady, setMapReady] = useState(false);
  const [permission, setPermission] = useState<Location.PermissionStatus | null>(null);
  const [canAskAgain, setCanAskAgain] = useState(true);
  const [driverLocation, setDriverLocation] = useState<DriverLocation | null>(null);
  const [follow, setFollow] = useState(false);
  const [isDefaultMap, setIsDefaultMap] = useState<boolean>(() => getUseNativeMapSync());

  const toggleDefaultMap = useCallback(async () => {
    const next = !isDefaultMap;
    setIsDefaultMap(next);
    await setUseNativeMap(next);
  }, [isDefaultMap]);

  const stops = useMemo<DeliveryStop[]>(
    () => (storeStops && storeStops.length > 0 ? (storeStops as unknown as DeliveryStop[]) : DEMO_STOPS),
    [storeStops],
  );

  // Best-effort load of the user's real manifest.
  useEffect(() => {
    fetchStops?.().catch(() => {});
  }, [fetchStops]);

  // Check current permission status on mount (no prompt yet — wait for intent).
  useEffect(() => {
    Location.getForegroundPermissionsAsync()
      .then((res) => {
        setPermission(res.status);
        setCanAskAgain(res.canAskAgain);
      })
      .catch(() => {});
  }, []);

  // Fit the camera to the stops once the map finishes loading.
  const fitToStops = useCallback(() => {
    if (!stops.length) return;
    let minLng = Infinity, minLat = Infinity, maxLng = -Infinity, maxLat = -Infinity;
    stops.forEach((s) => {
      minLng = Math.min(minLng, s.longitude);
      maxLng = Math.max(maxLng, s.longitude);
      minLat = Math.min(minLat, s.latitude);
      maxLat = Math.max(maxLat, s.latitude);
    });
    if (Number.isFinite(minLng)) {
      mapRef.current?.fitBounds([[minLng, minLat], [maxLng, maxLat]], 64);
    }
  }, [stops]);

  useEffect(() => {
    if (mapReady) {
      const t = setTimeout(fitToStops, 350);
      return () => clearTimeout(t);
    }
  }, [mapReady, fitToStops]);

  // Contextual location permission request (only on explicit "locate me" tap).
  const ensureLocation = useCallback(async (): Promise<boolean> => {
    const current = await Location.getForegroundPermissionsAsync();
    if (current.status === 'granted') {
      setPermission(current.status);
      return true;
    }
    if (current.canAskAgain) {
      const req = await Location.requestForegroundPermissionsAsync();
      setPermission(req.status);
      setCanAskAgain(req.canAskAgain);
      return req.status === 'granted';
    }
    // Permanently blocked → route the user to Settings.
    setPermission(current.status);
    setCanAskAgain(false);
    Linking.openSettings().catch(() => {});
    return false;
  }, []);

  const recenterOnMe = useCallback(async () => {
    const ok = await ensureLocation();
    if (!ok) return;
    try {
      const pos = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.High });
      const loc: DriverLocation = {
        latitude: pos.coords.latitude,
        longitude: pos.coords.longitude,
        heading: pos.coords.heading ?? 0,
      };
      setDriverLocation(loc);
      mapRef.current?.flyTo([loc.longitude, loc.latitude], { zoom: 16, duration: 800 });
    } catch {
      // ignore one-shot location failures
    }
  }, [ensureLocation]);

  const toggleFollow = useCallback(async () => {
    if (!follow) {
      const ok = await ensureLocation();
      if (!ok) return;
    }
    setFollow((f) => !f);
  }, [follow, ensureLocation]);

  // Web / Expo Go cannot load the native module.
  if (Platform.OS === 'web') {
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

  return (
    <SafeAreaView style={styles.flex} edges={['top']}>
      <Stack.Screen options={{ headerShown: false }} />

      <View style={styles.header}>
        <TouchableOpacity style={styles.iconBtn} onPress={() => router.back()} hitSlop={12}>
          <Ionicons name="chevron-back" size={24} color="#0b2545" />
        </TouchableOpacity>
        <View style={styles.headerTextWrap}>
          <Text style={styles.headerTitle}>Native Map (Phase 1)</Text>
          <Text style={styles.headerSub}>
            {storeStops?.length ? `${storeStops.length} stops` : 'demo stops'} ·{' '}
            {mapReady ? 'loaded' : 'loading…'}
          </Text>
        </View>
        <TouchableOpacity
          style={[styles.defaultPill, isDefaultMap && styles.defaultPillOn]}
          onPress={toggleDefaultMap}
          hitSlop={8}
        >
          <Ionicons
            name={isDefaultMap ? 'checkmark-circle' : 'ellipse-outline'}
            size={16}
            color={isDefaultMap ? '#fff' : '#0b2545'}
          />
          <Text style={[styles.defaultPillText, isDefaultMap && styles.defaultPillTextOn]}>
            {isDefaultMap ? 'Default' : 'Set default'}
          </Text>
        </TouchableOpacity>
      </View>

      <View style={styles.mapWrap}>
        <DeliveryMapNative
          ref={mapRef}
          stops={stops}
          routeCoordinates={null}
          driverLocation={driverLocation}
          traveledPath={null}
          followDriver={follow}
          initialCenter={[153.0905, -26.6531]}
          initialZoom={11}
          onMapReady={() => setMapReady(true)}
          onStopClick={() => {}}
        />

        {!mapReady && (
          <View style={styles.loadingOverlay} pointerEvents="none">
            <ActivityIndicator size="large" color="#0b2545" />
            <Text style={styles.loadingText}>Rendering native map…</Text>
          </View>
        )}

        <View style={styles.controls}>
          <TouchableOpacity style={styles.fab} onPress={recenterOnMe}>
            <Ionicons name="locate" size={22} color="#fff" />
          </TouchableOpacity>
          <TouchableOpacity
            style={[styles.fab, follow && styles.fabActive]}
            onPress={toggleFollow}
          >
            <Ionicons name="navigate" size={22} color="#fff" />
          </TouchableOpacity>
        </View>

        {permission === 'denied' && !canAskAgain && (
          <View style={styles.permBanner}>
            <Text style={styles.permText}>Location is off for RouTeD.</Text>
            <TouchableOpacity onPress={() => Linking.openSettings()}>
              <Text style={styles.permLink}>Open Settings</Text>
            </TouchableOpacity>
          </View>
        )}
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: '#fff' },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 8,
    paddingVertical: 10,
    gap: 8,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: '#e2e8f0',
  },
  iconBtn: {
    width: 44,
    height: 44,
    alignItems: 'center',
    justifyContent: 'center',
  },
  headerTextWrap: { flex: 1 },
  headerTitle: { fontSize: 17, fontWeight: '700', color: '#0b2545' },
  headerSub: { fontSize: 12, color: '#64748b', marginTop: 1 },
  defaultPill: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 12,
    height: 36,
    borderRadius: 18,
    borderWidth: 1,
    borderColor: '#cbd5e1',
    backgroundColor: '#fff',
  },
  defaultPillOn: { backgroundColor: '#2563eb', borderColor: '#2563eb' },
  defaultPillText: { fontSize: 12, fontWeight: '700', color: '#0b2545' },
  defaultPillTextOn: { color: '#fff' },
  mapWrap: { flex: 1 },
  loadingOverlay: {
    ...StyleSheet.absoluteFillObject,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: 'rgba(255,255,255,0.6)',
  },
  loadingText: { marginTop: 12, color: '#0b2545', fontWeight: '600' },
  controls: {
    position: 'absolute',
    right: 16,
    bottom: 28,
    gap: 12,
  },
  fab: {
    width: 52,
    height: 52,
    borderRadius: 26,
    backgroundColor: '#0b2545',
    alignItems: 'center',
    justifyContent: 'center',
    shadowColor: '#000',
    shadowOpacity: 0.2,
    shadowRadius: 6,
    shadowOffset: { width: 0, height: 3 },
    elevation: 4,
  },
  fabActive: { backgroundColor: '#2563eb' },
  permBanner: {
    position: 'absolute',
    left: 16,
    right: 16,
    bottom: 28,
    backgroundColor: '#0b2545',
    borderRadius: 12,
    paddingVertical: 12,
    paddingHorizontal: 16,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  permText: { color: '#fff', fontSize: 13, flex: 1 },
  permLink: { color: '#60a5fa', fontWeight: '700', fontSize: 13 },
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
