import { apiFetch } from "./api";

export const DEFAULT_TTL_MS = 60_000;

interface CacheRecord<T = unknown> {
  data: T | undefined;
  error: string | null;
  updatedAt: number;
}

type CacheListener = () => void;

const EMPTY_RECORD: CacheRecord = Object.freeze({ data: undefined, error: null, updatedAt: 0 });

const cacheStore = new Map<string, CacheRecord>();
const listeners = new Map<string, Set<CacheListener>>();
const inflightRequests = new Map<string, Promise<unknown>>();

function emitCacheChange(key: string): void {
  listeners.get(key)?.forEach((l) => l());
}

function normalizeError(error: unknown): string {
  if (!error) return "Request failed.";
  return (error as Error).message ?? String(error);
}

export function getCachedApi<T = unknown>(key: string | null | undefined): CacheRecord<T> {
  if (!key) return EMPTY_RECORD as CacheRecord<T>;
  return (cacheStore.get(key) ?? EMPTY_RECORD) as CacheRecord<T>;
}

export function subscribeApiCache(key: string | null | undefined, listener: CacheListener): () => void {
  if (!key) return () => {};
  let bucket = listeners.get(key);
  if (!bucket) {
    bucket = new Set();
    listeners.set(key, bucket);
  }
  bucket.add(listener);
  return () => {
    bucket!.delete(listener);
    if (!bucket!.size) listeners.delete(key);
  };
}

export function primeApiCache<T>(key: string | null | undefined, data: T): T {
  if (!key) return data;
  cacheStore.set(key, { data, error: null, updatedAt: Date.now() });
  emitCacheChange(key);
  return data;
}

export function setApiCacheError(key: string | null | undefined, error: unknown): unknown {
  if (!key) return error;
  const previous = getCachedApi(key);
  cacheStore.set(key, { data: previous.data, error: normalizeError(error), updatedAt: Date.now() });
  emitCacheChange(key);
  return error;
}

export function clearApiCache(matcher?: string | ((key: string) => boolean) | null): void {
  const listenerKeys = [...listeners.keys()];

  if (!matcher) {
    const cacheKeys = [...cacheStore.keys()];
    cacheStore.clear();
    inflightRequests.clear();
    new Set([...listenerKeys, ...cacheKeys]).forEach(emitCacheChange);
    return;
  }

  const shouldClear: (key: string) => boolean =
    typeof matcher === "function"
      ? matcher
      : (key) => key === matcher || key.startsWith(`${matcher}?`);

  const keys = [...cacheStore.keys()].filter(shouldClear);
  keys.forEach((key) => {
    cacheStore.delete(key);
    inflightRequests.delete(key);
  });
  new Set([...keys, ...listenerKeys.filter(shouldClear)]).forEach(emitCacheChange);
}

export function prefetchApi<T = unknown>(
  key: string,
  { force = false, signal, ttl = DEFAULT_TTL_MS }: { force?: boolean; signal?: AbortSignal; ttl?: number } = {},
): Promise<T> {
  if (!force && inflightRequests.has(key)) {
    return inflightRequests.get(key) as Promise<T>;
  }

  const cached = getCachedApi<T>(key);
  const isFresh = cached.data !== undefined && Date.now() - cached.updatedAt < ttl;
  if (!force && isFresh) return Promise.resolve(cached.data as T);

  const request: Promise<T> = apiFetch<T>(key, { signal })
    .then((data) => {
      primeApiCache(key, data);
      return data;
    })
    .catch((error: unknown) => {
      if ((error as Error)?.name !== "AbortError") setApiCacheError(key, error);
      throw error;
    })
    .finally(() => {
      if (inflightRequests.get(key) === request) inflightRequests.delete(key);
    });

  inflightRequests.set(key, request);
  return request;
}
