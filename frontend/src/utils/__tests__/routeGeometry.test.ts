import { normaliseLineCoordinates, toRouteFeatureCollection } from '../routeGeometry';

describe('normaliseLineCoordinates', () => {
  it('returns [] for null input', () => {
    expect(normaliseLineCoordinates(null)).toEqual([]);
  });

  it('returns [] for undefined input', () => {
    expect(normaliseLineCoordinates(undefined)).toEqual([]);
  });

  it('returns [] for empty array', () => {
    expect(normaliseLineCoordinates([])).toEqual([]);
  });

  it('passes valid [lng, lat] pairs through unchanged', () => {
    const coords = [[153.02, -27.47], [151.21, -33.87]];
    const result = normaliseLineCoordinates(coords);
    expect(result).toEqual([[153.02, -27.47], [151.21, -33.87]]);
  });

  it('strips pairs containing NaN', () => {
    const coords = [[153.02, -27.47], [NaN, -27.47], [151.21, -33.87]];
    const result = normaliseLineCoordinates(coords);
    expect(result).toHaveLength(2);
  });

  it('strips pairs containing Infinity', () => {
    const coords = [[153.02, -27.47], [Infinity, -27.0]];
    const result = normaliseLineCoordinates(coords);
    expect(result).toHaveLength(1);
  });

  it('strips pairs with longitude out of range (>180)', () => {
    const coords = [[181.0, -27.47]];
    expect(normaliseLineCoordinates(coords)).toEqual([]);
  });

  it('strips pairs with longitude out of range (<-180)', () => {
    const coords = [[-181.0, -27.47]];
    expect(normaliseLineCoordinates(coords)).toEqual([]);
  });

  it('strips pairs with latitude out of range (>90)', () => {
    const coords = [[153.0, 91.0]];
    expect(normaliseLineCoordinates(coords)).toEqual([]);
  });

  it('strips exact consecutive duplicate points', () => {
    const coords = [[153.02, -27.47], [153.02, -27.47], [151.21, -33.87]];
    const result = normaliseLineCoordinates(coords);
    expect(result).toHaveLength(2);
    expect(result[0]).toEqual([153.02, -27.47]);
    expect(result[1]).toEqual([151.21, -33.87]);
  });

  it('keeps non-consecutive duplicates', () => {
    const coords = [[153.02, -27.47], [151.21, -33.87], [153.02, -27.47]];
    expect(normaliseLineCoordinates(coords)).toHaveLength(3);
  });

  it('returns a new array (not the same reference)', () => {
    const coords = [[153.02, -27.47]];
    const result = normaliseLineCoordinates(coords);
    expect(result).not.toBe(coords);
  });

  it('with autoFlipLatLng detects and flips [lat, lng] Brisbane coords', () => {
    // Brisbane in [lat, lng] format: [-27.47, 153.02] — first value in lat range, second is longitude
    const coords = [[-27.47, 153.02], [-33.87, 151.21]];
    const result = normaliseLineCoordinates(coords, { autoFlipLatLng: true });
    expect(result[0]).toEqual([153.02, -27.47]);
    expect(result[1]).toEqual([151.21, -33.87]);
  });

  it('without autoFlipLatLng does NOT flip coords', () => {
    const coords = [[-27.47, 153.02], [-33.87, 151.21]];
    const result = normaliseLineCoordinates(coords, { autoFlipLatLng: false });
    // -27.47 as lng is valid; 153.02 as lat is out of range → stripped
    expect(result).toHaveLength(0);
  });
});

describe('toRouteFeatureCollection', () => {
  it('returns empty FeatureCollection for null input', () => {
    const fc = toRouteFeatureCollection(null);
    expect(fc.type).toBe('FeatureCollection');
    expect(fc.features).toHaveLength(0);
  });

  it('returns empty FeatureCollection for single point (needs ≥2 for LineString)', () => {
    const fc = toRouteFeatureCollection([[153.02, -27.47]]);
    expect(fc.features).toHaveLength(0);
  });

  it('returns a FeatureCollection with one LineString feature for valid input', () => {
    const fc = toRouteFeatureCollection([[153.02, -27.47], [151.21, -33.87]]);
    expect(fc.type).toBe('FeatureCollection');
    expect(fc.features).toHaveLength(1);
    expect(fc.features[0].type).toBe('Feature');
    expect(fc.features[0].geometry.type).toBe('LineString');
  });

  it('geometry coordinates match the cleaned input', () => {
    const coords = [[153.02, -27.47], [151.21, -33.87]];
    const fc = toRouteFeatureCollection(coords);
    expect(fc.features[0].geometry.coordinates).toEqual(coords);
  });

  it('each call returns a different object reference (fresh object for MapLibre diff)', () => {
    const coords = [[153.02, -27.47], [151.21, -33.87]];
    const a = toRouteFeatureCollection(coords);
    const b = toRouteFeatureCollection(coords);
    expect(a).not.toBe(b);
    expect(a.features[0]).not.toBe(b.features[0]);
  });

  it('merges custom properties into the feature', () => {
    const fc = toRouteFeatureCollection(
      [[153.02, -27.47], [151.21, -33.87]],
      { properties: { color: 'red', speed: 60 } }
    );
    expect(fc.features[0].properties).toMatchObject({ color: 'red', speed: 60 });
  });

  it('strips NaN points; returns empty if fewer than 2 valid remain', () => {
    const fc = toRouteFeatureCollection([[153.02, -27.47], [NaN, -27.0]]);
    expect(fc.features).toHaveLength(0);
  });

  it('returns empty FeatureCollection for empty array', () => {
    const fc = toRouteFeatureCollection([]);
    expect(fc.features).toHaveLength(0);
  });
});
