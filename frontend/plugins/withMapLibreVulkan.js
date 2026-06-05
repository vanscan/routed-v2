/**
 * withMapLibreVulkan.js — Expo config plugin
 * Enables Vulkan GPU-accelerated rendering for MapLibre on Android.
 *
 * Benefits:
 *  - Hardware-level GPU acceleration via Vulkan API
 *  - Better battery life compared to OpenGL ES
 *  - Higher performance map rendering
 *  - Modern graphics pipeline (Android 7+ / API 24+)
 *
 * This plugin:
 *  1. Replaces the default MapLibre SDK with the Vulkan-enabled artifact
 *  2. Adds necessary Gradle configuration
 */
const {
  withAppBuildGradle,
  withGradleProperties,
  createRunOncePlugin,
} = require('@expo/config-plugins');

const MAPLIBRE_VULKAN_VERSION = '12.3.0';

/**
 * Modify app/build.gradle to use MapLibre Vulkan SDK
 */
const withMapLibreVulkanGradle = (config) => {
  return withAppBuildGradle(config, (cfg) => {
    let contents = cfg.modResults.contents;

    // Check if we already have the Vulkan configuration
    if (contents.includes('android-sdk-vulkan')) {
      return cfg;
    }

    // Add configuration to exclude the default OpenGL SDK and use Vulkan instead
    // This goes in the android { } block
    const vulkanConfig = `
    // MapLibre Vulkan GPU Acceleration
    configurations.all {
        resolutionStrategy {
            // Force Vulkan renderer for MapLibre (better performance & battery)
            force "org.maplibre.gl:android-sdk-vulkan:${MAPLIBRE_VULKAN_VERSION}"
        }
    }
`;

    // Find the android { block and add our configuration
    const androidBlockMatch = contents.match(/android\s*\{/);
    if (androidBlockMatch) {
      const insertIndex = androidBlockMatch.index + androidBlockMatch[0].length;
      contents =
        contents.slice(0, insertIndex) +
        vulkanConfig +
        contents.slice(insertIndex);
    }

    // Also add a dependency substitution in dependencies block if it exists
    const dependencySubstitution = `
    // Substitute MapLibre OpenGL with Vulkan variant
    implementation("org.maplibre.gl:android-sdk-vulkan:${MAPLIBRE_VULKAN_VERSION}") {
        exclude group: 'org.maplibre.gl', module: 'android-sdk'
    }
`;

    // Find dependencies block
    const depsMatch = contents.match(/dependencies\s*\{/);
    if (depsMatch && !contents.includes('android-sdk-vulkan')) {
      const insertIndex = depsMatch.index + depsMatch[0].length;
      contents =
        contents.slice(0, insertIndex) +
        dependencySubstitution +
        contents.slice(insertIndex);
    }

    cfg.modResults.contents = contents;
    return cfg;
  });
};

/**
 * Add Gradle properties for Vulkan optimization
 */
const withVulkanGradleProperties = (config) => {
  return withGradleProperties(config, (cfg) => {
    // Add properties that help with Vulkan rendering
    const vulkanProps = [
      {
        type: 'property',
        key: 'maplibre.renderer',
        value: 'vulkan',
      },
      {
        type: 'comment',
        value: 'MapLibre Vulkan GPU Acceleration',
      },
    ];

    // Check if already added
    const hasVulkanProp = cfg.modResults.some(
      (p) => p.key === 'maplibre.renderer'
    );

    if (!hasVulkanProp) {
      cfg.modResults.push(...vulkanProps);
    }

    return cfg;
  });
};

/**
 * Combined plugin
 */
const withMapLibreVulkan = (config) => {
  config = withMapLibreVulkanGradle(config);
  config = withVulkanGradleProperties(config);
  return config;
};

module.exports = createRunOncePlugin(
  withMapLibreVulkan,
  'with-maplibre-vulkan',
  '1.0.0'
);
