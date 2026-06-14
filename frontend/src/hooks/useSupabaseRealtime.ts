// Supabase Realtime subscription hook for live updates
import { useState, useEffect, useCallback, useRef } from 'react';
import { getSupabase } from '../lib/supabase';
import { useSupabase } from '../contexts/SupabaseContext';
import type { RealtimeChannel, RealtimePostgresChangesPayload } from '@supabase/supabase-js';

export type RealtimeEvent = 'INSERT' | 'UPDATE' | 'DELETE' | '*';

export interface RealtimeSubscription<T> {
  data: T[];
  isConnected: boolean;
  error: string | null;
  unsubscribe: () => void;
}

export interface UseRealtimeOptions<T> {
  table: string;
  schema?: string;
  event?: RealtimeEvent;
  filter?: string; // e.g., 'user_id=eq.123'
  onInsert?: (payload: T) => void;
  onUpdate?: (payload: { old: T; new: T }) => void;
  onDelete?: (payload: T) => void;
  initialData?: T[];
}

/**
 * Hook for subscribing to realtime database changes via Supabase
 * 
 * Usage:
 * ```
 * const { data, isConnected, error } = useRealtime<Stop>({
 *   table: 'stops',
 *   filter: `user_id=eq.${userId}`,
 *   onInsert: (stop) => console.log('New stop:', stop),
 * });
 * ```
 */
export function useRealtime<T extends Record<string, any> = any>(
  options: UseRealtimeOptions<T>
): RealtimeSubscription<T> {
  const { user } = useSupabase();
  const [data, setData] = useState<T[]>(options.initialData || []);
  const [isConnected, setIsConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const channelRef = useRef<RealtimeChannel | null>(null);

  const unsubscribe = useCallback(() => {
    if (channelRef.current) {
      console.log('[Realtime] Unsubscribing from channel');
      channelRef.current.unsubscribe();
      channelRef.current = null;
      setIsConnected(false);
    }
  }, []);

  useEffect(() => {
    // Only subscribe if user is authenticated
    if (!user) {
      console.log('[Realtime] No user, skipping subscription');
      return;
    }

    const setupSubscription = async () => {
      try {
        const supabase = getSupabase();
        
        const channelName = `${options.schema || 'public'}_${options.table}_${user.id}`;
        console.log('[Realtime] Setting up subscription:', channelName);

        // Build the subscription config
        const subscriptionConfig: any = {
          event: options.event || '*',
          schema: options.schema || 'public',
          table: options.table,
        };

        if (options.filter) {
          subscriptionConfig.filter = options.filter;
        }

        const channel = (supabase
          .channel(channelName) as any)
          .on(
            'postgres_changes',
            subscriptionConfig,
            (payload: RealtimePostgresChangesPayload<T>) => {
              console.log('[Realtime] Change received:', payload.eventType, payload);

              switch (payload.eventType) {
                case 'INSERT':
                  setData(prev => [...prev, payload.new as T]);
                  options.onInsert?.(payload.new as T);
                  break;

                case 'UPDATE':
                  setData(prev =>
                    prev.map(item =>
                      (item as any).id === (payload.new as any).id
                        ? (payload.new as T)
                        : item
                    )
                  );
                  options.onUpdate?.({
                    old: payload.old as T,
                    new: payload.new as T,
                  });
                  break;

                case 'DELETE':
                  setData(prev =>
                    prev.filter(item => (item as any).id !== (payload.old as any).id)
                  );
                  options.onDelete?.(payload.old as T);
                  break;
              }
            }
          )
          .subscribe((status: string) => {
            console.log('[Realtime] Subscription status:', status);
            if (status === 'SUBSCRIBED') {
              setIsConnected(true);
              setError(null);
            } else if (status === 'CHANNEL_ERROR') {
              setError('Failed to subscribe to realtime updates');
              setIsConnected(false);
            } else if (status === 'TIMED_OUT') {
              setError('Realtime subscription timed out');
              setIsConnected(false);
            }
          });

        channelRef.current = channel;
      } catch (err: any) {
        console.error('[Realtime] Setup error:', err);
        setError(err.message || 'Failed to setup realtime subscription');
      }
    };

    setupSubscription();

    // Cleanup on unmount
    return () => {
      unsubscribe();
    };
  }, [user, options.table, options.schema, options.event, options.filter]);

  return {
    data,
    isConnected,
    error,
    unsubscribe,
  };
}

/**
 * Hook for presence tracking (who's online)
 */
export function usePresence(channelName: string) {
  const { user } = useSupabase();
  const [presenceState, setPresenceState] = useState<Record<string, any[]>>({});
  const [isConnected, setIsConnected] = useState(false);
  const channelRef = useRef<RealtimeChannel | null>(null);

  useEffect(() => {
    if (!user) return;

    const setupPresence = async () => {
      try {
        const supabase = getSupabase();
        
        const channel = supabase.channel(channelName, {
          config: {
            presence: {
              key: user.id,
            },
          },
        });

        channel
          .on('presence', { event: 'sync' }, () => {
            const state = channel.presenceState();
            console.log('[Presence] Synced:', state);
            setPresenceState(state);
          })
          .on('presence', { event: 'join' }, ({ key, newPresences }) => {
            console.log('[Presence] Join:', key, newPresences);
          })
          .on('presence', { event: 'leave' }, ({ key, leftPresences }) => {
            console.log('[Presence] Leave:', key, leftPresences);
          })
          .subscribe(async (status) => {
            if (status === 'SUBSCRIBED') {
              setIsConnected(true);
              // Track this user's presence
              await channel.track({
                user_id: user.id,
                email: user.email,
                online_at: new Date().toISOString(),
              });
            }
          });

        channelRef.current = channel;
      } catch (err) {
        console.error('[Presence] Setup error:', err);
      }
    };

    setupPresence();

    return () => {
      if (channelRef.current) {
        channelRef.current.unsubscribe();
        channelRef.current = null;
      }
    };
  }, [user, channelName]);

  const getOnlineUsers = useCallback(() => {
    return Object.values(presenceState).flat();
  }, [presenceState]);

  return {
    presenceState,
    isConnected,
    getOnlineUsers,
  };
}
