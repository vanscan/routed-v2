// Styles for the main delivery screen (app/(tabs)/index.tsx).
// Extracted verbatim from index.tsx — fully static StyleSheet, no
// component or module dependencies beyond StyleSheet itself.
import { StyleSheet } from 'react-native';

export const styles = StyleSheet.create({
  offlineBannerWrap: {
    position: 'absolute',
    left: 12, right: 12,
    alignItems: 'center',
    zIndex: 10000,
    elevation: 10,
  },
  clusterWarningsWrap: {
    position: 'absolute',
    // Wrap is full-width. The banner children skip the chevron column via
    // the wrap's `paddingLeft: 56` (sidebar collapsed width).
    // Positioned at the bottom of the screen to avoid overlaying top header buttons.
    left: 0, right: 0,
    paddingLeft: 56,
    zIndex: 9000,
    elevation: 9,
  },
  offlineBanner: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
    gap: 8,
    paddingVertical: 6, paddingHorizontal: 12,
    borderRadius: 999,
    backgroundColor: '#fbbf24',   // amber-400 — visible but not alarming
  },
  offlineBannerText: {
    color: '#111827',
    fontSize: 12,
    fontWeight: '700',
    letterSpacing: 0.3,
  },
  offlineBannerPanel: {
    marginTop: 6,
    alignSelf: 'stretch',
    backgroundColor: '#fffbeb',   // amber-50
    borderRadius: 14,
    paddingVertical: 8,
    paddingHorizontal: 10,
    borderWidth: 1,
    borderColor: '#fcd34d',       // amber-300
    elevation: 6,
    shadowColor: '#000',
    shadowOpacity: 0.12,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 2 },
  },
  offlineBannerRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingVertical: 8,
    paddingHorizontal: 6,
    borderBottomWidth: 1,
    borderBottomColor: '#fde68a',  // amber-200
  },
  offlineBannerDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
  },
  offlineBannerRowLabel: {
    color: '#111827',
    fontSize: 13,
    fontWeight: '600',
  },
  offlineBannerRowMeta: {
    color: '#78350f',             // amber-900
    fontSize: 11,
    marginTop: 2,
  },
  offlineBannerFooter: {
    color: '#78350f',
    fontSize: 11,
    fontStyle: 'italic',
    textAlign: 'center',
    paddingVertical: 6,
  },
  offlineBannerActions: {
    flexDirection: 'row',
    justifyContent: 'center',
    paddingTop: 10,
    paddingBottom: 2,
  },
  offlineBannerRetryBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingVertical: 8,
    paddingHorizontal: 16,
    borderRadius: 999,
    backgroundColor: '#fcd34d',   // amber-300
    borderWidth: 1,
    borderColor: '#f59e0b',       // amber-500
  },
  offlineBannerRetryText: {
    color: '#111827',
    fontSize: 12,
    fontWeight: '700',
    letterSpacing: 0.3,
  },
  offlineBannerSwipeAction: {
    backgroundColor: '#dc2626',    // red-600
    justifyContent: 'center',
    alignItems: 'center',
    width: 96,
    paddingHorizontal: 8,
    marginVertical: 0,
  },
  offlineBannerSwipeActionText: {
    color: '#fff',
    fontSize: 11,
    fontWeight: '700',
    letterSpacing: 0.4,
    marginTop: 2,
  },
  undoToastWrap: {
    position: 'absolute',
    left: 12, right: 12,
    alignItems: 'center',
    zIndex: 10001,
    elevation: 11,
  },
  undoToast: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingVertical: 10,
    paddingHorizontal: 14,
    borderRadius: 14,
    backgroundColor: 'rgba(17, 24, 39, 0.96)',  // gray-900 @ 96%
    borderWidth: 1,
    borderColor: 'rgba(75, 85, 99, 0.6)',
    minWidth: 280,
    maxWidth: 420,
    elevation: 8,
    shadowColor: '#000',
    shadowOpacity: 0.25,
    shadowRadius: 10,
    shadowOffset: { width: 0, height: 4 },
  },
  undoToastTitle: {
    color: '#f9fafb',
    fontSize: 13,
    fontWeight: '700',
  },
  undoToastSubtitle: {
    color: '#9ca3af',
    fontSize: 11,
    marginTop: 1,
  },
  undoToastBtn: {
    paddingVertical: 6,
    paddingHorizontal: 12,
    borderRadius: 999,
    backgroundColor: 'rgba(251, 191, 36, 0.18)',
    borderWidth: 1,
    borderColor: 'rgba(251, 191, 36, 0.6)',
  },
  undoToastBtnText: {
    color: '#fbbf24',
    fontSize: 12,
    fontWeight: '800',
    letterSpacing: 0.5,
  },
  resumeToastWrap: {
    position: 'absolute',
    left: 0, right: 0,
    alignItems: 'center',
    zIndex: 10002,
    elevation: 12,
  },
  resumeToast: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingVertical: 8,
    paddingHorizontal: 14,
    borderRadius: 999,
    backgroundColor: 'rgba(22, 163, 74, 0.95)', // green-600
    borderWidth: 1,
    borderColor: 'rgba(134, 239, 172, 0.4)',
    elevation: 8,
    shadowColor: '#000',
    shadowOpacity: 0.25,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 3 },
  },
  resumeToastText: {
    color: '#f0fdf4',
    fontSize: 13,
    fontWeight: '700',
    letterSpacing: 0.2,
  },
  // Amber variant for "stay here" / multi-parcel warnings. Same pill geometry,
  // angrier colour palette so it's visually distinct from the green resume pill.
  resumeToastWarning: {
    backgroundColor: 'rgba(251, 191, 36, 0.96)',  // amber-400
    borderColor: 'rgba(120, 53, 15, 0.6)',         // amber-900
    paddingVertical: 10,
    paddingHorizontal: 16,
    maxWidth: '92%',
  },
  resumeToastTextWarning: {
    color: '#451a03',  // amber-950 — high contrast on amber-400
    fontSize: 13,
    fontWeight: '900',
    letterSpacing: 0.4,
    flexShrink: 1,
  },
  // Centred "RESUMING AT #N" card — high-contrast, scale-in, 400 ms dwell.
  resumingOverlayWrap: {
    position: 'absolute',
    top: 0, left: 0, right: 0, bottom: 0,
    justifyContent: 'center',
    alignItems: 'center',
    zIndex: 10003,
    elevation: 14,
  },
  resumingOverlay: {
    alignItems: 'center',
    paddingVertical: 18,
    paddingHorizontal: 32,
    borderRadius: 20,
    backgroundColor: 'rgba(15, 23, 42, 0.92)', // slate-900 / 92%
    borderWidth: 1,
    borderColor: 'rgba(134, 239, 172, 0.35)',
    elevation: 16,
    shadowColor: '#000',
    shadowOpacity: 0.35,
    shadowRadius: 14,
    shadowOffset: { width: 0, height: 6 },
  },
  resumingOverlayLabel: {
    color: '#86efac', // green-300
    fontSize: 11,
    fontWeight: '800',
    letterSpacing: 2.2,
    marginBottom: 2,
  },
  resumingOverlayPin: {
    color: '#f8fafc',
    fontSize: 44,
    fontWeight: '900',
    letterSpacing: 1.5,
  },
  celebrationCard: {
    position: 'absolute',
    top: 60,
    alignSelf: 'center',
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    paddingVertical: 12,
    paddingHorizontal: 16,
    minWidth: 280,
    maxWidth: 420,
    borderRadius: 14,
    backgroundColor: 'rgba(17, 24, 39, 0.92)',
    borderWidth: 1,
    borderColor: 'rgba(251, 191, 36, 0.4)',
    elevation: 8,
    shadowColor: '#000',
    shadowOpacity: 0.35,
    shadowRadius: 12,
    shadowOffset: { width: 0, height: 4 },
    zIndex: 9999,
  },
  celebrationIconWrap: {
    width: 36, height: 36, borderRadius: 18,
    alignItems: 'center', justifyContent: 'center',
    backgroundColor: 'rgba(251, 191, 36, 0.15)',
    borderWidth: 1, borderColor: 'rgba(251, 191, 36, 0.45)',
  },
  celebrationTitle: {
    color: '#fbbf24', fontSize: 15, fontWeight: '700', marginBottom: 2,
  },
  celebrationStatsLine: {
    color: '#e5e7eb', fontSize: 13, fontWeight: '500',
  },
  pausedPill: {
    position: 'absolute',
    top: 58,
    alignSelf: 'center',
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingVertical: 6,
    paddingHorizontal: 12,
    borderRadius: 999,
    backgroundColor: 'rgba(17, 24, 39, 0.85)',
    borderWidth: 1,
    borderColor: 'rgba(251, 191, 36, 0.35)',
    elevation: 6,
    shadowColor: '#000',
    shadowOpacity: 0.25,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 2 },
    zIndex: 9998,
  },
  pausedPillText: {
    color: '#fde68a',
    fontSize: 12,
    fontWeight: '600',
    letterSpacing: 0.2,
  },
  container: {
    flex: 1,
    backgroundColor: '#f8fafc',
    flexDirection: 'row',
  },
  mapContainer: {
    flex: 1,
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
  },
  map: {
    flex: 1,
  },
  mapPlaceholder: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: '#f1f5f9',
  },
  mapPlaceholderText: {
    color: '#475569',
    marginTop: 12,
  },
  
  // Circuit-style Navigation UI
  // ============================================
  // IMMERSIVE NAVIGATION STYLES - Maximum Map View
  // ============================================
  
  // Floating Turn Banner - Minimal top bar
  immersiveTurnBanner: {
    position: 'absolute',
    left: 12,
    right: 12,
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(255, 255, 255, 0.94)',
    borderRadius: 16,
    paddingVertical: 10,
    paddingHorizontal: 12,
    borderWidth: 1,
    borderColor: 'rgba(15, 23, 42, 0.08)',
    zIndex: 20,
  },
  immersiveManeuver: {
    width: 48,
    height: 48,
    borderRadius: 12,
    backgroundColor: 'rgba(59, 130, 246, 0.12)',
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: 10,
  },
  immersiveTurnInfo: {
    flex: 1,
  },
  immersiveTurnDistance: {
    color: '#1d4ed8',
    fontSize: 13,
    fontWeight: '700',
  },
  immersiveTurnText: {
    color: '#0f172a',
    fontSize: 15,
    fontWeight: '600',
  },
  immersiveExitBtn: {
    width: 36,
    height: 36,
    borderRadius: 18,
    backgroundColor: 'rgba(239, 68, 68, 0.12)',
    justifyContent: 'center',
    alignItems: 'center',
    marginLeft: 8,
  },
  
  // Floating Speed Display - Left side
  immersiveSpeedDisplay: {
    position: 'absolute',
    left: 12,
    backgroundColor: 'rgba(255, 255, 255, 0.92)',
    borderRadius: 12,
    paddingVertical: 8,
    paddingHorizontal: 12,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: 'rgba(15, 23, 42, 0.08)',
    zIndex: 15,
  },
  immersiveSpeedValue: {
    color: '#0f172a',
    fontSize: 28,
    fontWeight: '800',
  },
  immersiveSpeedUnit: {
    color: '#475569',
    fontSize: 11,
    fontWeight: '600',
  },
  
  // Stats Row - Right side (ETA + Distance)
  immersiveStatsRow: {
    position: 'absolute',
    right: 12,
    flexDirection: 'row',

    zIndex: 15,
  },
  immersiveStatChip: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(255, 255, 255, 0.92)',
    borderRadius: 10,
    paddingVertical: 6,
    paddingHorizontal: 10,

    borderWidth: 1,
    borderColor: 'rgba(15, 23, 42, 0.08)',
  },
  immersiveStatText: {
    color: '#0f172a',
    fontSize: 14,
    fontWeight: '700',
  },
  
  // Full Bottom Panel
  immersiveBottomFull: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    backgroundColor: 'rgba(255, 255, 255, 0.96)',
    borderTopLeftRadius: 24,
    borderTopRightRadius: 24,
    paddingTop: 16,
    paddingHorizontal: 16,
    borderTopWidth: 1,
    borderColor: 'rgba(15, 23, 42, 0.08)',
    zIndex: 20,
  },
  immersiveStopRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 12,
  },
  immersiveStopBadge: {
    flexDirection: 'row',
    alignItems: 'baseline',
    backgroundColor: '#2563eb',
    borderRadius: 10,
    paddingHorizontal: 10,
    paddingVertical: 6,
    marginRight: 12,
  },
  immersiveStopNum: {
    color: '#fff',
    fontSize: 20,
    fontWeight: '800',
  },
  immersiveStopOf: {
    color: 'rgba(255,255,255,0.6)',
    fontSize: 13,
    fontWeight: '500',
  },
  immersiveStopInfo: {
    flex: 1,
  },
  immersiveStopName: {
    color: '#0f172a',
    fontSize: 16,
    fontWeight: '700',
  },
  immersiveStopAddress: {
    color: '#64748b',
    fontSize: 12,
    marginTop: 2,
  },
  immersiveDetailsRow: {
    flexDirection: 'row',
    alignItems: 'center',

    marginBottom: 12,
    paddingHorizontal: 4,
  },
  immersiveDetailChip: {
    flexDirection: 'row',
    alignItems: 'center',

    backgroundColor: '#f1f5f9',
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 16,
    borderWidth: 1,
    borderColor: '#e2e8f0',
  },
  immersiveDetailText: {
    color: '#475569',
    fontSize: 13,
    fontWeight: '600',
  },
  immersiveVoiceBtn: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: '#f1f5f9',
    borderWidth: 1,
    borderColor: '#e2e8f0',
    justifyContent: 'center',
    alignItems: 'center',
  },
  
  // Quick Actions Row
  immersiveQuickRow: {
    flexDirection: 'row',
    justifyContent: 'center',

    marginBottom: 12,
  },
  immersiveQuickBtn: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: '#f1f5f9',
    borderWidth: 1,
    borderColor: '#e2e8f0',
    justifyContent: 'center',
    alignItems: 'center',
  },
  
  // Main Action Buttons
  immersiveMainActions: {
    flexDirection: 'row',
    alignItems: 'center',

    marginBottom: 8,
  },
  immersiveSkipBtn: {
    width: 56,
    height: 56,
    borderRadius: 16,
    backgroundColor: 'rgba(245, 158, 11, 0.15)',
    borderWidth: 1.5,
    borderColor: 'rgba(245, 158, 11, 0.3)',
    justifyContent: 'center',
    alignItems: 'center',
  },
  immersiveDeliveredBtn: {
    flex: 1,
    height: 56,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#10b981',
    borderRadius: 16,

  },
  immersiveDeliveredText: {
    color: '#fff',
    fontSize: 17,
    fontWeight: '700',
  },
  immersiveFailedBtn: {
    width: 56,
    height: 56,
    borderRadius: 16,
    backgroundColor: 'rgba(239, 68, 68, 0.15)',
    borderWidth: 1.5,
    borderColor: 'rgba(239, 68, 68, 0.3)',
    justifyContent: 'center',
    alignItems: 'center',
  },
  
  // Minimal Bottom Bar (Immersive Mode)
  immersiveBottomMinimal: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: 'rgba(255, 255, 255, 0.96)',
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    paddingTop: 12,
    paddingHorizontal: 16,
    borderTopWidth: 1,
    borderColor: 'rgba(15, 23, 42, 0.08)',
    zIndex: 20,
  },
  immersiveMinimalInfo: {
    flexDirection: 'row',
    alignItems: 'center',

  },
  immersiveMinimalStop: {
    color: '#334155',
    fontSize: 14,
    fontWeight: '600',
  },
  immersiveMinimalDelivered: {
    width: 56,
    height: 56,
    borderRadius: 28,
    backgroundColor: '#10b981',
    justifyContent: 'center',
    alignItems: 'center',
  },
  
  // Sidebar styles
  sidebar: {
    backgroundColor: 'rgba(255, 255, 255, 0.97)',
    borderRightWidth: 1,
    borderRightColor: '#e2e8f0',
    zIndex: 10,
  },
  sidebarHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 12,
    paddingBottom: 12,
    borderBottomWidth: 1,
    borderBottomColor: '#e2e8f0',
  },
  sidebarTitle: {
    color: '#0f172a',
    fontSize: 20,
    fontWeight: '800',
    letterSpacing: -0.5,
  },
  toggleButton: {
    width: 32,
    height: 32,
    borderRadius: 16,
    backgroundColor: '#f1f5f9',
    borderWidth: 1,
    borderColor: '#e2e8f0',
    justifyContent: 'center',
    alignItems: 'center',
  },
  headerButtons: {
    flexDirection: 'row',
    alignItems: 'center',

  },
  profileButton: {
    padding: 4,
  },
  statsCompact: {
    flexDirection: 'row',
    paddingHorizontal: 12,
    paddingVertical: 12,

    borderBottomWidth: 1,
    borderBottomColor: '#e2e8f0',
  },
  statCompactItem: {
    flexDirection: 'row',
    alignItems: 'center',

  },
  statCompactIcon: {
    width: 28,
    height: 28,
    borderRadius: 14,
    justifyContent: 'center',
    alignItems: 'center',
    borderWidth: 1,
    borderColor: 'rgba(15, 23, 42, 0.06)',
  },
  statCompactValue: {
    color: '#0f172a',
    fontSize: 14,
    fontWeight: '700',
  },
  expandedContent: {
    flex: 1,
  },
  routeStats: {
    flexDirection: 'row',
    paddingHorizontal: 12,
    paddingVertical: 10,

    borderBottomWidth: 1,
    borderBottomColor: '#e2e8f0',
  },
  routeStatRow: {
    flexDirection: 'row',
    alignItems: 'center',

  },
  routeStatText: {
    color: '#475569',
    fontSize: 13,
    fontWeight: '600',
  },
  sidebarActions: {
    padding: 12,

    borderBottomWidth: 1,
    borderBottomColor: '#e2e8f0',
  },
  actionBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#ffffff',
    paddingVertical: 12,
    paddingHorizontal: 14,
    borderRadius: 10,

    borderWidth: 1,
    borderColor: '#e2e8f0',
  },
  actionBtnText: {
    color: '#0f172a',
    fontSize: 14,
    fontWeight: '700',
  },
  actionBtnPrimary: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#3b82f6',
    paddingVertical: 12,
    paddingHorizontal: 14,
    borderRadius: 10,

  },
  actionBtnStart: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#10b981',
    paddingVertical: 12,
    paddingHorizontal: 14,
    borderRadius: 10,

  },
  actionBtnNewRoute: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: 'rgba(239, 68, 68, 0.15)',
    paddingVertical: 10,
    paddingHorizontal: 14,
    borderRadius: 10,

    borderWidth: 1,
    borderColor: 'rgba(239, 68, 68, 0.3)',
  },
  actionBtnNewRouteText: {
    color: '#ef4444',
    fontSize: 13,
    fontWeight: '500',
  },
  actionBtnClearHubs: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',

    paddingVertical: 8,
    paddingHorizontal: 12,
    borderRadius: 8,
    backgroundColor: '#fef2f2',
    borderWidth: 1,
    borderColor: '#fecaca',
    marginTop: 8,
  },
  actionBtnClearHubsText: {
    color: '#ef4444',
    fontSize: 12,
    fontWeight: '500',
  },
  hubHintContainer: {
    flexDirection: 'row',
    alignItems: 'center',

    paddingHorizontal: 12,
    paddingVertical: 8,
    marginTop: 8,
    backgroundColor: '#f9fafb',
    borderRadius: 8,
  },
  hubHintText: {
    color: '#6b7280',
    fontSize: 11,
    flex: 1,
  },
  actionBtnPrimaryText: {
    color: '#fff',
    fontSize: 14,
    fontWeight: '600',
  },
  actionBtnDisabled: {
    opacity: 0.5,
  },
  optimizeButtonContainer: {
    flexDirection: 'row',
    alignItems: 'stretch',
    borderRadius: 12,
    overflow: 'hidden',
    borderWidth: 1,
    borderColor: '#e2e8f0',
    backgroundColor: '#ffffff',
  },
  optimizeMainBtn: {
    flex: 1,
    borderTopRightRadius: 0,
    borderBottomRightRadius: 0,
    borderRightWidth: 1,
    borderRightColor: '#e2e8f0',
  },
  algorithmPickerBtn: {
    backgroundColor: '#2563eb',
    paddingHorizontal: 12,
    justifyContent: 'center',
    alignItems: 'center',
  },
  stopsSection: {
    flex: 1,
    paddingTop: 12,
  },
  stopsSectionHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: 12,
    marginBottom: 8,
  },
  stopsSectionTitle: {
    color: '#64748b',
    fontSize: 12,
    fontWeight: '700',
    letterSpacing: 0.5,
    textTransform: 'uppercase',
  },
  stopsCount: {
    color: '#3b82f6',
    fontSize: 12,
    fontWeight: '600',
  },
  stopsList: {
    flex: 1,
    paddingHorizontal: 8,
  },
  emptyState: {
    alignItems: 'center',
    paddingVertical: 40,
  },
  emptyStateText: {
    color: '#64748b',
    fontSize: 14,
    fontWeight: '500',
    marginTop: 12,
  },
  stopItem: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#ffffff',
    borderRadius: 10,
    padding: 10,
    marginBottom: 6,
    borderWidth: 1,
    borderColor: '#e2e8f0',
  },
  stopItemCompleted: {
    backgroundColor: 'rgba(16, 185, 129, 0.1)',
    borderWidth: 1,
    borderColor: 'rgba(16, 185, 129, 0.2)',
  },
  stopItemPressed: {
    backgroundColor: '#e2e8f0',
  },
  stopIndex: {
    minWidth: 26,
    height: 26,
    borderRadius: 13,
    backgroundColor: '#3b82f6',
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: 10,
    paddingHorizontal: 6,
  },
  stopIndexCompleted: {
    backgroundColor: '#10b981',
  },
  stopIndexHigh: {
    backgroundColor: '#ef4444',
  },
  stopIndexLow: {
    backgroundColor: '#6b7280',
  },
  stopIndexText: {
    color: '#fff',
    fontSize: 11,
    fontWeight: '700',
  },
  stopInfo: {
    flex: 1,
  },
  stopName: {
    color: '#0f172a',
    fontSize: 14,
    fontWeight: '700',
  },
  stopNameCompleted: {
    color: '#64748b',
    textDecorationLine: 'line-through',
  },
  stopWeight: {
    color: '#64748b',
    fontSize: 12,
    marginTop: 4,
    fontWeight: '500',
  },
  collapsedActions: {
    flex: 1,
    paddingVertical: 12,
    alignItems: 'center',

  },
  collapsedBtn: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: '#f1f5f9',
    borderWidth: 1,
    borderColor: '#e2e8f0',
    justifyContent: 'center',
    alignItems: 'center',
  },
  collapsedBtnStart: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: '#10b981',
    justifyContent: 'center',
    alignItems: 'center',
  },
  collapsedBtnStop: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: '#ef4444',
    justifyContent: 'center',
    alignItems: 'center',
  },
  collapsedBtnDisabled: {
    opacity: 0.4,
  },
  collapsedBtnNewRoute: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: '#fef2f2',
    borderWidth: 1,
    borderColor: '#fecaca',
    justifyContent: 'center',
    alignItems: 'center',
    marginTop: 4,
  },
  // Suburb grouping styles
  suburbGroup: {
    marginBottom: 12,
  },
  suburbHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 8,
    paddingHorizontal: 4,

  },
  suburbTitle: {
    color: '#8b5cf6',
    fontSize: 13,
    fontWeight: '600',
    flex: 1,
  },
  suburbCount: {
    color: '#475569',
    fontSize: 12,
    fontWeight: '700',
    backgroundColor: '#f1f5f9',
    borderWidth: 1,
    borderColor: '#e2e8f0',
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: 10,
  },
  // Drag and drop styles
  reorderToggle: {
    width: 32,
    height: 32,
    borderRadius: 8,
    backgroundColor: '#f1f5f9',
    borderWidth: 1,
    borderColor: '#e2e8f0',
    justifyContent: 'center',
    alignItems: 'center',
    marginLeft: 8,
  },
  reorderToggleActive: {
    backgroundColor: 'rgba(16, 185, 129, 0.2)',
  },
  dragListContainer: {
    flex: 1,
  },
  dragHint: {
    color: '#64748b',
    fontSize: 12,
    textAlign: 'center',
    marginBottom: 8,
    fontStyle: 'italic',
  },
  dragHandle: {
    width: 24,
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: 4,
  },
  stopItemDragging: {
    backgroundColor: '#f1f5f9',
    borderColor: '#2563eb',
    borderWidth: 2,
    transform: [{ scale: 1.02 }],
  },
  stopSuburb: {
    color: '#64748b',
    fontSize: 11,
    marginTop: 2,
  },
  
  // Algorithm Modal Styles
  modalOverlay: {
    flex: 1,
    backgroundColor: 'rgba(15, 23, 42, 0.45)',
    justifyContent: 'center',
    alignItems: 'center',
    padding: 20,
  },
  algorithmModal: {
    backgroundColor: '#ffffff',
    borderRadius: 20,
    padding: 20,
    width: '100%',
    maxWidth: 400,
    maxHeight: '80%',
    borderWidth: 1,
    borderColor: '#e2e8f0',
  },
  algorithmModalHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 8,
  },
  algorithmModalTitle: {
    fontSize: 20,
    fontWeight: '800',
    color: '#0f172a',
  },
  algorithmModalSubtitle: {
    fontSize: 14,
    color: '#475569',
    marginBottom: 16,
    fontWeight: '600',
  },
  algorithmList: {
    maxHeight: 350,
  },
  algorithmOption: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#f8fafc',
    borderRadius: 12,
    padding: 14,
    marginBottom: 10,
    borderWidth: 1,
    borderColor: '#e2e8f0',
  },
  algorithmOptionSelected: {
    borderColor: '#2563eb',
    backgroundColor: 'rgba(37, 99, 235, 0.08)',
  },
  algorithmOptionContent: {
    flex: 1,
  },
  algorithmOptionName: {
    fontSize: 16,
    fontWeight: '700',
    color: '#0f172a',
    marginBottom: 4,
  },
  algorithmOptionNameSelected: {
    color: '#2563eb',
  },
  algorithmOptionDesc: {
    fontSize: 13,
    color: '#475569',
    fontWeight: '600',
  },
  algorithmOptionRecommended: {
    borderColor: '#f59e0b',
    backgroundColor: 'rgba(245, 158, 11, 0.06)',
  },
  recommendationBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    backgroundColor: 'rgba(245, 158, 11, 0.1)',
    borderRadius: 8,
    padding: 10,
    marginBottom: 8,
  },
  recommendationText: {
    fontSize: 13,
    color: '#92400e',
    flex: 1,
  },
  recommendationHighlight: {
    fontWeight: '700',
    color: '#b45309',
  },
  recommendedBadge: {
    backgroundColor: '#f59e0b',
    borderRadius: 4,
    paddingHorizontal: 6,
    paddingVertical: 2,
  },
  recommendedBadgeText: {
    fontSize: 9,
    fontWeight: '800',
    color: '#fff',
  },
  recommendationReason: {
    fontSize: 11,
    color: '#92400e',
    fontStyle: 'italic',
    marginTop: 4,
  },
  algorithmApplyBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#2563eb',
    borderRadius: 12,
    paddingVertical: 14,
    marginTop: 16,

  },
  algorithmApplyText: {
    fontSize: 16,
    fontWeight: '700',
    color: '#fff',
  },
  // Stop Modal Styles
  stopModalOverlay: {
    flex: 1,
    backgroundColor: 'rgba(0, 0, 0, 0.5)',
    justifyContent: 'flex-end',
  },
  stopModalBackdrop: {
    flex: 1,
  },
  stopModalContent: {
    backgroundColor: '#fff',
    borderTopLeftRadius: 24,
    borderTopRightRadius: 24,
    padding: 24,
    paddingBottom: 32,
    maxHeight: '85%',
  },
  stopModalHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 20,
  },
  stopModalBadge: {
    width: 48,
    height: 48,
    borderRadius: 24,
    justifyContent: 'center',
    alignItems: 'center',
  },
  stopModalBadgeText: {
    fontSize: 18,
    fontWeight: '700',
    color: '#fff',
  },
  stopModalClose: {
    padding: 8,
  },
  stopModalBody: {
    marginBottom: 24,
  },
  stopModalScrollBody: {
    flexGrow: 0,
  },
  stopModalFieldLabel: {
    fontSize: 12,
    fontWeight: '700',
    color: '#64748b',
    textTransform: 'uppercase',
    letterSpacing: 0.4,
    marginBottom: 8,
  },
  stopModalAddressInput: {
    borderWidth: 1,
    borderColor: '#cbd5e1',
    borderRadius: 12,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 16,
    color: '#0f172a',
    lineHeight: 22,
    backgroundColor: '#f8fafc',
    minHeight: 58,
    marginBottom: 10,
  },
  stopModalAddress: {
    fontSize: 18,
    fontWeight: '600',
    color: '#1e293b',
    marginBottom: 12,
    lineHeight: 24,
  },
  stopModalNeedsFixBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    alignSelf: 'flex-start',
    backgroundColor: 'rgba(245, 158, 11, 0.15)',
    borderWidth: 1,
    borderColor: 'rgba(245, 158, 11, 0.35)',
    borderRadius: 999,
    paddingHorizontal: 10,
    paddingVertical: 5,
    marginBottom: 10,
  },
  stopModalNeedsFixText: {
    color: '#b45309',
    fontSize: 12,
    fontWeight: '700',
    marginLeft: 6,
  },
  stopModalAddressActions: {
    flexDirection: 'row',
    gap: 8,
    marginBottom: 14,
  },
  stopModalAddressBtn: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: 10,
    paddingVertical: 10,
    paddingHorizontal: 12,
  },
  stopModalAddressSaveBtn: {
    backgroundColor: '#334155',
  },
  stopModalAddressGeocodeBtn: {
    backgroundColor: '#2563eb',
  },
  stopModalAddressBtnDisabled: {
    opacity: 0.65,
  },
  stopModalAddressBtnText: {
    color: '#fff',
    fontSize: 13,
    fontWeight: '700',
    marginLeft: 6,
  },
  stopModalSuburbContainer: {
    flexDirection: 'row',
    alignItems: 'center',

    marginBottom: 16,
  },
  stopModalSuburb: {
    fontSize: 14,
    color: '#64748b',
    fontWeight: '500',
  },
  stopModalStatus: {
    flexDirection: 'row',
    alignItems: 'center',

  },
  stopModalStatusText: {
    fontSize: 14,
    fontWeight: '600',
  },
  stopModalNotesCard: {
    marginTop: 16,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: '#bfdbfe',
    backgroundColor: '#eff6ff',
    padding: 12,
  },
  stopModalNotesHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  stopModalNotesTitle: {
    color: '#1d4ed8',
    fontSize: 13,
    fontWeight: '700',
  },
  stopModalNotesInput: {
    marginTop: 10,
    minHeight: 88,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: '#dbeafe',
    backgroundColor: '#ffffff',
    paddingHorizontal: 12,
    paddingVertical: 10,
    color: '#0f172a',
    fontSize: 14,
    lineHeight: 20,
  },
  stopModalNotesSaveBtn: {
    marginTop: 10,
    alignSelf: 'flex-end',
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    backgroundColor: '#2563eb',
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: 999,
  },
  stopModalActions: {

  },
  stopModalBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 14,
    paddingHorizontal: 20,
    borderRadius: 12,

  },
  stopModalBtnComplete: {
    backgroundColor: '#10b981',
  },
  stopModalBtnNavigate: {
    backgroundColor: '#3b82f6',
  },
  stopModalBtnInsert: {
    // Distinct teal — not green (Mark Complete) and not blue (Navigate),
    // so the driver doesn't conflate "wedge a courtesy stop" with either
    // a finishing action or a re-route action. Sits between Delete
    // (destructive, red) and Mark Complete (success, green) in the row.
    backgroundColor: '#0ea5e9',
  },
  stopModalBtnDelete: {
    backgroundColor: '#dc2626',
  },
  stopModalBtnText: {
    fontSize: 16,
    fontWeight: '600',
    color: '#fff',
  },
  // Refine Mode Styles
  refineModeContainer: {
    backgroundColor: '#fafafa',
    borderRadius: 12,
    padding: 12,
    marginTop: 8,
    marginBottom: 8,
    borderWidth: 1,
    borderColor: '#e2e8f0',
  },
  // New Bottom Bar Styles for Refine Mode
  floatingRefinePanel: {
    position: 'absolute',
    left: 16,
    right: 16,
    backgroundColor: 'rgba(15, 23, 42, 0.92)',
    borderRadius: 20,
    padding: 16,
    zIndex: 20,
    elevation: 10,
  },
  refineModeBottomBar: {
    backgroundColor: 'rgba(15, 23, 42, 0.92)',
    borderRadius: 16,
    padding: 16,
    marginTop: 8,
    marginBottom: 8,
  },
  drawingStatusBar: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(254, 243, 199, 0.15)',
    padding: 12,
    borderRadius: 12,
  },
  drawingStatusText: {
    flex: 1,
    fontSize: 14,
    fontWeight: '600',
    color: '#fbbf24',
    marginLeft: 10,
  },
  cancelDrawingBtn: {
    padding: 4,
  },
  sectionSummary: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    marginBottom: 12,
  },
  sectionPill: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 6,
    paddingHorizontal: 10,
    borderRadius: 20,
    marginRight: 8,
    marginBottom: 6,
  },
  sectionPillDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    marginRight: 6,
  },
  sectionPillText: {
    fontSize: 12,
    fontWeight: '600',
  },
  refineActionRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 12,
  },
  refineActionBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 10,
    paddingHorizontal: 14,
    borderRadius: 10,
    backgroundColor: 'rgba(255, 255, 255, 0.1)',
  },
  refineActionBtnDisabled: {
    opacity: 0.5,
  },
  refineActionBtnText: {
    fontSize: 13,
    fontWeight: '600',
    color: '#e2e8f0',
    marginLeft: 6,
  },
  refineActionBtnTextDisabled: {
    color: '#94a3b8',
  },
  drawNextGroupBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 12,
    paddingHorizontal: 16,
    borderRadius: 10,
    backgroundColor: 'rgba(139, 92, 246, 0.3)',
    borderWidth: 1,
    borderColor: '#8b5cf6',
  },
  drawNextGroupBtnText: {
    fontSize: 14,
    fontWeight: '600',
    color: '#c4b5fd',
    marginLeft: 8,
  },
  reoptimizeBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 12,
    paddingHorizontal: 16,
    borderRadius: 10,
    backgroundColor: '#3b82f6',
  },
  reoptimizeBtnDisabled: {
    backgroundColor: '#93c5fd',
  },
  reoptimizeBtnText: {
    fontSize: 14,
    fontWeight: '600',
    color: '#fff',
    marginLeft: 6,
  },
  exitRefineBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 10,
    borderTopWidth: 1,
    borderTopColor: 'rgba(255, 255, 255, 0.1)',
    marginTop: 4,
  },
  exitRefineBtnText: {
    fontSize: 13,
    fontWeight: '500',
    color: 'rgba(255, 255, 255, 0.6)',
    marginLeft: 6,
  },
  refineModeHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 8,
  },
  refineModeTitle: {
    flexDirection: 'row',
    alignItems: 'center',

  },
  refineModeHeaderText: {
    fontSize: 16,
    fontWeight: '700',
    color: '#0f172a',
  },
  refineModeHint: {
    fontSize: 13,
    color: '#64748b',
    marginBottom: 12,
    lineHeight: 18,
  },
  startDrawingBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#fff',
    paddingVertical: 14,
    paddingHorizontal: 20,
    borderRadius: 12,
    borderWidth: 2,
    borderColor: '#8b5cf6',

    marginBottom: 12,
  },
  startDrawingBtnActive: {
    backgroundColor: '#fef2f2',
    borderColor: '#ef4444',
  },
  startDrawingBtnText: {
    fontSize: 16,
    fontWeight: '700',
    color: '#8b5cf6',
  },
  startDrawingBtnTextActive: {
    color: '#ef4444',
  },
  drawingActiveIndicator: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#fef3c7',
    padding: 10,
    borderRadius: 8,
    marginBottom: 12,

  },
  drawingPulse: {
    width: 10,
    height: 10,
    borderRadius: 5,
    backgroundColor: '#f59e0b',
  },
  drawingActiveText: {
    fontSize: 13,
    fontWeight: '600',
    color: '#92400e',
    flex: 1,
  },
  sectionList: {
    marginBottom: 12,

  },
  sectionItem: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#fff',
    padding: 10,
    borderRadius: 8,

    borderWidth: 1,
    borderColor: '#e2e8f0',
  },
  sectionBadge: {
    width: 28,
    height: 28,
    borderRadius: 14,
    justifyContent: 'center',
    alignItems: 'center',
  },
  sectionBadgeText: {
    fontSize: 14,
    fontWeight: '700',
    color: '#fff',
  },
  sectionItemText: {
    fontSize: 14,
    fontWeight: '500',
    color: '#374151',
  },
  refineModeEmpty: {
    alignItems: 'center',
    paddingVertical: 20,

  },
  refineModeEmptyText: {
    fontSize: 14,
    color: '#9ca3af',
    fontWeight: '500',
  },
  refineModeActions: {
    flexDirection: 'row',
    justifyContent: 'center',

    marginBottom: 12,
  },
  refineModeActionBtn: {
    flexDirection: 'row',
    alignItems: 'center',

    paddingVertical: 8,
    paddingHorizontal: 12,
  },
  refineModeActionBtnText: {
    fontSize: 14,
    fontWeight: '600',
    color: '#64748b',
  },
  refineModeApplyBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#8b5cf6',
    paddingVertical: 14,
    paddingHorizontal: 20,
    borderRadius: 12,

  },
  refineModeApplyBtnText: {
    fontSize: 16,
    fontWeight: '700',
    color: '#fff',
  },
  floatingRefineEntryBtn: {
    position: 'absolute',
    right: 16,
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#7c3aed',
    paddingVertical: 12,
    paddingHorizontal: 18,
    borderRadius: 24,
    zIndex: 15,
    elevation: 8,
    shadowColor: '#7c3aed',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.3,
    shadowRadius: 8,
  },
  floatingRefineEntryBtnText: {
    color: '#fff',
    fontSize: 14,
    fontWeight: '700',
    marginLeft: 8,
  },
  // Confirm Route — positioned above the Refine pill, amber/green palette
  // so it reads as the primary commit CTA against the neutral map.
  confirmRouteBtn: {
    position: 'absolute',
    right: 16,
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#10b981',
    paddingVertical: 14,
    paddingHorizontal: 22,
    borderRadius: 28,
    zIndex: 16,
    elevation: 10,
    shadowColor: '#10b981',
    shadowOffset: { width: 0, height: 6 },
    shadowOpacity: 0.45,
    shadowRadius: 10,
  },
  confirmRouteBtnText: {
    color: '#0f172a',
    fontSize: 15,
    fontWeight: '800',
    marginLeft: 8,
    letterSpacing: 0.3,
  },
  parcelToggleBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(15, 23, 42, 0.75)',
    borderRadius: 20,
    paddingVertical: 6,
    paddingHorizontal: 12,
  },
  parcelToggleBtnText: {
    color: '#94a3b8',
    fontSize: 12,
    fontWeight: '600',
    marginLeft: 5,
  },
});
