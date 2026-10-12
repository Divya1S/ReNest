import { apiFetch } from "./api";

/** True when this browser can hold a Web Push subscription at all. */
export function pushSupported(): boolean {
  return (
    typeof navigator !== "undefined" &&
    "serviceWorker" in navigator &&
    typeof window !== "undefined" &&
    "PushManager" in window
  );
}

/**
 * The browser's current push subscription, or null.
 *
 * Uses getRegistration() rather than `navigator.serviceWorker.ready`: `ready`
 * never settles when no worker is registered (development builds, a blocked
 * worker), which would hang whatever awaits it. Logout awaits this.
 */
export async function getPushSubscription(): Promise<PushSubscription | null> {
  if (!pushSupported()) return null;
  const registration = await navigator.serviceWorker.getRegistration();
  if (!registration?.pushManager) return null;
  return registration.pushManager.getSubscription();
}

/**
 * Drop the browser's push subscription without telling the server, for when
 * there is no session to tell it with: the session expired, or the app booted
 * signed out while the browser still held a subscription from whoever used it
 * last. A signed-out browser must not keep receiving someone's notifications,
 * and the next person to sign in must not inherit them. The server prunes the
 * dead endpoint when the push service answers 404/410 on the next delivery.
 */
export async function dropBrowserPushSubscription(): Promise<boolean> {
  try {
    const subscription = await getPushSubscription();
    return subscription ? await subscription.unsubscribe() : false;
  } catch {
    return false;
  }
}

/**
 * Best-effort removal of this browser's push subscription. Resolves to true
 * when a subscription existed and the browser dropped it.
 *
 * The subscription belongs to whoever enabled it, not to the device: left in
 * place after logout, the next person to sign in here keeps receiving the
 * previous user's notifications. Call this while the session is still valid,
 * because /push/unsubscribe requires authentication.
 *
 * The browser side is dropped even when the server call fails. An endpoint the
 * browser no longer honours answers 404/410 on the next delivery and the
 * server prunes it then, whereas a live one would keep delivering.
 */
export async function removePushSubscription(): Promise<boolean> {
  let subscription: PushSubscription | null = null;
  try {
    subscription = await getPushSubscription();
  } catch {
    return false;
  }
  if (!subscription) return false;

  try {
    await apiFetch("/push/unsubscribe", {
      method: "POST",
      body: { endpoint: subscription.endpoint },
      timeout: 5000,
    });
  } catch {
    // Offline or already signed out: fall through to the browser-side removal.
  }

  try {
    return await subscription.unsubscribe();
  } catch {
    return false;
  }
}
