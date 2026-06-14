import React, { useEffect, useRef } from 'react';
import { Animated, View, Text, StyleSheet, TouchableOpacity, Switch, ScrollView } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { navColors, navShelfColors, SETTINGS_PANEL_WIDTH } from './navTheme';
import { NavSettings } from './useNavSettings';
import { MAP_STYLES, MapStyleKey } from '../../map/MapStyleSwitcher';

interface SettingsPanelProps {
  visible: boolean;
  onClose: () => void;
  settings: NavSettings;
  onSettingsChange: (patch: Partial<NavSettings>) => void;
  onStopNavigation: () => void;
}

export const SettingsPanel: React.FC<SettingsPanelProps> = ({
  visible,
  onClose,
  settings,
  onSettingsChange,
  onStopNavigation,
}) => {
  const translateX = useRef(new Animated.Value(SETTINGS_PANEL_WIDTH)).current;
  const overlayOpacity = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    Animated.parallel([
      Animated.timing(translateX, { toValue: visible ? 0 : SETTINGS_PANEL_WIDTH, duration: 260, useNativeDriver: true }),
      Animated.timing(overlayOpacity, { toValue: visible ? 1 : 0, duration: 260, useNativeDriver: true }),
    ]).start();
  }, [visible, translateX, overlayOpacity]);

  const MAP_STYLE_KEYS: MapStyleKey[] = ['colorful', 'eclipse', 'graybeard', 'neutrino'];

  return (
    <>
      {/* Overlay — tapping it closes the panel */}
      <Animated.View
        style={[styles.overlay, { opacity: overlayOpacity }]}
        pointerEvents={visible ? 'auto' : 'none'}
      >
        <TouchableOpacity style={{ flex: 1 }} onPress={onClose} activeOpacity={1} />
      </Animated.View>

      {/* Panel */}
      <Animated.View style={[styles.panel, { transform: [{ translateX }] }]}>
        <ScrollView bounces={false} showsVerticalScrollIndicator={false}>
          {/* Header */}
          <View style={styles.header}>
            <Text style={styles.title}>Nav Settings</Text>
            <TouchableOpacity onPress={onClose} hitSlop={12} style={styles.closeBtn} testID="settings-close-btn">
              <Ionicons name="close" size={18} color="#94a3b8" />
            </TouchableOpacity>
          </View>

          {/* Voice navigation */}
          <View style={styles.row}>
            <View style={styles.rowLeft}>
              <Ionicons name="volume-high-outline" size={17} color="#94a3b8" />
              <Text style={styles.rowLabel}>Voice navigation</Text>
            </View>
            <Switch
              value={settings.voiceEnabled}
              onValueChange={(v) => onSettingsChange({ voiceEnabled: v })}
              trackColor={{ false: '#1e293b', true: '#2563eb' }}
              thumbColor="#fff"
              testID="settings-voice-switch"
            />
          </View>

          {/* Map style */}
          <View style={styles.section}>
            <View style={styles.sectionLabel}>
              <Ionicons name="map-outline" size={17} color="#94a3b8" />
              <Text style={styles.rowLabel}>Map style</Text>
            </View>
            <View style={styles.styleTiles}>
              {MAP_STYLE_KEYS.map((key) => (
                <TouchableOpacity
                  key={key}
                  style={[styles.styleTile, settings.mapStyle === key && styles.styleTileActive]}
                  onPress={() => onSettingsChange({ mapStyle: key })}
                  testID={`settings-style-${key}`}
                >
                  <Ionicons
                    name={MAP_STYLES[key].icon}
                    size={16}
                    color={settings.mapStyle === key ? '#fff' : '#64748b'}
                  />
                  <Text style={[styles.styleTileLabel, settings.mapStyle === key && styles.styleTileLabelActive]}>
                    {MAP_STYLES[key].name}
                  </Text>
                </TouchableOpacity>
              ))}
            </View>
          </View>

          {/* Card sensitivity */}
          <View style={styles.section}>
            <View style={styles.sectionLabel}>
              <Ionicons name="radio-outline" size={17} color="#94a3b8" />
              <Text style={styles.rowLabel}>Card sensitivity</Text>
            </View>
            <View style={styles.segPill}>
              {(['tight', 'normal', 'wide'] as const).map((opt) => (
                <TouchableOpacity
                  key={opt}
                  style={[styles.segOpt, settings.cardSensitivity === opt && styles.segOptActive]}
                  onPress={() => onSettingsChange({ cardSensitivity: opt })}
                  testID={`settings-sens-${opt}`}
                >
                  <Text style={[styles.segOptLabel, settings.cardSensitivity === opt && styles.segOptLabelActive]}>
                    {opt === 'tight' ? 'Tight 20m' : opt === 'normal' ? 'Normal 30m' : 'Wide 50m'}
                  </Text>
                </TouchableOpacity>
              ))}
            </View>
          </View>

          {/* Speed units */}
          <View style={styles.row}>
            <View style={styles.rowLeft}>
              <Ionicons name="speedometer-outline" size={17} color="#94a3b8" />
              <Text style={styles.rowLabel}>Speed units</Text>
            </View>
            <View style={styles.segPillSm}>
              {(['kmh', 'mph'] as const).map((unit) => (
                <TouchableOpacity
                  key={unit}
                  style={[styles.segOpt, settings.speedUnits === unit && styles.segOptActive]}
                  onPress={() => onSettingsChange({ speedUnits: unit })}
                  testID={`settings-units-${unit}`}
                >
                  <Text style={[styles.segOptLabel, settings.speedUnits === unit && styles.segOptLabelActive]}>
                    {unit === 'kmh' ? 'km/h' : 'mph'}
                  </Text>
                </TouchableOpacity>
              ))}
            </View>
          </View>

          <View style={styles.divider} />

          {/* Stop navigation */}
          <TouchableOpacity
            style={styles.stopNavBtn}
            onPress={() => { onClose(); onStopNavigation(); }}
            testID="settings-stop-nav-btn"
          >
            <Ionicons name="stop-circle-outline" size={17} color={navColors.failedFg} />
            <Text style={styles.stopNavLabel}>Stop Navigation</Text>
          </TouchableOpacity>
        </ScrollView>
      </Animated.View>
    </>
  );
};

const styles = StyleSheet.create({
  overlay: {
    position: 'absolute',
    top: 0, left: 0, right: 0, bottom: 0,
    backgroundColor: 'rgba(0,0,0,0.45)',
    zIndex: 110,
  },
  panel: {
    position: 'absolute',
    top: 0, right: 0, bottom: 0,
    width: SETTINGS_PANEL_WIDTH,
    backgroundColor: navShelfColors.settingsPanelBg,
    borderLeftWidth: 1,
    borderLeftColor: navColors.hairline,
    zIndex: 120,
    paddingTop: 48,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 20,
    paddingVertical: 14,
    borderBottomWidth: 1,
    borderBottomColor: navColors.divider,
  },
  title: { fontSize: 15, fontWeight: '700', color: '#fff' },
  closeBtn: {
    width: 28, height: 28, borderRadius: 14,
    backgroundColor: 'rgba(255,255,255,0.08)',
    justifyContent: 'center', alignItems: 'center',
  },

  row: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 20,
    paddingVertical: 14,
    borderBottomWidth: 1,
    borderBottomColor: navColors.divider,
  },
  rowLeft: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  rowLabel: { fontSize: 13, color: '#cbd5e1', fontWeight: '500' },

  section: {
    paddingHorizontal: 20,
    paddingVertical: 14,
    borderBottomWidth: 1,
    borderBottomColor: navColors.divider,
    gap: 10,
  },
  sectionLabel: { flexDirection: 'row', alignItems: 'center', gap: 10 },

  styleTiles: { flexDirection: 'row', gap: 6 },
  styleTile: {
    flex: 1, paddingVertical: 8,
    backgroundColor: '#1e293b',
    borderRadius: 10,
    borderWidth: 1.5,
    borderColor: 'transparent',
    alignItems: 'center',
    gap: 4,
  },
  styleTileActive: { borderColor: '#3b82f6', backgroundColor: 'rgba(37,99,235,0.20)' },
  styleTileLabel: { fontSize: 9, fontWeight: '600', color: '#64748b' },
  styleTileLabelActive: { color: '#93c5fd' },

  segPill: {
    flexDirection: 'row',
    backgroundColor: '#1e293b',
    borderRadius: 10,
    padding: 3,
    gap: 2,
  },
  segPillSm: {
    flexDirection: 'row',
    backgroundColor: '#1e293b',
    borderRadius: 10,
    padding: 3,
    gap: 2,
  },
  segOpt: { flex: 1, paddingVertical: 6, paddingHorizontal: 4, borderRadius: 8, alignItems: 'center' },
  segOptActive: { backgroundColor: '#2563eb' },
  segOptLabel: { fontSize: 11, fontWeight: '600', color: '#64748b' },
  segOptLabelActive: { color: '#fff' },

  divider: { height: 1, backgroundColor: navColors.divider, marginVertical: 8 },

  stopNavBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingHorizontal: 20,
    paddingVertical: 14,
  },
  stopNavLabel: { fontSize: 13, fontWeight: '600', color: navColors.failedFg },
});
