/**
 * DeliveryMapNative.tsx — WEB stub.
 *
 * The real native MapLibre implementation lives in
 * `DeliveryMapNative.native.tsx` and is bundled ONLY on iOS/Android (it pulls
 * in `@maplibre/maplibre-react-native`, a native module with no web build).
 *
 * Metro resolves `.native.tsx` on device and this file on web / SSR static
 * export. This stub keeps the web bundle free of the native module while
 * preserving the exact `DeliveryMapRef` interface so callers type-check
 * identically across platforms.
 */
import React, { forwardRef, useImperativeHandle } from 'react';
import { View, Text, StyleSheet } from 'react-native';
import type { DeliveryMapRef } from '../DeliveryMap';
import type { DeliveryMapNativeProps } from './DeliveryMapNative.types';

const DeliveryMapNativeInner = forwardRef<DeliveryMapRef, DeliveryMapNativeProps>(
  (_props, ref) => {
    useImperativeHandle(
      ref,
      (): DeliveryMapRef => ({
        flyTo: () => {},
        jumpTo: () => {},
        fitBounds: () => {},
        setDrawingMode: () => {},
        clearLasso: () => {},
        addSectionPolygon: () => {},
        removeSectionPolygon: () => {},
        clearAllSectionPolygons: () => {},
        toggleParcels: () => {},
        setBlockRoadMode: () => {},
        setNogoZones: () => {},
        setRouteConfirmed: () => {},
        sendMessage: () => {},
        setClusters: () => {},
        forceStopsRefresh: () => {},
        getMap: () => null,
      }),
      [],
    );

    return (
      <View style={styles.container}>
        <Text style={styles.text}>
          Native MapLibre map is available on iOS / Android dev builds only.
        </Text>
      </View>
    );
  },
);

DeliveryMapNativeInner.displayName = 'DeliveryMapNativeWebStub';

export const DeliveryMapNative = React.memo(DeliveryMapNativeInner);
export default DeliveryMapNative;

const styles = StyleSheet.create({
  container: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    padding: 24,
    backgroundColor: '#eef2f7',
  },
  text: { color: '#475569', textAlign: 'center', fontSize: 14 },
});
