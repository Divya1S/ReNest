// ─── Push notifications ───────────────────────────────────────────────────────
self.addEventListener("push", (event) => {
  if (!event.data) return;

  let payload = { title: "ReNest", body: "", url: "/dashboard" };
  try { payload = { ...payload, ...JSON.parse(event.data.text()) }; } catch {}

  event.waitUntil(
    self.registration.showNotification(payload.title, {
      body: payload.body,
      icon: payload.icon || "/images/icon-192.png",
      badge: "/images/icon-192.png",
      data: { url: payload.url },
      vibrate: [100, 50, 100],
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = event.notification.data?.url || "/dashboard";
  event.waitUntil(
    clients
      .matchAll({ type: "window", includeUncontrolled: true })
      .then((wins) => {
        const match = wins.find((w) => w.url.includes(self.location.origin));
        if (match) return match.focus().then((w) => w.navigate(url));
        return clients.openWindow(url);
      }),
  );
});

// ─── Cache config ────────────────────────────────────────────────────────────
// v2: API responses are no longer written to Cache Storage — they can contain
// personal data (profile, messages, pickup addresses) that must not persist on
// shared devices. Only same-origin static assets and the offline page cache.
// v3: /media/ (room-scan photos, listing images, donation receipts) is excluded
// too; the old extension-based match had been caching those uploads. The
// version bump makes existing clients discard what v2 already stored.
const CACHE_VERSION = "v3";
const STATIC_CACHE = `renest-static-${CACHE_VERSION}`;

// Static assets to pre-cache on install
const PRECACHE_URLS = [
  "/",
  "/dashboard",
  "/browse",
  "/offline.html",
];

// ─── Install: pre-cache shell ────────────────────────────────────────────────
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(STATIC_CACHE)
      .then((cache) => cache.addAll(PRECACHE_URLS).catch(() => {}))
      .then(() => self.skipWaiting()),
  );
});

// ─── Activate: prune old caches (incl. v1 API caches holding personal data) ──
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((k) => k !== STATIC_CACHE)
            .map((k) => caches.delete(k)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

// ─── Fetch: route by request type ────────────────────────────────────────────
self.addEventListener("fetch", (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Never intercept: non-GET, cross-origin (Sentry, fonts, S3), or SSE streams
  if (request.method !== "GET") return;
  if (url.origin !== self.location.origin) return;

  // API calls → network only. Responses may contain personal data and are
  // never written to Cache Storage; the in-memory apiCache handles reuse.
  if (url.pathname.startsWith("/api/")) {
    event.respondWith(
      fetch(request).catch(
        () =>
          new Response(JSON.stringify({ detail: "You appear to be offline." }), {
            status: 503,
            headers: { "Content-Type": "application/json" },
          }),
      ),
    );
    return;
  }

  // User uploads (room scans, listing photos, donation receipts) are personal
  // data served from the same origin. They must never be written to Cache
  // Storage, where they would outlive the session on a shared device.
  if (url.pathname.startsWith("/media/")) return;

  // Static app assets → cache-first. Scoped to the build output and bundled
  // images; a bare extension match would have swept up /media/ uploads too.
  if (
    url.pathname.startsWith("/assets/") ||
    url.pathname.startsWith("/images/") ||
    url.pathname.match(/\.(css|js|svg|woff2?|ttf)$/)
  ) {
    event.respondWith(cacheFirst(request, STATIC_CACHE));
    return;
  }

  // HTML navigation → network-first, fall back to offline page
  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request).catch(() =>
        caches.match("/offline.html").then((r) => r || new Response("Offline", { status: 503 })),
      ),
    );
    return;
  }

  // Everything else → network-first
  event.respondWith(networkFirst(request, STATIC_CACHE));
});

// ─── Helpers ─────────────────────────────────────────────────────────────────
async function cacheFirst(request, cacheName) {
  const cached = await caches.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  if (response.ok) {
    const cache = await caches.open(cacheName);
    cache.put(request, response.clone());
  }
  return response;
}

async function networkFirst(request, cacheName) {
  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(cacheName);
      cache.put(request, response.clone());
    }
    return response;
  } catch {
    const cached = await caches.match(request);
    return cached || new Response(JSON.stringify({ detail: "Offline" }), {
      status: 503,
      headers: { "Content-Type": "application/json" },
    });
  }
}
