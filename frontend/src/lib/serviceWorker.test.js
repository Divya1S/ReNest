import fs from "node:fs";
import path from "node:path";

import { describe, expect, test, vi } from "vitest";

/**
 * Loads public/sw.js into a fake worker global and drives its event handlers,
 * so the caching rules are pinned without a real browser.
 */
function loadWorker({ fetchImpl } = {}) {
  const source = fs.readFileSync(path.resolve(globalThis.process.cwd(), "public/sw.js"), "utf8");
  const listeners = {};
  const cache = { addAll: vi.fn(() => Promise.resolve()), put: vi.fn(() => Promise.resolve()) };
  const caches = {
    open: vi.fn(() => Promise.resolve(cache)),
    keys: vi.fn(() => Promise.resolve(["renest-static-v3", "renest-static-v4", "renest-api-v1"])),
    delete: vi.fn(() => Promise.resolve(true)),
    match: vi.fn(() => Promise.resolve(undefined)),
  };
  const self = {
    location: { origin: "https://renest.test" },
    addEventListener: (type, handler) => {
      listeners[type] = handler;
    },
    skipWaiting: vi.fn(() => Promise.resolve()),
    clients: { claim: vi.fn(() => Promise.resolve()) },
    registration: { showNotification: vi.fn() },
  };
  const fetchMock = fetchImpl ?? vi.fn(() => Promise.resolve(new Response("ok")));
  new Function("self", "caches", "clients", "fetch", source)(self, caches, self.clients, fetchMock);
  return { listeners, cache, caches, self, fetchMock };
}

function fetchEvent(url, { method = "GET", mode = "cors", accept = "*/*" } = {}) {
  return {
    request: { url, method, mode, headers: new Headers({ accept }) },
    respondWith: vi.fn(),
  };
}

describe("service worker", () => {
  test("precaches only the offline page", async () => {
    const { listeners, cache, caches } = loadWorker();
    let done;
    listeners.install({ waitUntil: (promise) => { done = promise; } });
    await done;

    expect(caches.open).toHaveBeenCalledWith("renest-static-v4");
    // "/", "/dashboard" and "/browse" were stored but never served.
    expect(cache.addAll).toHaveBeenCalledWith(["/offline.html"]);
  });

  test("activation drops every cache from an earlier version", async () => {
    const { listeners, caches } = loadWorker();
    let done;
    listeners.activate({ waitUntil: (promise) => { done = promise; } });
    await done;

    expect(caches.delete).toHaveBeenCalledWith("renest-static-v3");
    expect(caches.delete).toHaveBeenCalledWith("renest-api-v1");
    expect(caches.delete).not.toHaveBeenCalledWith("renest-static-v4");
  });

  test("leaves the server-sent event stream to the browser", () => {
    const { listeners, fetchMock } = loadWorker();

    const byPath = fetchEvent("https://renest.test/api/events", { accept: "text/event-stream" });
    listeners.fetch(byPath);
    expect(byPath.respondWith).not.toHaveBeenCalled();

    const byHeader = fetchEvent("https://renest.test/api/concierge/stream", { accept: "text/event-stream" });
    listeners.fetch(byHeader);
    expect(byHeader.respondWith).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  test("other API reads stay network-only with a JSON 503 when offline", async () => {
    const { listeners, cache } = loadWorker({ fetchImpl: vi.fn(() => Promise.reject(new TypeError("offline"))) });
    const event = fetchEvent("https://renest.test/api/listings?page=1");

    listeners.fetch(event);

    expect(event.respondWith).toHaveBeenCalledTimes(1);
    const response = await event.respondWith.mock.calls[0][0];
    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({ detail: "You appear to be offline." });
    expect(cache.put).not.toHaveBeenCalled();
  });

  test("an offline navigation falls back to the offline page", async () => {
    const offlinePage = new Response("<h1>You're offline</h1>");
    const { listeners, caches } = loadWorker({ fetchImpl: vi.fn(() => Promise.reject(new TypeError("offline"))) });
    caches.match.mockImplementation((key) => Promise.resolve(key === "/offline.html" ? offlinePage : undefined));
    const event = fetchEvent("https://renest.test/dashboard", { mode: "navigate", accept: "text/html" });

    listeners.fetch(event);

    expect(await event.respondWith.mock.calls[0][0]).toBe(offlinePage);
  });

  test("uploads under /media/ and non-GET requests are never intercepted", () => {
    const { listeners } = loadWorker();

    const media = fetchEvent("https://renest.test/media/scans/room.jpg");
    listeners.fetch(media);
    expect(media.respondWith).not.toHaveBeenCalled();

    const post = fetchEvent("https://renest.test/api/listings", { method: "POST" });
    listeners.fetch(post);
    expect(post.respondWith).not.toHaveBeenCalled();
  });
});
