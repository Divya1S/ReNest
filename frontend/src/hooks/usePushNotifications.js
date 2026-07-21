import { useCallback, useEffect, useState } from "react";

import { apiFetch } from "../lib/api";

/**
 * Manages the Web Push subscription lifecycle for the current user.
 *
 * Returns:
 *   - supported: browser supports Web Push
 *   - permission: "default" | "granted" | "denied"
 *   - subscribed: subscription is active and registered with the backend
 *   - loading: async operation in progress
 *   - subscribe(): request permission + subscribe + POST to backend
 *   - unsubscribe(): remove subscription from backend + browser
 */
export function usePushNotifications(enabled = true) {
  const supported =
    "serviceWorker" in navigator &&
    "PushManager" in window &&
    "Notification" in window;

  const [permission, setPermission] = useState(
    supported ? Notification.permission : "denied",
  );
  const [subscribed, setSubscribed] = useState(false);
  const [loading, setLoading] = useState(false);

  // Check if there's already an active subscription on mount
  useEffect(() => {
    if (!supported || !enabled) return;

    navigator.serviceWorker.ready.then((reg) =>
      reg.pushManager.getSubscription().then((sub) => {
        setSubscribed(!!sub);
      }),
    );
  }, [supported, enabled]);

  const subscribe = useCallback(async () => {
    if (!supported || loading) return false;
    setLoading(true);
    try {
      // 1. Fetch VAPID public key
      const { public_key, enabled: vapidEnabled } = await apiFetch("/push/vapid-key");
      if (!vapidEnabled || !public_key) return false;

      // 2. Request permission
      const result = await Notification.requestPermission();
      setPermission(result);
      if (result !== "granted") return false;

      // 3. Subscribe via PushManager
      const reg = await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: _urlBase64ToUint8Array(public_key),
      });

      // 4. POST subscription to backend
      const subJson = sub.toJSON();
      await apiFetch("/push/subscribe", {
        method: "POST",
        body: {
          endpoint: subJson.endpoint,
          keys: subJson.keys,
        },
      });

      setSubscribed(true);
      return true;
    } catch {
      return false;
    } finally {
      setLoading(false);
    }
  }, [supported, loading]);

  const unsubscribe = useCallback(async () => {
    if (!supported || loading) return;
    setLoading(true);
    try {
      const reg = await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.getSubscription();
      if (sub) {
        await apiFetch("/push/unsubscribe", {
          method: "POST",
          body: { endpoint: sub.endpoint },
        });
        await sub.unsubscribe();
      }
      setSubscribed(false);
    } catch {
      // best-effort
    } finally {
      setLoading(false);
    }
  }, [supported, loading]);

  return { supported, permission, subscribed, loading, subscribe, unsubscribe };
}

// Convert VAPID public key from base64url to Uint8Array
function _urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = window.atob(base64);
  return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
}
