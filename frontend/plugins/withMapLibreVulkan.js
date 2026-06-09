/**
 * withMapLibreVulkan.js — Expo config plugin
 * Enables Vulkan GPU-accelerated rendering for MapLibre on Android.
 *
 * Uses the official @maplibre/maplibre-react-native nativeVariant mechanism:
 * sets `org.maplibre.reactnative.nativeVariant=vulkan` in gradle.properties,
 * which causes the RN wrapper's own build.gradle to pull
 * `org.maplibre.gl:android-sdk-vulkan:<nativeVersion>` at the correct version
 * (currently 13.2.0 as shipped with maplibre-react-native v11).
 *
 * Previous approach (resolutionStrategy.force to 12.3.0) was wrong — it
 * downgraded the native SDK from the version the RN wrapper was compiled
 * against (13.2.0), causing Kotlin compilation failures.
 */
const { withGradleProperties, createRunOncePlugin } = require('@expo/config-plugins');

const withMapLibreVulkan = (config) => {
  return withGradleProperties(config, (cfg) => {
    const hasVariant = cfg.modResults.some(
      (p) => p.key === 'org.maplibre.reactnative.nativeVariant'
    );

    if (!hasVariant) {
      cfg.modResults.push({
        type: 'property',
        key: 'org.maplibre.reactnative.nativeVariant',
        value: 'vulkan',
      });
    }

    return cfg;
  });
};

module.exports = createRunOncePlugin(
  withMapLibreVulkan,
  'with-maplibre-vulkan',
  '1.0.0'
);
