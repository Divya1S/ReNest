/**
 * Register the service worker, in production builds only.
 *
 * In development the worker fights the dev server: it serves cache-first
 * copies of /assets and *.js, so edits appear not to apply, and it survives
 * across sessions on localhost. A development session therefore registers
 * nothing and removes a worker left behind by an earlier one.
 */
export function registerServiceWorker({ isProduction }: { isProduction: boolean }): void {
  if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) return;

  if (!isProduction) {
    navigator.serviceWorker
      .getRegistrations?.()
      .then((registrations) => Promise.all(registrations.map((registration) => registration.unregister())))
      .catch(() => undefined);
    return;
  }

  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => undefined);
  });
}
