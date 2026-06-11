/**
 * withKotlinVersion.js — Expo config plugin
 *
 * expo-build-properties sets `android.kotlinVersion` in gradle.properties, which
 * lands in `buildscript.ext.kotlinVersion`. However ExpoRootProjectPlugin (from
 * expo-modules-autolinking) checks `project.ext.kotlinVersion` — a separate Gradle
 * extra-properties scope — so it never sees the value and falls back to its hardcoded
 * default ("2.0.21"), deriving an incompatible KSP version ("2.0.21-1.0.28").
 *
 * react-native-async-storage v3+ requires Kotlin 2.1.0+ for KSP processing. When
 * KSP 2.0.21-1.0.28 runs against a Kotlin 2.1.x compiler it crashes with:
 *   NoSuchMethodError: KotlinTypeMapper.Companion.getLANGUAGE_VERSION_SETTINGS_DEFAULT()
 *
 * Fix: inject `ext.kotlinVersion` at the project level in android/build.gradle so
 * ExpoRootProjectPlugin.extra.has("kotlinVersion") returns true, causing it to use
 * the correct version and auto-derive a compatible KSP version via KSPLookup.
 */
const { withProjectBuildGradle } = require('@expo/config-plugins');

const MARKER = '// [withKotlinVersion] set project.ext.kotlinVersion';

module.exports = (config) => {
  return withProjectBuildGradle(config, (cfg) => {
    if (cfg.modResults.language !== 'groovy') return cfg;

    if (cfg.modResults.contents.includes(MARKER)) return cfg;

    // Insert immediately after the closing brace of the buildscript {} block.
    // This runs before any plugin is applied (ExpoRootProjectPlugin fires via
    // gradle.afterProject, which is after the whole build.gradle is evaluated).
    cfg.modResults.contents = cfg.modResults.contents.replace(
      /(^buildscript\s*\{[\s\S]*?^\})/m,
      `$1\n\n${MARKER}\next.kotlinVersion = findProperty('android.kotlinVersion') ?: '2.2.10'`
    );

    return cfg;
  });
};
