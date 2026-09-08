import { useState, useEffect, useCallback, useRef, useSyncExternalStore } from "react";

import {
  DEFAULT_TTL_MS,
  getCachedApi,
  prefetchApi,
  primeApiCache,
  subscribeApiCache,
} from "../lib/apiCache";

interface UseApiOptions<T> {
  initialData?: T;
  skip?: boolean;
  ttl?: number;
}

interface UseApiResult<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  refetch: (overridePath?: string, opts?: { force?: boolean }) => Promise<T | null>;
  setData: (valueOrUpdater: T | ((prev: T | null) => T)) => void;
}

export function useApi<T = unknown>(
  path: string | null | undefined,
  { initialData, skip = false, ttl = DEFAULT_TTL_MS }: UseApiOptions<T> = {},
): UseApiResult<T> {
  const controllerRef = useRef<AbortController | null>(null);

  const subscribe = useCallback(
    (onStoreChange: () => void) =>
      path ? subscribeApiCache(path, onStoreChange) : () => {},
    [path],
  );
  const getSnapshot = useCallback(() => getCachedApi<T>(path), [path]);
  const cacheRecord = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);

  // initialData is a render fallback ("show an empty list, not a crash"), not
  // fetched data. Treating it as fetched made `loading` permanently false, so
  // pages that pass it rendered their empty state ("You're all caught up.")
  // during the very first fetch and their skeleton branches were dead code.
  const [loading, setLoading] = useState<boolean>(
    () => !skip && !!path && cacheRecord.data === undefined,
  );

  const execute = useCallback(
    async (
      overridePath?: string,
      { force = true }: { force?: boolean } = {},
    ): Promise<T | null> => {
      const target = overridePath ?? path;
      if (!target) return null;

      controllerRef.current?.abort();
      controllerRef.current = new AbortController();

      const targetRecord = getCachedApi<T>(target);
      if (targetRecord.data === undefined) setLoading(true);

      try {
        return await prefetchApi<T>(target, {
          force,
          signal: controllerRef.current.signal,
          ttl,
        });
      } catch (err) {
        if ((err as Error).name !== "AbortError") throw err;
        return null;
      } finally {
        setLoading(false);
      }
    },
    [path, ttl],
  );

  useEffect(() => {
    if (skip || !path) {
      setLoading(false);
      return () => controllerRef.current?.abort();
    }
    execute().catch(() => {});
    return () => controllerRef.current?.abort();
  }, [execute, path, skip]);

  const setData = useCallback(
    (valueOrUpdater: T | ((prev: T | null) => T)): void => {
      if (!path) return;
      const currentRecord = getCachedApi<T>(path);
      const currentValue =
        currentRecord.data !== undefined ? currentRecord.data : (initialData ?? null);
      const nextValue =
        typeof valueOrUpdater === "function"
          ? (valueOrUpdater as (prev: T | null) => T)(currentValue)
          : valueOrUpdater;
      primeApiCache(path, nextValue);
    },
    [initialData, path],
  );

  const data = cacheRecord.data !== undefined ? cacheRecord.data : (initialData ?? null);
  const error = cacheRecord.error;

  return { data, loading, error, refetch: execute, setData };
}
