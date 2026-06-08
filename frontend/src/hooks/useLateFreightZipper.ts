/**
 * Web stub for useLateFreightZipper.
 * The zipper hook and map components require @maplibre/maplibre-react-native
 * which is native-only. Metro resolves the .native.tsx variant on device.
 */

export type PlannedStop = {
  id: string;
  label: string;
  lat: number;
  lon: number;
  original_sequence: number | null;
  is_late_freight: boolean;
};

export function useLateFreightZipper() {
  return {
    route: [] as PlannedStop[],
    inserting: false,
    error: null as string | null,
    zip: async (_stops: Array<Omit<PlannedStop, 'label' | 'is_late_freight'> & { is_depot?: boolean }>): Promise<PlannedStop[] | null> => null,
  };
}

export const ZipperRouteLayer = (_props: { route: PlannedStop[] }) => null;

export function FollowCamera() {
  return null;
}
