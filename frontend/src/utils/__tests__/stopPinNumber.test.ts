import { stopPinNumber, stopPinLabel } from '../stopPinNumber';

describe('stopPinNumber', () => {
  it('returns original_sequence when it is a valid number', () => {
    expect(stopPinNumber({ original_sequence: 3 })).toBe(3);
  });

  it('returns 1 for original_sequence=1', () => {
    expect(stopPinNumber({ original_sequence: 1 })).toBe(1);
  });

  it('returns null for null stop', () => {
    expect(stopPinNumber(null)).toBeNull();
  });

  it('returns null for undefined stop', () => {
    expect(stopPinNumber(undefined)).toBeNull();
  });

  it('returns null when original_sequence is null', () => {
    expect(stopPinNumber({ original_sequence: null })).toBeNull();
  });

  it('returns null when original_sequence is undefined', () => {
    expect(stopPinNumber({ original_sequence: undefined })).toBeNull();
  });

  it('returns null when original_sequence is NaN — the sharpie-marker contract', () => {
    expect(stopPinNumber({ original_sequence: NaN })).toBeNull();
  });

  it('returns null for empty object with no original_sequence', () => {
    expect(stopPinNumber({})).toBeNull();
  });

  it('returns the number as-is (no +1 adjustment)', () => {
    expect(stopPinNumber({ original_sequence: 10 })).toBe(10);
  });
});

describe('stopPinLabel', () => {
  it('returns locked sequence string when original_sequence is set', () => {
    expect(stopPinLabel({ original_sequence: 5 }, 0, false)).toBe('5');
  });

  it('locked sequence wins even when route is confirmed', () => {
    expect(stopPinLabel({ original_sequence: 2 }, 0, true)).toBe('2');
  });

  it('locked sequence wins regardless of index argument', () => {
    expect(stopPinLabel({ original_sequence: 7 }, 99, true)).toBe('7');
  });

  it('returns ★ for late freight (confirmed route, no sequence)', () => {
    expect(stopPinLabel({ original_sequence: null }, 3, true)).toBe('★');
  });

  it('returns ★ when original_sequence is undefined and route confirmed', () => {
    expect(stopPinLabel({}, 0, true)).toBe('★');
  });

  it('NaN original_sequence falls through to ★ when confirmed', () => {
    expect(stopPinLabel({ original_sequence: NaN }, 2, true)).toBe('★');
  });

  it('returns index+1 string in planning mode (not confirmed)', () => {
    expect(stopPinLabel({ original_sequence: null }, 4, false)).toBe('5');
  });

  it('returns "1" for first stop in planning mode (index 0)', () => {
    expect(stopPinLabel({}, 0, false)).toBe('1');
  });

  it('returns correct index+1 for last stop in planning mode', () => {
    expect(stopPinLabel({ original_sequence: undefined }, 9, false)).toBe('10');
  });
});
