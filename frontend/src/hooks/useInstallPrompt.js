import { useEffect, useState } from "react";

/**
 * Captures the browser's beforeinstallprompt event so we can show a custom
 * install banner instead of the default browser prompt.
 *
 * Returns { canInstall, install, dismiss }:
 * - canInstall: true when the deferred prompt is available
 * - install(): triggers the native install flow; resolves to "accepted"|"dismissed"
 * - dismiss(): hides the banner and persists the dismissal to localStorage
 */
export function useInstallPrompt() {
  const [deferredPrompt, setDeferredPrompt] = useState(null);
  const [dismissed, setDismissed] = useState(
    () => localStorage.getItem("pwa-install-dismissed") === "1",
  );

  useEffect(() => {
    if (dismissed) return;

    const handler = (e) => {
      e.preventDefault();
      setDeferredPrompt(e);
    };

    window.addEventListener("beforeinstallprompt", handler);
    return () => window.removeEventListener("beforeinstallprompt", handler);
  }, [dismissed]);

  // If already installed, clear the prompt
  useEffect(() => {
    const handler = () => setDeferredPrompt(null);
    window.addEventListener("appinstalled", handler);
    return () => window.removeEventListener("appinstalled", handler);
  }, []);

  async function install() {
    if (!deferredPrompt) return "not-available";
    deferredPrompt.prompt();
    const { outcome } = await deferredPrompt.userChoice;
    setDeferredPrompt(null);
    return outcome;
  }

  function dismiss() {
    localStorage.setItem("pwa-install-dismissed", "1");
    setDismissed(true);
    setDeferredPrompt(null);
  }

  return {
    canInstall: !!deferredPrompt && !dismissed,
    install,
    dismiss,
  };
}
