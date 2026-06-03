// Enhanced Supabase Database helpers with better error handling and typing
import { Platform } from 'react-native';

// Lazy import to avoid SSR issues
const getSupabase = async () => {
  if (Platform.OS === 'web' && typeof window === 'undefined') {
    throw new Error('Supabase cannot be used during SSR');
  }
  const { supabase } = await import('../lib/supabase');
  return supabase;
};

export interface QueryOptions<T = unknown> {
  select?: string;
  filter?: Record<string, unknown>;
  filters?: Array<{
    column: string;
    operator: 'eq' | 'neq' | 'gt' | 'gte' | 'lt' | 'lte' | 'like' | 'ilike' | 'is' | 'in';
    value: unknown;
  }>;
  order?: { column: string; ascending?: boolean } | Array<{ column: string; ascending?: boolean }>;
  limit?: number;
  offset?: number;
  single?: boolean;
}

export interface DatabaseResult<T> {
  data: T | null;
  error: Error | null;
}

/**
 * Generic query helper for Supabase tables
 */
export async function queryTable<T>(
  table: string,
  options?: QueryOptions<T>
): Promise<DatabaseResult<T[]>> {
  try {
    const supabase = await getSupabase();
    let query = supabase.from(table).select(options?.select || '*');

    // Simple equality filters
    if (options?.filter) {
      Object.entries(options.filter).forEach(([key, value]) => {
        query = query.eq(key, value);
      });
    }

    // Advanced filters
    if (options?.filters) {
      options.filters.forEach(({ column, operator, value }) => {
        switch (operator) {
          case 'eq':
            query = query.eq(column, value);
            break;
          case 'neq':
            query = query.neq(column, value);
            break;
          case 'gt':
            query = query.gt(column, value);
            break;
          case 'gte':
            query = query.gte(column, value);
            break;
          case 'lt':
            query = query.lt(column, value);
            break;
          case 'lte':
            query = query.lte(column, value);
            break;
          case 'like':
            query = query.like(column, value as string);
            break;
          case 'ilike':
            query = query.ilike(column, value as string);
            break;
          case 'is':
            query = query.is(column, value as null | boolean);
            break;
          case 'in':
            query = query.in(column, value as unknown[]);
            break;
        }
      });
    }

    // Ordering
    if (options?.order) {
      const orders = Array.isArray(options.order) ? options.order : [options.order];
      orders.forEach(({ column, ascending }) => {
        query = query.order(column, { ascending: ascending ?? true });
      });
    }

    // Pagination
    if (options?.limit) {
      query = query.limit(options.limit);
    }

    if (options?.offset) {
      query = query.range(
        options.offset,
        options.offset + (options.limit || 10) - 1
      );
    }

    const { data, error } = await query;
    if (error) {
      return { data: null, error: new Error(error.message) };
    }
    return { data: data as T[], error: null };
  } catch (error) {
    return { data: null, error: error as Error };
  }
}

/**
 * Get a single record by ID
 */
export async function getById<T>(
  table: string,
  id: string | number,
  idColumn: string = 'id'
): Promise<DatabaseResult<T>> {
  try {
    const supabase = await getSupabase();
    const { data, error } = await supabase
      .from(table)
      .select('*')
      .eq(idColumn, id)
      .single();

    if (error) {
      return { data: null, error: new Error(error.message) };
    }
    return { data: data as T, error: null };
  } catch (error) {
    return { data: null, error: error as Error };
  }
}

/**
 * Insert data into a table
 */
export async function insertIntoTable<T>(
  table: string,
  data: Partial<T> | Partial<T>[]
): Promise<DatabaseResult<T[]>> {
  try {
    const supabase = await getSupabase();
    const { data: result, error } = await supabase
      .from(table)
      .insert(data)
      .select();

    if (error) {
      return { data: null, error: new Error(error.message) };
    }
    return { data: result as T[], error: null };
  } catch (error) {
    return { data: null, error: error as Error };
  }
}

/**
 * Upsert (insert or update) data in a table
 */
export async function upsertIntoTable<T>(
  table: string,
  data: Partial<T> | Partial<T>[],
  options?: { onConflict?: string; ignoreDuplicates?: boolean }
): Promise<DatabaseResult<T[]>> {
  try {
    const supabase = await getSupabase();
    const { data: result, error } = await supabase
      .from(table)
      .upsert(data, {
        onConflict: options?.onConflict,
        ignoreDuplicates: options?.ignoreDuplicates,
      })
      .select();

    if (error) {
      return { data: null, error: new Error(error.message) };
    }
    return { data: result as T[], error: null };
  } catch (error) {
    return { data: null, error: error as Error };
  }
}

/**
 * Update data in a table
 */
export async function updateTable<T>(
  table: string,
  data: Partial<T>,
  filter: Record<string, unknown>
): Promise<DatabaseResult<T[]>> {
  try {
    const supabase = await getSupabase();
    let query = supabase.from(table).update(data);

    Object.entries(filter).forEach(([key, value]) => {
      query = query.eq(key, value);
    });

    const { data: result, error } = await query.select();
    if (error) {
      return { data: null, error: new Error(error.message) };
    }
    return { data: result as T[], error: null };
  } catch (error) {
    return { data: null, error: error as Error };
  }
}

/**
 * Delete data from a table
 */
export async function deleteFromTable(
  table: string,
  filter: Record<string, unknown>
): Promise<{ error: Error | null }> {
  try {
    const supabase = await getSupabase();
    let query = supabase.from(table).delete();

    Object.entries(filter).forEach(([key, value]) => {
      query = query.eq(key, value);
    });

    const { error } = await query;
    if (error) {
      return { error: new Error(error.message) };
    }
    return { error: null };
  } catch (error) {
    return { error: error as Error };
  }
}

/**
 * Subscribe to realtime changes on a table
 */
export async function subscribeToTable(
  table: string,
  callback: (payload: {
    eventType: 'INSERT' | 'UPDATE' | 'DELETE';
    new: Record<string, unknown>;
    old: Record<string, unknown>;
  }) => void,
  options?: {
    event?: 'INSERT' | 'UPDATE' | 'DELETE' | '*';
    filter?: string;
    schema?: string;
  }
): Promise<() => void> {
  const supabase = await getSupabase();
  const channelName = `${table}-changes-${Date.now()}`;
  
  const channel = supabase
    .channel(channelName)
    .on(
      'postgres_changes',
      {
        event: options?.event || '*',
        schema: options?.schema || 'public',
        table: table,
        filter: options?.filter,
      },
      (payload: any) => {
        callback({
          eventType: payload.eventType,
          new: payload.new || {},
          old: payload.old || {},
        });
      }
    )
    .subscribe();

  return () => {
    supabase.removeChannel(channel);
  };
}

/**
 * Execute a Supabase RPC function
 */
export async function callRpc<T>(
  functionName: string,
  params?: Record<string, unknown>
): Promise<DatabaseResult<T>> {
  try {
    const supabase = await getSupabase();
    const { data, error } = await supabase.rpc(functionName, params);

    if (error) {
      return { data: null, error: new Error(error.message) };
    }
    return { data: data as T, error: null };
  } catch (error) {
    return { data: null, error: error as Error };
  }
}

/**
 * Count records in a table
 */
export async function countRecords(
  table: string,
  filter?: Record<string, unknown>
): Promise<DatabaseResult<number>> {
  try {
    const supabase = await getSupabase();
    let query = supabase.from(table).select('*', { count: 'exact', head: true });

    if (filter) {
      Object.entries(filter).forEach(([key, value]) => {
        query = query.eq(key, value);
      });
    }

    const { count, error } = await query;
    if (error) {
      return { data: null, error: new Error(error.message) };
    }
    return { data: count ?? 0, error: null };
  } catch (error) {
    return { data: null, error: error as Error };
  }
}
