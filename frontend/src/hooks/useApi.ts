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

interface RefetchOptions {
  force?: boolean;
}

interface UseApiResult<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  /** HTTP status behind `error`; null when the request never reached the server. */
  errorStatus: number | null;
  /**
   * Fetch again and resolve with the data. Rejects when the request fails, so
   * await it inside try/catch. Do not pass it straight to `onClick`: use
   * `retry` for that.
   */
  refetch: (overridePath?: string, opts?: RefetchOptions) => Promise<T | null>;
  /**
   * Event-handler-safe refetch: ignores its arguments and never rejects. A
   * failure lands in `error` / `errorStatus` like any other failed load.
   */
  retry: () => void;
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
    async (overridePath?: string, opts?: RefetchOptions): Promise<T | null> => {
      // `refetch` is routinely wired to buttons. A caller that passes it
      // straight to onClick hands us the click event here, which used to be
      // interpolated into the URL as "/api[object Object]". Only a real path
      // string overrides the hook's own path.
      const target = typeof overridePath === "string" && overridePath ? overridePath : path;
      const force = typeof opts?.force === "boolean" ? opts.force : true;
      if (!target) return null;

      controllerRef.current?.abort();
      const controller = new AbortController();
      controllerRef.current = controller;

      // Loading means "nothing to show yet". Set it both ways: a request that
      // was superseded no longer clears it (see the finally below), so a switch
      // to a path that is already cached has to clear it here.
      const targetRecord = getCachedApi<T>(target);
      setLoading(targetRecord.data === undefined);

      try {
        return await prefetchApi<T>(target, { force, signal: controller.signal, ttl });
      } catch (err) {
        if ((err as Error).name !== "AbortError") throw err;
        return null;
      } finally {
        // A request superseded by a newer one must not end the newer one's
        // loading state (double refetch, path change, StrictMode remount).
        if (controllerRef.current === controller) setLoading(false);
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

  // Logout and session expiry clear the whole cache. A public page that stays
  // mounted through that (a listing detail, the hubs list) lost its data with
  // nothing to fetch it again, and rendered its "not found" state. Refetch
  // when the entry this hook was showing is evicted. Deferred a tick so a
  // component that unmounts in the same pass (a protected page redirecting to
  // /login) never fires a request.
  const populatedPathRef = useRef<string | null>(null);
  const evicted = cacheRecord.updatedAt === 0;
  useEffect(() => {
    if (!evicted) {
      populatedPathRef.current = path ?? null;
      return undefined;
    }
    if (skip || !path || populatedPathRef.current !== path) return undefined;
    populatedPathRef.current = null;
    const timer = setTimeout(() => {
      execute().catch(() => {});
    }, 0);
    return () => clearTimeout(timer);
  }, [evicted, execute, path, skip]);

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

  const retry = useCallback((): void => {
    // The failure is already recorded in the cache (and so in `error`); the
    // rejection itself has no other consumer when called from a click handler.
    execute().catch(() => undefined);
  }, [execute]);

  const data = cacheRecord.data !== undefined ? cacheRecord.data : (initialData ?? null);
  const error = cacheRecord.error;
  const errorStatus = cacheRecord.errorStatus;

  return { data, loading, error, errorStatus, refetch: execute, retry, setData };
}
