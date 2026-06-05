import React, { useEffect, useState } from 'react';
import { View, Text } from 'react-native';
import { BACKEND_URL } from '../../utils/config';

interface CarStop {
  id: string;
  name?: string;
  address: string;
  order: number;
  mobile_number?: string;
  delivery_status?: string;
}

export const CarMainScreen = () => {
  const [loading, setLoading] = useState(true);
  const [stops, setStops] = useState<CarStop[]>([]);

  const loadStops = async () => {
    if (!BACKEND_URL) {
      setStops([]);
      setLoading(false);
      return;
    }
    try {
      setLoading(true);
      const response = await fetch(`${BACKEND_URL}/api/car/next-stops?limit=20`, {
        credentials: 'include',
      });
      const data = await response.json();
      setStops(Array.isArray(data) ? data : []);
    } catch (error) {
      setStops([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadStops();
  }, []);

  return (
    <View>
      <Text>Android Auto runtime disabled for current deployment build.</Text>
      <Text>{loading ? 'Loading stops...' : `Stops loaded: ${stops.length}`}</Text>
    </View>
  );
};
