import { useCallback, useEffect, useRef, useState } from "react";

import { useApi } from "./useApi";

const POLL_INTERVAL_MS = 60_000;
const SSE_URL = "/api/events";

/**
 * Returns the current unread notification count.
 *
 * Strategy:
 *   1. Open an EventSource to GET /api/events (SSE).
 *      The server pushes `notification.new` events with an `unread_count` field.
 *   2. If the browser doesn't support EventSource, or the SSE connection fails
 *      permanently (onerror after the stream never opened), fall back to the
 *      60 s polling strategy used previously.
 *   3. Regardless of strategy, re-fetch immediately when the tab becomes visible.
 */
export function useNotificationBadge(enabled = true) {
  // Initial load via the REST endpoint so the badge is populated before SSE connects
  const { data, refetch } = useApi(
    enabled ? "/notifications?limit=1&unread=1" : null,
    { initialData: { results: [], unread_count: 0 }, skip: !enabled },
  );

  const [sseCount, setSseCount] = useState(null);
  const sseRef = useRef(null);
  const fallbackTimerRef = useRef(null);
  const refetchRef = useRef(refetch);
  refetchRef.current = refetch;

  const startPolling = useCallback(() => {
    if (fallbackTimerRef.current) return;
    fallbackTimerRef.current = setInterval(() => {
      if (!document.hidden) {
        refetchRef.current(undefined, { force: true }).catch(() => {});
      }
    }, POLL_INTERVAL_MS);
  }, []);

  const stopPolling = useCallback(() => {
    if (fallbackTimerRef.current) {
      clearInterval(fallbackTimerRef.current);
      fallbackTimerRef.current = null;
    }
  }, []);

  const closeSse = useCallback(() => {
    if (sseRef.current) {
      sseRef.current.close();
      sseRef.current = null;
    }
  }, []);

  const openSse = useCallback(() => {
    if (!enabled || !("EventSource" in window)) {
      startPolling();
      return;
    }

    closeSse();
    const es = new EventSource(SSE_URL, { withCredentials: true });
    sseRef.current = es;

    es.addEventListener("notification.new", (e) => {
      try {
        const payload = JSON.parse(e.data);
        if (typeof payload.unread_count === "number") {
          setSseCount(payload.unread_count);
        }
      } catch {
        // ignore malformed events
      }
    });

    es.addEventListener("stream.end", () => {
      // Server closed cleanly after max_duration — reconnect
      closeSse();
      openSse();
    });

    let opened = false;
    es.onopen = () => { opened = true; stopPolling(); };

    es.onerror = () => {
      closeSse();
      if (!opened) {
        // Connection never established — fall back to polling
        startPolling();
      } else {
        // Transient error after a good connection; retry after a short delay
        setTimeout(openSse, 5_000);
      }
    };
  }, [enabled, closeSse, startPolling, stopPolling]);  

  useEffect(() => {
    if (!enabled) return;

    openSse();

    const handleVisibility = () => {
      if (!document.hidden) {
        refetchRef.current(undefined, { force: true }).catch(() => {});
        // If SSE dropped while hidden, reconnect
        if (!sseRef.current && !fallbackTimerRef.current) {
          openSse();
        }
      }
    };
    document.addEventListener("visibilitychange", handleVisibility);

    return () => {
      closeSse();
      stopPolling();
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [enabled, openSse, closeSse, stopPolling]);

  // SSE count takes precedence over the REST snapshot when available
  const count = sseCount ?? data?.unread_count ?? 0;
  return count;
}
