/**
 * navTheme.ts — design tokens + tunable constants for the driving-mode UI.
 *
 * No design-system existed before this; these values centralise the
 * "modern nav-app" look (dark fake-glass surfaces + blue/green gradients)
 * so the header and proximity card stay visually consistent. We deliberately
 * do NOT use expo-blur: the map camera animates via refs every 250ms during
 * driving and a real BlurView re-snapshots the layer behind it continuously
 * on Android (GPU regression) for a marginal gain over high-opacity slate.
 */

export const navColors = {
  // Fake-glass surfaces over the moving map.
  surface: 'rgba(15, 23, 42, 0.90)',
  hairline: 'rgba(255, 255, 255, 0.10)',
  divider: 'rgba(255, 255, 255, 0.08)',

  textPrimary: '#ffffff',
  textSecondary: '#e2e8f0',
  textMuted: '#94a3b8',
  textFaint: '#64748b',

  // Gradients (consumed by expo-linear-gradient — already in the binary).
  blueGrad: ['#2563eb', '#60a5fa'] as const,
  greenGrad: ['#059669', '#10b981'] as const,
  lateFreight: '#7c3aed',

  etaPillBg: 'rgba(16, 185, 129, 0.16)',
  etaPillText: '#34d399',

  failedBg: 'rgba(239, 68, 68, 0.14)',
  failedBorder: 'rgba(239, 68, 68, 0.25)',
  failedFg: '#f87171',
  skipBg: 'rgba(245, 158, 11, 0.14)',
  skipBorder: 'rgba(245, 158, 11, 0.25)',
  skipFg: '#fbbf24',

  warnBg: 'rgba(245, 158, 11, 0.16)',
  warnBorder: 'rgba(245, 158, 11, 0.35)',
  warnTitle: '#fbbf24',
  warnBody: '#fde68a',

  ghost: 'rgba(255, 255, 255, 0.10)',
  ghostSoft: 'rgba(255, 255, 255, 0.06)',
};

export const navRadii = {
  header: 24,
  card: 28,
  tile: 18,
  tileSm: 10,
  button: 16,
  buttonLg: 18,
  pill: 999,
};

/**
 * Vertical space (px, below insets.top) the unified header occupies, including
 * its top margin. Other top-anchored overlays (LastMilePrecisionHUD, the
 * paused pill) offset by this during navigation so they clear the header.
 */
export const NAV_HEADER_CLEARANCE = 132;

/**
 * Proximity-card thresholds (metres). The action card shows when the driver is
 * within SHOW of the current stop and hides once they pass beyond HIDE. The
 * 20-in / 40-out hysteresis prevents flapping from consumer-GPS wander (±5–15m)
 * while parked at the doorstep. Visibility is driven by zone *crossings*, so a
 * manual dismiss persists until the driver actually leaves and returns.
 */
export const CARD_SHOW_RADIUS_M = 20;
export const CARD_HIDE_RADIUS_M = 40;
