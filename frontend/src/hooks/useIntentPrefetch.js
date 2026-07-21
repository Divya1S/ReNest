import { useCallback, useEffect, useRef } from "react";

import { prefetchApi } from "../lib/apiCache";
import { prefetchRoute } from "../lib/routeLoaders";

function shouldPrefetch() {
  if (typeof window === "undefined") {
    return false;
  }

  const connection =
    navigator.connection || navigator.mozConnection || navigator.webkitConnection;

  if (connection?.saveData) {
    return false;
  }

  if (typeof connection?.effectiveType === "string" && connection.effectiveType.includes("2g")) {
    return false;
  }

  return true;
}

function scheduleIdleWork(callback) {
  if (typeof window === "undefined") {
    return () => {};
  }

  if ("requestIdleCallback" in window) {
    const id = window.requestIdleCallback(callback, { timeout: 400 });
    return () => window.cancelIdleCallback?.(id);
  }

  const id = window.setTimeout(callback, 80);
  return () => window.clearTimeout(id);
}

export function useIntentPrefetch({ route, data, delay = 90, enabled = true } = {}) {
  const timeoutRef = useRef(null);
  const cancelIdleRef = useRef(null);
  const prefetchedRef = useRef(false);

  const resolveDataPaths = useCallback(() => {
    if (!data) {
      return [];
    }
    if (typeof data === "function") {
      return data() || [];
    }
    return Array.isArray(data) ? data : [data];
  }, [data]);

  const prefetchNow = useCallback(() => {
    if (!enabled || prefetchedRef.current || !shouldPrefetch()) {
      return;
    }

    prefetchedRef.current = true;

    if (route) {
      prefetchRoute(route).catch(() => {});
    }

    resolveDataPaths().forEach((path) => {
      prefetchApi(path).catch(() => {});
    });
  }, [enabled, resolveDataPaths, route]);

  const cancelScheduledPrefetch = useCallback(() => {
    if (typeof window !== "undefined" && timeoutRef.current) {
      window.clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }
    if (cancelIdleRef.current) {
      cancelIdleRef.current();
      cancelIdleRef.current = null;
    }
  }, []);

  const schedulePrefetch = useCallback(() => {
    if (!enabled || prefetchedRef.current || !shouldPrefetch() || typeof window === "undefined") {
      return;
    }

    cancelScheduledPrefetch();
    timeoutRef.current = window.setTimeout(() => {
      cancelIdleRef.current = scheduleIdleWork(() => {
        prefetchNow();
      });
    }, delay);
  }, [cancelScheduledPrefetch, delay, enabled, prefetchNow]);

  useEffect(() => {
    prefetchedRef.current = false;
    cancelScheduledPrefetch();
  }, [cancelScheduledPrefetch, data, route]);

  useEffect(() => cancelScheduledPrefetch, [cancelScheduledPrefetch]);

  return {
    onMouseEnter: schedulePrefetch,
    onFocus: schedulePrefetch,
    onTouchStart: prefetchNow,
    onMouseLeave: cancelScheduledPrefetch,
    onBlur: cancelScheduledPrefetch,
  };
}
