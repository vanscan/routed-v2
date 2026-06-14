import React, { useState } from 'react';
import { Stop } from '../../store/stopsStore';
import { ViewMode } from '../../types/route';
import { NavSettings } from './nav/useNavSettings';
import { DriverBillboard } from './nav/DriverBillboard';
import { BinaryActionBar } from './nav/BinaryActionBar';
import { SettingsPanel } from './nav/SettingsPanel';

interface NavigationPanelProps {
  viewMode: ViewMode;
  currentStep: any;
  currentLeg: any;
  stops: Stop[];
  currentLegIndex: number;
  speedKmh: number;
  etaToNextStop: string;
  completedCount: number;
  insets: { top: number; bottom: number };
  liveRoute: any;
  navSettings: NavSettings;
  onSettingsChange: (patch: Partial<NavSettings>) => void;
  legs?: any[];
  canPreviewNext?: boolean;
  canPreviewPrev?: boolean;

  onExpandRequest: () => void;
  onStopNavigation: () => void;
  onMarkDelivered: () => void;
  onMarkFailed: (reason?: string) => void;
  onSkipStop: () => void;
  onCallCustomer: () => void;
  onShareETA: () => void;
  onPreviewNextStop?: () => void;
  onPreviewPrevStop?: () => void;
  onJumpToStop?: (index: number) => void;
  onShowDetails?: () => void;
}

export const NavigationPanel: React.FC<NavigationPanelProps> = ({
  currentStep,
  currentLeg,
  insets,
  navSettings,
  onSettingsChange,
  onStopNavigation,
  onMarkDelivered,
  onMarkFailed,
}) => {
  const [settingsOpen, setSettingsOpen] = useState(false);

  return (
    <>
      <DriverBillboard
        stop={currentLeg?.to_stop ?? null}
        currentStep={currentStep}
        insets={insets}
      />

      <BinaryActionBar
        onMarkDelivered={onMarkDelivered}
        onMarkFailed={onMarkFailed}
        insets={insets}
      />

      <SettingsPanel
        visible={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        settings={navSettings}
        onSettingsChange={onSettingsChange}
        onStopNavigation={onStopNavigation}
      />
    </>
  );
};

export default NavigationPanel;
