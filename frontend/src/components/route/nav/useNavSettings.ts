import { useState, useEffect, useCallback } from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { MapStyleKey } from '../../map/MapStyleSwitcher';

const STORAGE_KEY = 'routed:navSettings:v1';

type CardSensitivity = 'tight' | 'normal' | 'wide';
type SpeedUnits = 'kmh' | 'mph';

export interface NavSettings {
  voiceEnabled: boolean;
  mapStyle: MapStyleKey;
  cardSensitivity: CardSensitivity;
  speedUnits: SpeedUnits;
}

const DEFAULTS: NavSettings = {
  voiceEnabled: true,
  mapStyle: 'colorful',
  cardSensitivity: 'normal',
  speedUnits: 'kmh',
};

const SENSITIVITY_SHOW: Record<CardSensitivity, number> = {
  tight: 20,
  normal: 30,
  wide: 50,
};

export function useNavSettings() {
  const [settings, setSettings] = useState<NavSettings>(DEFAULTS);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    AsyncStorage.getItem(STORAGE_KEY).then((raw) => {
      if (cancelled || !raw) { setLoaded(true); return; }
      try {
        const parsed = JSON.parse(raw) as Partial<NavSettings>;
        setSettings((prev) => ({ ...prev, ...parsed }));
      } catch {}
      setLoaded(true);
    });
    return () => { cancelled = true; };
  }, []);

  const updateSettings = useCallback((patch: Partial<NavSettings>) => {
    setSettings((prev) => {
      const next = { ...prev, ...patch };
      AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(next)).catch(() => {});
      return next;
    });
  }, []);

  const cardShowRadiusM = SENSITIVITY_SHOW[settings.cardSensitivity];
  const cardHideRadiusM = cardShowRadiusM + 20;

  return { settings, updateSettings, cardShowRadiusM, cardHideRadiusM, loaded };
}
