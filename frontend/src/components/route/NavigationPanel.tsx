import React, { useState } from 'react';
import { Stop } from '../../store/stopsStore';
import { ViewMode } from '../../types/route';
import { ShelfState } from './nav/navTheme';
import { NavSettings } from './nav/useNavSettings';
import { TurnPill } from './nav/TurnPill';
import { NavCard } from './nav/NavCard';
import { SettingsPanel } from './nav/SettingsPanel';

interface NavigationPanelProps {
  viewMode: ViewMode;
  shelfState: ShelfState;
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
  onMarkFailed: () => void;
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
  stops,
  currentLegIndex,
  speedKmh,
  etaToNextStop,
  insets,
  navSettings,
  onSettingsChange,
  legs,
  canPreviewNext = true,
  canPreviewPrev = true,
  onExpandRequest,
  onStopNavigation,
  onMarkDelivered,
  onMarkFailed,
  onSkipStop,
  onPreviewNextStop,
  onPreviewPrevStop,
  onJumpToStop,
  onShowDetails,
}) => {
  const [settingsOpen, setSettingsOpen] = useState(false);

  return (
    <>
      <TurnPill
        currentStep={currentStep}
        topOffset={insets.top + 8}
      />

      <NavCard
        settings={navSettings}
        stops={stops}
        currentLeg={currentLeg}
        currentLegIndex={currentLegIndex}
        etaToNextStop={etaToNextStop}
        speedKmh={speedKmh}
        insets={insets}
        legs={legs}
        canPreviewNext={canPreviewNext}
        canPreviewPrev={canPreviewPrev}
        onOpenSettings={() => setSettingsOpen(true)}
        onExpandRequest={onExpandRequest}
        onMarkDelivered={onMarkDelivered}
        onMarkFailed={onMarkFailed}
        onSkipStop={onSkipStop}
        onShowDetails={onShowDetails}
        onJumpToStop={onJumpToStop}
        onPreviewNextStop={onPreviewNextStop}
        onPreviewPrevStop={onPreviewPrevStop}
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
