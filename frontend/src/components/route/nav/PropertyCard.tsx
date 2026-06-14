import React from 'react';
import { View, Text, StyleSheet, TouchableOpacity, Linking, Share } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import * as Haptics from 'expo-haptics';
import { navColors, navShelfColors } from './navTheme';
import { parseStopNotes } from './parseStopNotes';

interface ColocatedInfo {
  count: number;
  index: number;
  doneCount: number;
  group: any[];
}

interface PropertyCardProps {
  stop: any;
  colocatedInfo: ColocatedInfo;
}

export const PropertyCard: React.FC<PropertyCardProps> = ({ stop, colocatedInfo }) => {
  const parsed = React.useMemo(() => parseStopNotes(stop?.notes), [stop?.notes]);

  const hasChips =
    parsed.propertyType || parsed.safePlace || parsed.physicalKeyAccess ||
    stop?.mobile_number || stop?.tracking_number;

  const callPhone = () => {
    const num = stop?.mobile_number;
    if (!num) return;
    Linking.openURL(`tel:${num}`).catch(() => {});
  };

  const copyTracking = async () => {
    const t = stop?.tracking_number;
    if (!t) return;
    try {
      await Share.share({ message: t });
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    } catch {}
  };

  return (
    <View>
      {/* Multi-parcel warning */}
      {colocatedInfo.count > 1 && (
        <View style={styles.warnBanner}>
          <View style={styles.warnHeader}>
            <Ionicons name="warning" size={13} color={navColors.warnTitle} />
            <Text style={styles.warnTitle}>MULTIPLE PARCELS AT THIS ADDRESS</Text>
          </View>
          <View style={styles.warnBody}>
            <Text style={styles.warnLine}>
              Parcel <Text style={styles.warnBold}>{colocatedInfo.index}</Text> of{' '}
              <Text style={styles.warnBold}>{colocatedInfo.count}</Text>
              {stop?.weight ? `  ·  ${stop.weight} kg` : ''}
            </Text>
            <View style={styles.dotsRow}>
              {colocatedInfo.group.map((s: any, i: number) => (
                <View
                  key={s.id || i}
                  style={[
                    styles.dot,
                    s.completed && styles.dotDone,
                    s.id === stop?.id && styles.dotCurrent,
                  ]}
                />
              ))}
            </View>
          </View>
        </View>
      )}

      {/* Property chips */}
      {hasChips && (
        <View style={styles.chipsRow}>
          {parsed.propertyType && (
            <View style={styles.chip}>
              <Text style={styles.chipIcon}>{propertyIcon(parsed.propertyType)}</Text>
              <Text style={styles.chipText}>{parsed.propertyType}</Text>
            </View>
          )}
          {parsed.safePlace && (
            <View style={styles.chip}>
              <Ionicons name="exit-outline" size={11} color="#cbd5e1" />
              <Text style={styles.chipText}>{parsed.safePlace}</Text>
            </View>
          )}
          {parsed.physicalKeyAccess && (
            <View style={[styles.chip, styles.chipKey]}>
              <Ionicons name="key-outline" size={11} color={navShelfColors.chipKeyFg} />
              <Text style={[styles.chipText, { color: navShelfColors.chipKeyFg }]}>
                Key: {parsed.physicalKeyAccess}
              </Text>
            </View>
          )}
          {stop?.mobile_number && (
            <TouchableOpacity style={[styles.chip, styles.chipPhone]} onPress={callPhone} hitSlop={6}>
              <Ionicons name="call-outline" size={11} color={navShelfColors.chipPhoneFg} />
              <Text style={[styles.chipText, { color: navShelfColors.chipPhoneFg }]}>
                {stop.mobile_number}
              </Text>
            </TouchableOpacity>
          )}
          {stop?.tracking_number && (
            <TouchableOpacity style={styles.chip} onLongPress={copyTracking} hitSlop={6}>
              <Ionicons name="barcode-outline" size={11} color="#94a3b8" />
              <Text style={[styles.chipText, { color: '#94a3b8' }]} numberOfLines={1}>
                {stop.tracking_number.length > 12
                  ? `${stop.tracking_number.slice(0, 12)}…`
                  : stop.tracking_number}
              </Text>
            </TouchableOpacity>
          )}
        </View>
      )}

      {/* Free-text notes */}
      {!!parsed.freeText && (
        <View style={styles.notesRow}>
          <Ionicons name="document-text-outline" size={12} color="#94a3b8" style={{ marginTop: 1 }} />
          <Text style={styles.notesText} numberOfLines={3}>{parsed.freeText}</Text>
        </View>
      )}
    </View>
  );
};

function propertyIcon(type: string): string {
  const t = type.toUpperCase();
  if (t === 'HOUSE') return '🏠';
  if (t === 'UNIT' || t === 'APARTMENT') return '🏢';
  if (t === 'BUSINESS') return '🏪';
  return '📦';
}

const styles = StyleSheet.create({
  warnBanner: {
    backgroundColor: navColors.warnBg,
    borderWidth: 1,
    borderColor: navColors.warnBorder,
    borderRadius: 12,
    paddingVertical: 8,
    paddingHorizontal: 10,
    marginBottom: 10,
  },
  warnHeader: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  warnTitle: { color: navColors.warnTitle, fontSize: 10, fontWeight: '900', letterSpacing: 0.4 },
  warnBody: { marginTop: 3, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  warnLine: { color: navColors.warnBody, fontSize: 12, fontWeight: '600', flexShrink: 1 },
  warnBold: { color: '#fff', fontWeight: '900' },
  dotsRow: { flexDirection: 'row', alignItems: 'center', gap: 4, marginLeft: 6 },
  dot: { width: 7, height: 7, borderRadius: 3.5, backgroundColor: 'rgba(255,255,255,0.22)' },
  dotDone: { backgroundColor: '#10b981' },
  dotCurrent: { backgroundColor: '#fbbf24', width: 9, height: 9, borderRadius: 4.5 },

  chipsRow: { flexDirection: 'row', gap: 6, flexWrap: 'wrap', marginBottom: 8 },
  chip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    backgroundColor: navShelfColors.chipBg,
    borderWidth: 1,
    borderColor: navShelfColors.chipBorder,
    borderRadius: 999,
    paddingVertical: 4,
    paddingHorizontal: 9,
  },
  chipKey: {
    backgroundColor: navShelfColors.chipKeyBg,
    borderColor: navShelfColors.chipKeyBorder,
  },
  chipPhone: {
    backgroundColor: navShelfColors.chipPhoneBg,
    borderColor: navShelfColors.chipPhoneBorder,
  },
  chipIcon: { fontSize: 11 },
  chipText: { fontSize: 11, fontWeight: '600', color: '#cbd5e1' },

  notesRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 6,
    backgroundColor: navColors.ghostSoft,
    borderRadius: 10,
    paddingHorizontal: 10,
    paddingVertical: 7,
    marginBottom: 4,
  },
  notesText: { fontSize: 11, color: '#cbd5e1', flex: 1, lineHeight: 16 },
});
