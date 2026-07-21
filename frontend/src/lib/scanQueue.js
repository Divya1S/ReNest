/**
 * Offline-first queue for scan session mutations.
 * Writes are stored in localStorage (or @capacitor/preferences when native)
 * and replayed in FIFO order when the browser comes back online.
 *
 * Usage:
 *   ScanQueue.enqueue({ method: "POST", url: "/scan-sessions", body: {...} })
 *   ScanQueue.replay(apiFetch)   // call on "online" event
 *   ScanQueue.size()             // int — show in UI
 */

const STORAGE_KEY = "renest_scan_queue";

async function readQueue() {
  try {
    if (window.Capacitor?.isNativePlatform?.()) {
      const { Preferences } = await import("@capacitor/preferences");
      const { value } = await Preferences.get({ key: STORAGE_KEY });
      return value ? JSON.parse(value) : [];
    }
  } catch {
    // Preferences plugin unavailable — fall through to localStorage
  }
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "[]");
  } catch {
    return [];
  }
}

async function writeQueue(items) {
  const serialised = JSON.stringify(items);
  try {
    if (window.Capacitor?.isNativePlatform?.()) {
      const { Preferences } = await import("@capacitor/preferences");
      await Preferences.set({ key: STORAGE_KEY, value: serialised });
      return;
    }
  } catch {
    // Preferences plugin unavailable — fall through to localStorage
  }
  localStorage.setItem(STORAGE_KEY, serialised);
}

export const ScanQueue = {
  async enqueue(operation) {
    const queue = await readQueue();
    queue.push({ ...operation, id: `${Date.now()}_${Math.random()}` });
    await writeQueue(queue);
  },

  async size() {
    return (await readQueue()).length;
  },

  async replay(apiFetch) {
    const queue = await readQueue();
    if (!queue.length) return 0;

    const remaining = [];
    let replayed = 0;
    for (const op of queue) {
      try {
        await apiFetch(op.url, {
          method: op.method ?? "POST",
          body: op.body ?? {},
        });
        replayed++;
      } catch {
        remaining.push(op);
      }
    }
    await writeQueue(remaining);
    return replayed;
  },

  async clear() {
    await writeQueue([]);
  },
};

/**
 * Hook the browser "online" event once to auto-replay.
 * Call this once from App.jsx or a top-level effect.
 */
export function initOfflineReplay(apiFetch, onReplay) {
  function handleOnline() {
    ScanQueue.replay(apiFetch).then((count) => {
      if (count > 0 && onReplay) onReplay(count);
    });
  }
  window.addEventListener("online", handleOnline);
  return () => window.removeEventListener("online", handleOnline);
}
