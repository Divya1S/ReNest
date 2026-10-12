import { vi } from "vitest";

/**
 * Give jsdom just enough of the Web Push surface for the app's push code:
 * a service worker registration whose pushManager holds (or does not hold) a
 * subscription. Returns the fakes plus `restore()` to undo everything.
 */
export function stubPushEnvironment({ subscribed = true, permission = "granted", endpoint = "https://push.example/sub-1" } = {}) {
  let current = null;

  function makeSubscription() {
    const subscription = {
      endpoint,
      toJSON: () => ({ endpoint, keys: { p256dh: "p256dh-key", auth: "auth-key" } }),
      unsubscribe: vi.fn(async () => {
        current = null;
        return true;
      }),
    };
    return subscription;
  }

  if (subscribed) current = makeSubscription();
  const initialSubscription = current;

  const pushManager = {
    getSubscription: vi.fn(async () => current),
    subscribe: vi.fn(async () => {
      current = makeSubscription();
      return current;
    }),
  };
  const registration = { pushManager };
  const serviceWorker = {
    getRegistration: vi.fn(async () => registration),
    ready: Promise.resolve(registration),
  };

  Object.defineProperty(navigator, "serviceWorker", { configurable: true, value: serviceWorker });
  window.PushManager = function PushManager() {};
  window.Notification = { permission, requestPermission: vi.fn(async () => "granted") };

  return {
    subscription: initialSubscription,
    pushManager,
    serviceWorker,
    get current() {
      return current;
    },
    restore() {
      delete navigator.serviceWorker;
      delete window.PushManager;
      delete window.Notification;
    },
  };
}
