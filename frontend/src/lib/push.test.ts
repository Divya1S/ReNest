import { afterEach, describe, expect, test, vi } from "vitest";

import { stubPushEnvironment } from "../test/pushStubs";

import { getPushSubscription, pushSupported, removePushSubscription } from "./push";

type PushEnv = ReturnType<typeof stubPushEnvironment>;

function ok(body: unknown = {}) {
  return { ok: true, status: 200, json: async () => body } as Response;
}

describe("removePushSubscription", () => {
  let env: PushEnv | null = null;

  afterEach(() => {
    env?.restore();
    env = null;
    vi.restoreAllMocks();
  });

  test("tells the server which endpoint to drop, then unsubscribes the browser", async () => {
    env = stubPushEnvironment({ endpoint: "https://push.example/abc" });
    const spy = vi.spyOn(globalThis, "fetch").mockResolvedValue(ok({ unsubscribed: true }));

    const removed = await removePushSubscription();

    expect(removed).toBe(true);
    expect(spy).toHaveBeenCalledTimes(1);
    expect(String(spy.mock.calls[0][0])).toBe("/api/push/unsubscribe");
    const init = spy.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ endpoint: "https://push.example/abc" });
    expect(env.subscription!.unsubscribe).toHaveBeenCalledTimes(1);
    expect(await getPushSubscription()).toBeNull();
  });

  test("still unsubscribes the browser when the server call fails", async () => {
    env = stubPushEnvironment();
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("Failed to fetch"));

    const removed = await removePushSubscription();

    expect(removed).toBe(true);
    expect(env.subscription!.unsubscribe).toHaveBeenCalledTimes(1);
  });

  test("does nothing when this browser holds no subscription", async () => {
    env = stubPushEnvironment({ subscribed: false });
    const spy = vi.spyOn(globalThis, "fetch").mockResolvedValue(ok());

    expect(await removePushSubscription()).toBe(false);
    expect(spy).not.toHaveBeenCalled();
  });

  test("does nothing, and does not hang, where push is unsupported or no worker is registered", async () => {
    expect(pushSupported()).toBe(false);
    expect(await removePushSubscription()).toBe(false);

    env = stubPushEnvironment();
    env.serviceWorker.getRegistration.mockResolvedValue(undefined as never);
    expect(await removePushSubscription()).toBe(false);
  });
});
