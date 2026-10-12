import { afterEach, describe, expect, test, vi } from "vitest";

import { registerServiceWorker } from "./registerServiceWorker";

function stubServiceWorker(overrides = {}) {
  const container = {
    register: vi.fn(() => Promise.resolve({})),
    getRegistrations: vi.fn(() => Promise.resolve([])),
    ...overrides,
  };
  Object.defineProperty(navigator, "serviceWorker", { configurable: true, value: container });
  return container;
}

describe("registerServiceWorker", () => {
  afterEach(() => {
    delete navigator.serviceWorker;
    vi.restoreAllMocks();
  });

  test("registers /sw.js after load in a production build", () => {
    const container = stubServiceWorker();
    const addListener = vi.spyOn(window, "addEventListener").mockImplementation(() => {});

    registerServiceWorker({ isProduction: true });

    expect(container.register).not.toHaveBeenCalled();
    const [type, onLoad] = addListener.mock.calls[0];
    expect(type).toBe("load");
    onLoad();
    expect(container.register).toHaveBeenCalledWith("/sw.js", { scope: "/" });
  });

  test("registers nothing in development and removes a worker left by an earlier session", async () => {
    const stale = { unregister: vi.fn(() => Promise.resolve(true)) };
    const container = stubServiceWorker({ getRegistrations: vi.fn(() => Promise.resolve([stale])) });
    const addListener = vi.spyOn(window, "addEventListener").mockImplementation(() => {});

    registerServiceWorker({ isProduction: false });
    await Promise.resolve();
    await Promise.resolve();

    expect(addListener).not.toHaveBeenCalled();
    expect(container.register).not.toHaveBeenCalled();
    expect(stale.unregister).toHaveBeenCalledTimes(1);
  });

  test("is a no-op where service workers are unsupported", () => {
    expect("serviceWorker" in navigator).toBe(false);
    expect(() => registerServiceWorker({ isProduction: true })).not.toThrow();
  });
});
