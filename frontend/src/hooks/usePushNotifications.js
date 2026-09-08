import { useCallback, useEffect, useState } from "react";

import { apiFetch } from "../lib/api";

/**
 * Manages the Web Push subscription lifecycle for the current user.
 *
 * Returns:
 *   - supported: browser supports Web Push
 *   - available: the server also has VAPID keys configured, so a subscription
 *     can actually succeed. Callers should not offer an opt-in without it.
 *   - permission: "default" | "granted" | "denied"
 *   - subscribed: subscription is active and registered with the backend
 *   - loading: async operation in progress
 *   - subscribe(): request permission + subscribe + POST to backend, resolving
 *     to { ok, reason } so the caller can surface why it failed
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
  const [publicKey, setPublicKey] = useState("");

  // A service worker that never activates would leave `ready` pending forever
  // and the opt-in button stuck on its loading label.
  const serviceWorkerReady = useCallback(
    () =>
      Promise.race([
        navigator.serviceWorker.ready,
        new Promise((_, reject) =>
          setTimeout(() => reject(new Error("Service worker did not become ready.")), 5000),
        ),
      ]),
    [],
  );

  // Check if there's already an active subscription on mount
  useEffect(() => {
    if (!supported || !enabled) return undefined;
    let active = true;

    serviceWorkerReady()
      .then((reg) => reg.pushManager.getSubscription())
      .then((sub) => {
        if (active) setSubscribed(!!sub);
      })
      .catch(() => {
        if (active) setSubscribed(false);
      });

    return () => {
      active = false;
    };
  }, [supported, enabled, serviceWorkerReady]);

  // Ask the server once whether push is configured at all. Without VAPID keys
  // the opt-in can only ever fail, so the UI should not offer it.
  useEffect(() => {
    if (!supported || !enabled) return undefined;
    let active = true;

    apiFetch("/push/vapid-key")
      .then((data) => {
        if (active && data?.enabled && data?.public_key) setPublicKey(data.public_key);
      })
      .catch(() => {
        if (active) setPublicKey("");
      });

    return () => {
      active = false;
    };
  }, [supported, enabled]);

  const subscribe = useCallback(async () => {
    if (!supported || loading) return { ok: false, reason: "unavailable" };
    if (!publicKey) return { ok: false, reason: "unavailable" };
    setLoading(true);
    try {
      // 1. Request permission
      const result = await Notification.requestPermission();
      setPermission(result);
      if (result !== "granted") return { ok: false, reason: "denied" };

      // 2. Subscribe via PushManager
      const reg = await serviceWorkerReady();
      const sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: _urlBase64ToUint8Array(publicKey),
      });

      // 3. POST subscription to backend
      const subJson = sub.toJSON();
      await apiFetch("/push/subscribe", {
        method: "POST",
        body: {
          endpoint: subJson.endpoint,
          keys: subJson.keys,
        },
      });

      setSubscribed(true);
      return { ok: true };
    } catch (error) {
      return { ok: false, reason: "failed", message: error?.message };
    } finally {
      setLoading(false);
    }
  }, [supported, loading, publicKey, serviceWorkerReady]);

  const unsubscribe = useCallback(async () => {
    if (!supported || loading) return;
    setLoading(true);
    try {
      const reg = await serviceWorkerReady();
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
  }, [supported, loading, serviceWorkerReady]);

  return {
    supported,
    available: supported && !!publicKey,
    permission,
    subscribed,
    loading,
    subscribe,
    unsubscribe,
  };
}

// Convert VAPID public key from base64url to Uint8Array
function _urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = window.atob(base64);
  return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
}
