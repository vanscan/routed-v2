/**
 * MapStyleSwitcher.tsx — UI component to switch between map styles
 * 
 * Available styles from VersaTiles:
 * - Colorful (vibrant, default)
 * - Graybeard (muted, professional)
 * - Eclipse (dark mode)
 * - Neutrino (minimal)
 * - Shadow (subtle shadows)
 * - Satellite (aerial imagery)
 */
import React, { useState } from 'react';
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
  Modal,
  ScrollView,
  Platform,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';

// VersaTiles style URLs - free, no API key needed
export const MAP_STYLES = {
  colorful: {
    name: 'Colorful',
    description: 'Vibrant and detailed',
    url: 'https://tiles.versatiles.org/assets/styles/colorful/style.json',
    icon: 'color-palette' as const,
  },
  graybeard: {
    name: 'Graybeard',
    description: 'Muted, professional tones',
    url: 'https://tiles.versatiles.org/assets/styles/graybeard/style.json',
    icon: 'contrast' as const,
  },
  eclipse: {
    name: 'Eclipse',
    description: 'Dark mode for night driving',
    url: 'https://tiles.versatiles.org/assets/styles/eclipse/style.json',
    icon: 'moon' as const,
  },
  neutrino: {
    name: 'Neutrino',
    description: 'Clean and minimal',
    url: 'https://tiles.versatiles.org/assets/styles/neutrino/style.json',
    icon: 'remove' as const,
  },
  shadow: {
    name: 'Shadow',
    description: 'Subtle depth and shadows',
    url: 'https://tiles.versatiles.org/assets/styles/shadow/style.json',
    icon: 'layers' as const,
  },
  satellite: {
    name: 'Satellite',
    description: 'Aerial imagery view',
    url: 'https://tiles.versatiles.org/assets/styles/satellite/style.json',
    icon: 'earth' as const,
  },
} as const;

export type MapStyleKey = keyof typeof MAP_STYLES;

interface MapStyleSwitcherProps {
  currentStyle: MapStyleKey;
  onStyleChange: (styleKey: MapStyleKey, styleUrl: string) => void;
  compact?: boolean; // Just show icon button
}

export const MapStyleSwitcher: React.FC<MapStyleSwitcherProps> = ({
  currentStyle,
  onStyleChange,
  compact = true,
}) => {
  const [modalVisible, setModalVisible] = useState(false);

  const handleStyleSelect = (key: MapStyleKey) => {
    onStyleChange(key, MAP_STYLES[key].url);
    setModalVisible(false);
  };

  return (
    <>
      {/* Trigger Button */}
      <TouchableOpacity
        style={styles.triggerButton}
        onPress={() => setModalVisible(true)}
        activeOpacity={0.7}
      >
        <Ionicons
          name="layers-outline"
          size={22}
          color="#1e293b"
        />
        {!compact && (
          <Text style={styles.triggerText}>
            {MAP_STYLES[currentStyle].name}
          </Text>
        )}
      </TouchableOpacity>

      {/* Style Selection Modal */}
      <Modal
        visible={modalVisible}
        transparent
        animationType="slide"
        onRequestClose={() => setModalVisible(false)}
      >
        <TouchableOpacity
          style={styles.modalOverlay}
          activeOpacity={1}
          onPress={() => setModalVisible(false)}
        >
          <View style={styles.modalContent}>
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>Map Style</Text>
              <TouchableOpacity
                onPress={() => setModalVisible(false)}
                hitSlop={{ top: 10, bottom: 10, left: 10, right: 10 }}
              >
                <Ionicons name="close" size={24} color="#64748b" />
              </TouchableOpacity>
            </View>

            <ScrollView style={styles.styleList}>
              {(Object.keys(MAP_STYLES) as MapStyleKey[]).map((key) => {
                const style = MAP_STYLES[key];
                const isSelected = currentStyle === key;

                return (
                  <TouchableOpacity
                    key={key}
                    style={[
                      styles.styleOption,
                      isSelected && styles.styleOptionSelected,
                    ]}
                    onPress={() => handleStyleSelect(key)}
                    activeOpacity={0.7}
                  >
                    <View style={[
                      styles.styleIconContainer,
                      isSelected && styles.styleIconContainerSelected,
                    ]}>
                      <Ionicons
                        name={style.icon}
                        size={24}
                        color={isSelected ? '#ffffff' : '#64748b'}
                      />
                    </View>
                    <View style={styles.styleInfo}>
                      <Text style={[
                        styles.styleName,
                        isSelected && styles.styleNameSelected,
                      ]}>
                        {style.name}
                      </Text>
                      <Text style={styles.styleDescription}>
                        {style.description}
                      </Text>
                    </View>
                    {isSelected && (
                      <Ionicons
                        name="checkmark-circle"
                        size={24}
                        color="#2563eb"
                      />
                    )}
                  </TouchableOpacity>
                );
              })}
            </ScrollView>
          </View>
        </TouchableOpacity>
      </Modal>
    </>
  );
};

const styles = StyleSheet.create({
  triggerButton: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#ffffff',
    paddingHorizontal: 12,
    paddingVertical: 10,
    borderRadius: 12,
    ...Platform.select({
      ios: {
        shadowColor: '#000',
        shadowOffset: { width: 0, height: 2 },
        shadowOpacity: 0.15,
        shadowRadius: 4,
      },
      android: {
        elevation: 4,
      },
    }),
  },
  triggerText: {
    marginLeft: 8,
    fontSize: 14,
    fontWeight: '600',
    color: '#1e293b',
  },
  modalOverlay: {
    flex: 1,
    backgroundColor: 'rgba(0, 0, 0, 0.5)',
    justifyContent: 'flex-end',
  },
  modalContent: {
    backgroundColor: '#ffffff',
    borderTopLeftRadius: 24,
    borderTopRightRadius: 24,
    paddingBottom: Platform.OS === 'ios' ? 34 : 24,
    maxHeight: '70%',
  },
  modalHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 20,
    paddingVertical: 16,
    borderBottomWidth: 1,
    borderBottomColor: '#e2e8f0',
  },
  modalTitle: {
    fontSize: 18,
    fontWeight: '700',
    color: '#0f172a',
  },
  styleList: {
    paddingHorizontal: 16,
    paddingTop: 12,
  },
  styleOption: {
    flexDirection: 'row',
    alignItems: 'center',
    padding: 14,
    borderRadius: 12,
    marginBottom: 8,
    backgroundColor: '#f8fafc',
  },
  styleOptionSelected: {
    backgroundColor: '#eff6ff',
    borderWidth: 1,
    borderColor: '#2563eb',
  },
  styleIconContainer: {
    width: 48,
    height: 48,
    borderRadius: 12,
    backgroundColor: '#e2e8f0',
    alignItems: 'center',
    justifyContent: 'center',
  },
  styleIconContainerSelected: {
    backgroundColor: '#2563eb',
  },
  styleInfo: {
    flex: 1,
    marginLeft: 14,
  },
  styleName: {
    fontSize: 16,
    fontWeight: '600',
    color: '#1e293b',
    marginBottom: 2,
  },
  styleNameSelected: {
    color: '#1e40af',
  },
  styleDescription: {
    fontSize: 13,
    color: '#64748b',
  },
});

export default MapStyleSwitcher;
