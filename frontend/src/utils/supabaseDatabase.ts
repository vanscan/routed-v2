// Supabase Database helpers
import { supabase } from '../lib/supabase';

/**
 * Generic query helper for Supabase tables
 */
export async function queryTable<T>(
  table: string,
  options?: {
    select?: string;
    filter?: Record<string, any>;
    order?: { column: string; ascending?: boolean };
    limit?: number;
    offset?: number;
  }
) {
  let query = supabase.from(table).select(options?.select || '*');

  if (options?.filter) {
    Object.entries(options.filter).forEach(([key, value]) => {
      query = query.eq(key, value);
    });
  }

  if (options?.order) {
    query = query.order(options.order.column, {
      ascending: options.order.ascending ?? true,
    });
  }

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
  if (error) throw error;
  return data as T[];
}

/**
 * Insert data into a table
 */
export async function insertIntoTable<T>(
  table: string,
  data: Partial<T> | Partial<T>[]
) {
  const { data: result, error } = await supabase
    .from(table)
    .insert(data)
    .select();

  if (error) throw error;
  return result as T[];
}

/**
 * Update data in a table
 */
export async function updateTable<T>(
  table: string,
  data: Partial<T>,
  filter: Record<string, any>
) {
  let query = supabase.from(table).update(data);

  Object.entries(filter).forEach(([key, value]) => {
    query = query.eq(key, value);
  });

  const { data: result, error } = await query.select();
  if (error) throw error;
  return result as T[];
}

/**
 * Delete data from a table
 */
export async function deleteFromTable(
  table: string,
  filter: Record<string, any>
) {
  let query = supabase.from(table).delete();

  Object.entries(filter).forEach(([key, value]) => {
    query = query.eq(key, value);
  });

  const { error } = await query;
  if (error) throw error;
}

/**
 * Subscribe to realtime changes on a table
 */
export function subscribeToTable(
  table: string,
  callback: (payload: any) => void,
  options?: {
    event?: 'INSERT' | 'UPDATE' | 'DELETE' | '*';
    filter?: string;
  }
) {
  const channel = supabase
    .channel(`${table}-changes`)
    .on(
      'postgres_changes',
      {
        event: options?.event || '*',
        schema: 'public',
        table: table,
        filter: options?.filter,
      },
      callback
    )
    .subscribe();

  return () => {
    supabase.removeChannel(channel);
  };
}
