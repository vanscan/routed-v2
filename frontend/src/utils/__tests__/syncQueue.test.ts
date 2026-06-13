import AsyncStorage from '@react-native-async-storage/async-storage';
import { enqueue, removeById, getQueuedIds, getQueuedActions, flush } from '../syncQueue';

const mockStore: Record<string, string> = {};

beforeEach(() => {
  jest.clearAllMocks();
  Object.keys(mockStore).forEach((k) => delete mockStore[k]);

  (AsyncStorage.getItem as jest.Mock).mockImplementation(async (key: string) =>
    mockStore[key] ?? null
  );
  (AsyncStorage.setItem as jest.Mock).mockImplementation(
    async (key: string, value: string) => {
      mockStore[key] = value;
    }
  );
});

describe('enqueue', () => {
  it('adds a new action; getQueuedIds returns it', async () => {
    await enqueue({ id: 'stop-1', op: 'complete' });
    const ids = await getQueuedIds();
    expect(ids.has('stop-1')).toBe(true);
  });

  it('enqueueing the same id twice replaces the old entry (idempotent)', async () => {
    await enqueue({ id: 'stop-1', op: 'complete' });
    await enqueue({ id: 'stop-1', op: 'uncomplete' });
    const actions = await getQueuedActions();
    expect(actions).toHaveLength(1);
    expect(actions[0].op).toBe('uncomplete');
  });

  it('two different ids both appear in the queue', async () => {
    await enqueue({ id: 'stop-1', op: 'complete' });
    await enqueue({ id: 'stop-2', op: 'complete' });
    const ids = await getQueuedIds();
    expect(ids.has('stop-1')).toBe(true);
    expect(ids.has('stop-2')).toBe(true);
  });

  it('stored action includes a timestamp', async () => {
    const before = Date.now();
    await enqueue({ id: 'stop-1', op: 'complete' });
    const actions = await getQueuedActions();
    expect(actions[0].ts).toBeGreaterThanOrEqual(before);
  });
});

describe('removeById', () => {
  it('removes a queued action', async () => {
    await enqueue({ id: 'stop-1', op: 'complete' });
    await removeById('stop-1');
    const ids = await getQueuedIds();
    expect(ids.has('stop-1')).toBe(false);
  });

  it('is a no-op when the id is not in the queue', async () => {
    await enqueue({ id: 'stop-1', op: 'complete' });
    await removeById('stop-999');
    const ids = await getQueuedIds();
    expect(ids.has('stop-1')).toBe(true);
  });

  it('does not write to storage when nothing changed', async () => {
    const setItemCalls = (AsyncStorage.setItem as jest.Mock).mock.calls.length;
    await removeById('not-there');
    expect((AsyncStorage.setItem as jest.Mock).mock.calls.length).toBe(setItemCalls);
  });
});

describe('flush', () => {
  it('returns 0 on empty queue', async () => {
    const fetcher = jest.fn();
    expect(await flush(fetcher, 'http://localhost')).toBe(0);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('returns flushed count and empties queue on all-success', async () => {
    await enqueue({ id: 'stop-1', op: 'complete' });
    await enqueue({ id: 'stop-2', op: 'complete' });
    const fetcher = jest.fn().mockResolvedValue({ ok: true } as Response);

    const count = await flush(fetcher, 'http://api');
    expect(count).toBe(2);
    expect((await getQueuedIds()).size).toBe(0);
  });

  it('keeps failed actions in the queue', async () => {
    await enqueue({ id: 'stop-1', op: 'complete' });
    await enqueue({ id: 'stop-2', op: 'complete' });
    const fetcher = jest
      .fn()
      .mockResolvedValueOnce({ ok: true } as Response)
      .mockResolvedValueOnce({ ok: false } as Response);

    const count = await flush(fetcher, 'http://api');
    expect(count).toBe(1);
    const remaining = await getQueuedIds();
    expect(remaining.has('stop-2')).toBe(true);
    expect(remaining.has('stop-1')).toBe(false);
  });

  it('keeps actions whose fetcher threw a network error', async () => {
    await enqueue({ id: 'stop-1', op: 'complete' });
    const fetcher = jest.fn().mockRejectedValue(new Error('Network error'));

    const count = await flush(fetcher, 'http://api');
    expect(count).toBe(0);
    expect((await getQueuedIds()).has('stop-1')).toBe(true);
  });

  it('calls fetcher with correct URL and method', async () => {
    await enqueue({ id: 'abc', op: 'complete' });
    const fetcher = jest.fn().mockResolvedValue({ ok: true } as Response);

    await flush(fetcher, 'http://api');
    expect(fetcher).toHaveBeenCalledWith('http://api/api/stops/abc/complete', { method: 'POST' });
  });
});
