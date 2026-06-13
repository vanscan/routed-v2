import {
  calculateDistance,
  formatDistance,
  formatDuration,
  getSuburbColor,
  isPointInPolygon,
  extractPhoneNumber,
} from '../route';

describe('calculateDistance', () => {
  it('same point returns 0', () => {
    expect(calculateDistance(-27.47, 153.02, -27.47, 153.02)).toBe(0);
  });

  it('Brisbane to Sydney is roughly 730 km', () => {
    const dist = calculateDistance(-27.4705, 153.026, -33.8688, 151.2093);
    expect(dist).toBeGreaterThan(720_000);
    expect(dist).toBeLessThan(740_000);
  });

  it('is symmetric: dist(A,B) === dist(B,A)', () => {
    const ab = calculateDistance(-27.47, 153.02, -33.87, 151.21);
    const ba = calculateDistance(-33.87, 151.21, -27.47, 153.02);
    expect(ab).toBeCloseTo(ba, 5);
  });
});

describe('formatDistance', () => {
  it('very short distances round to nearest 50, minimum 50 m', () => {
    expect(formatDistance(10)).toBe('50 m');
    expect(formatDistance(30)).toBe('50 m');
    expect(formatDistance(75)).toBe('100 m');
  });

  it('250–499 m rounds to nearest 50 m', () => {
    expect(formatDistance(260)).toBe('250 m');
    expect(formatDistance(480)).toBe('500 m');
  });

  it('500–999 m rounds to nearest 100 m', () => {
    expect(formatDistance(500)).toBe('500 m');
    expect(formatDistance(750)).toBe('800 m');
    expect(formatDistance(999)).toBe('1000 m');
  });

  it('1000–9999 m rounds to nearest 0.5 km', () => {
    expect(formatDistance(1000)).toBe('1 km');
    expect(formatDistance(1200)).toBe('1 km');
    expect(formatDistance(1300)).toBe('1.5 km');
    expect(formatDistance(2000)).toBe('2 km');
  });

  it('≥10000 m shows whole kilometres', () => {
    expect(formatDistance(10000)).toBe('10 km');
    expect(formatDistance(15000)).toBe('15 km');
    expect(formatDistance(99999)).toBe('100 km');
  });
});

describe('formatDuration', () => {
  it('formats sub-minute durations as "0 min"', () => {
    expect(formatDuration(30)).toBe('0 min');
  });

  it('formats 90 seconds as "1 min"', () => {
    expect(formatDuration(90)).toBe('1 min');
  });

  it('formats exactly 1 hour', () => {
    expect(formatDuration(3600)).toBe('1h 0m');
  });

  it('formats 1 hour 1 minute', () => {
    expect(formatDuration(3660)).toBe('1h 1m');
  });

  it('formats 2 hours 30 minutes', () => {
    expect(formatDuration(9000)).toBe('2h 30m');
  });
});

describe('getSuburbColor', () => {
  it('returns grey for null', () => {
    expect(getSuburbColor(null)).toBe('#6b7280');
  });

  it('returns grey for undefined', () => {
    expect(getSuburbColor(undefined)).toBe('#6b7280');
  });

  it('returns grey for empty string', () => {
    expect(getSuburbColor('')).toBe('#6b7280');
  });

  it('returns the same color for the same suburb (deterministic)', () => {
    expect(getSuburbColor('Fortitude Valley')).toBe(getSuburbColor('Fortitude Valley'));
  });

  it('returns a valid hex color string starting with #', () => {
    const color = getSuburbColor('Newstead');
    expect(color).toMatch(/^#[0-9a-fA-F]{6}$/);
  });

  it('different suburbs can return different colors', () => {
    const colors = new Set(
      ['Newstead', 'Teneriffe', 'Bowen Hills', 'Albion', 'Windsor'].map(getSuburbColor)
    );
    expect(colors.size).toBeGreaterThan(1);
  });
});

describe('isPointInPolygon', () => {
  const square = [
    { lat: 0, lng: 0 },
    { lat: 1, lng: 0 },
    { lat: 1, lng: 1 },
    { lat: 0, lng: 1 },
  ];

  it('point inside the polygon returns true', () => {
    expect(isPointInPolygon({ lat: 0.5, lng: 0.5 }, square)).toBe(true);
  });

  it('point outside the polygon returns false', () => {
    expect(isPointInPolygon({ lat: 2, lng: 2 }, square)).toBe(false);
  });

  it('point far outside returns false', () => {
    expect(isPointInPolygon({ lat: -10, lng: -10 }, square)).toBe(false);
  });

  it('polygon with fewer than 3 points returns false', () => {
    const line = [{ lat: 0, lng: 0 }, { lat: 1, lng: 1 }];
    expect(isPointInPolygon({ lat: 0.5, lng: 0.5 }, line)).toBe(false);
  });

  it('empty polygon returns false', () => {
    expect(isPointInPolygon({ lat: 0, lng: 0 }, [])).toBe(false);
  });
});

describe('extractPhoneNumber', () => {
  it('returns null for null stop', () => {
    expect(extractPhoneNumber(null)).toBeNull();
  });

  it('returns null for undefined stop', () => {
    expect(extractPhoneNumber(undefined)).toBeNull();
  });

  it('returns cleaned mobile_number when valid (≥8 digits)', () => {
    expect(extractPhoneNumber({ mobile_number: '0412 345 678' })).toBe('0412345678');
  });

  it('returns null when mobile_number is too short (<8 digits after cleaning)', () => {
    expect(extractPhoneNumber({ mobile_number: '1234' })).toBeNull();
  });

  it('strips formatting characters from mobile_number', () => {
    expect(extractPhoneNumber({ mobile_number: '+61-412-345-678' })).toBe('+61412345678');
  });

  it('extracts phone number from notes when mobile_number is absent', () => {
    const stop = { notes: 'Call before delivery: 0412 345 678' };
    const result = extractPhoneNumber(stop);
    expect(result).not.toBeNull();
    expect(result!.replace(/\D/g, '')).toMatch(/0412345678/);
  });

  it('returns null when no phone anywhere', () => {
    expect(extractPhoneNumber({ notes: 'Leave at front door', mobile_number: '' })).toBeNull();
  });
});
