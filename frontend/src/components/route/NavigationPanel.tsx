import React, { useState } from 'react';
import { Stop } from '../../store/stopsStore';
import { ViewMode } from '../../types/route';
import { ShelfState } from './nav/navTheme';
import { NavSettings } from './nav/useNavSettings';
import { TurnPill } from './nav/TurnPill';
import { DriveShelf } from './nav/DriveShelf';
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
  shelfState,
  currentStep,
  currentLeg,
  stops,
  currentLegIndex,
  speedKmh,
  etaToNextStop,
  completedCount,
  insets,
  liveRoute,
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
  onCallCustomer,
  onShareETA,
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
        shelfState={shelfState}
        topOffset={insets.top + 8}
      />

      <DriveShelf
        shelfState={shelfState}
        settings={navSettings}
        currentStep={currentStep}
        currentLeg={currentLeg}
        stops={stops}
        currentLegIndex={currentLegIndex}
        speedKmh={speedKmh}
        etaToNextStop={etaToNextStop}
        completedCount={completedCount}
        insets={insets}
        liveRoute={liveRoute}
        legs={legs}
        canPreviewNext={canPreviewNext}
        canPreviewPrev={canPreviewPrev}
        onOpenSettings={() => setSettingsOpen(true)}
        onExpandRequest={onExpandRequest}
        onMarkDelivered={onMarkDelivered}
        onMarkFailed={onMarkFailed}
        onSkipStop={onSkipStop}
        onStopNavigation={onStopNavigation}
        onCallCustomer={onCallCustomer}
        onShareETA={onShareETA}
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
