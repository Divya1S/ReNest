import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { clearApiCache, primeApiCache } from "../lib/apiCache";

import { useApi } from "./useApi";

function mockJson(body, { delayMs = 0 } = {}) {
  return vi.spyOn(global, "fetch").mockImplementation(
    () =>
      new Promise((resolve) =>
        setTimeout(
          () => resolve({ ok: true, status: 200, json: async () => body }),
          delayMs,
        ),
      ),
  );
}

describe("useApi", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    clearApiCache();
  });

  test("reports loading on the first fetch even when initialData is supplied", async () => {
    // Regression: initialData was treated as fetched data, so `loading` stayed
    // false and pages rendered their empty state instead of a skeleton.
    mockJson({ results: [{ id: 1 }], unread_count: 1 }, { delayMs: 10 });

    const { result } = renderHook(() =>
      useApi("/notifications", { initialData: { results: [], unread_count: 0 } }),
    );

    expect(result.current.loading).toBe(true);
    expect(result.current.data).toEqual({ results: [], unread_count: 0 });

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data.results).toHaveLength(1);
  });

  test("skip never enters the loading state", async () => {
    const spy = mockJson({ results: [] });
    const { result } = renderHook(() => useApi("/notifications", { skip: true }));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(spy).not.toHaveBeenCalled();
  });

  test("refetch ignores a non-string first argument such as a click event", async () => {
    // Regression: onClick={refetch} passed the event as overridePath and the
    // button requested "/api[object Object]".
    const spy = mockJson({ results: [] });
    const { result } = renderHook(() => useApi("/notifications"));
    await waitFor(() => expect(result.current.loading).toBe(false));

    const clickEvent = { type: "click", target: {}, preventDefault() {} };
    await act(async () => {
      await result.current.refetch(clickEvent);
    });

    expect(spy).toHaveBeenCalledTimes(2);
    for (const [url] of spy.mock.calls) {
      expect(url).toBe("/api/notifications");
    }
  });

  test("refetch still honours a real override path", async () => {
    const spy = mockJson({ results: [] });
    const { result } = renderHook(() => useApi("/notifications"));
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.refetch("/notifications?page=2");
    });

    expect(spy.mock.calls.at(-1)[0]).toBe("/api/notifications?page=2");
  });

  test("retry never rejects and a failed load exposes the HTTP status", async () => {
    const unhandled = vi.fn();
    globalThis.process.on("unhandledRejection", unhandled);
    const spy = vi
      .spyOn(global, "fetch")
      .mockResolvedValue({ ok: false, status: 503, json: async () => ({ detail: "Down for maintenance." }) });

    const { result } = renderHook(() => useApi("/saved-listings"));
    await waitFor(() => expect(result.current.error).toBe("Down for maintenance."));
    expect(result.current.errorStatus).toBe(503);

    let returned;
    await act(async () => {
      returned = result.current.retry({ type: "click" });
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(returned).toBeUndefined();
    expect(spy).toHaveBeenCalledTimes(2);
    expect(spy.mock.calls[1][0]).toBe("/api/saved-listings");
    expect(result.current.error).toBe("Down for maintenance.");
    expect(unhandled).not.toHaveBeenCalled();
    globalThis.process.off("unhandledRejection", unhandled);
  });

  test("errorStatus distinguishes 404 from a network failure and clears on success", async () => {
    const spy = vi
      .spyOn(global, "fetch")
      .mockResolvedValueOnce({ ok: false, status: 404, json: async () => ({ detail: "Not found." }) })
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ id: 7 }) });

    const { result } = renderHook(() => useApi("/listings/7"));
    await waitFor(() => expect(result.current.errorStatus).toBe(404));

    await act(async () => {
      result.current.retry();
    });
    await waitFor(() => expect(result.current.error).toBe("Failed to fetch"));
    expect(result.current.errorStatus).toBeNull();

    await act(async () => {
      result.current.retry();
    });
    await waitFor(() => expect(result.current.data).toEqual({ id: 7 }));
    expect(result.current.error).toBeNull();
    expect(result.current.errorStatus).toBeNull();
    expect(spy).toHaveBeenCalledTimes(3);
  });

  test("a request superseded by a newer one does not end the newer one's loading state", async () => {
    const resolvers = [];
    vi.spyOn(global, "fetch").mockImplementation(
      (_url, init) =>
        new Promise((resolve, reject) => {
          resolvers.push(resolve);
          init?.signal?.addEventListener("abort", () =>
            reject(Object.assign(new Error("aborted"), { name: "AbortError" })),
          );
        }),
    );

    const { result } = renderHook(() => useApi("/trust/me"));
    expect(result.current.loading).toBe(true);

    // A second fetch aborts the first while it is still in flight.
    await act(async () => {
      result.current.retry();
      await Promise.resolve();
    });
    expect(result.current.loading).toBe(true);

    await act(async () => {
      resolvers.at(-1)({ ok: true, status: 200, json: async () => ({ score: 5 }) });
    });
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toEqual({ score: 5 });
  });

  test("switching to an already cached path while a request is in flight shows the cached data at once", async () => {
    const pending = [];
    vi.spyOn(global, "fetch").mockImplementation(
      (url, init) =>
        new Promise((resolve, reject) => {
          pending.push({ url, resolve });
          init?.signal?.addEventListener("abort", () =>
            reject(Object.assign(new Error("aborted"), { name: "AbortError" })),
          );
        }),
    );
    primeApiCache("/leaderboard?period=week", { leaderboard: ["cached"] });

    const { result, rerender } = renderHook(({ path }) => useApi(path), {
      initialProps: { path: "/leaderboard?period=all" },
    });
    expect(result.current.loading).toBe(true);

    rerender({ path: "/leaderboard?period=week" });

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toEqual({ leaderboard: ["cached"] });
  });

  test("refetches when the cache entry is cleared underneath a mounted consumer", async () => {
    // Logout clears the cache; a public page that stays mounted used to be
    // left with no data and nothing to fetch it again.
    const spy = mockJson({ id: 7, title: "Mini Fridge" });
    const { result } = renderHook(() => useApi("/listings/7"));
    await waitFor(() => expect(result.current.data).toEqual({ id: 7, title: "Mini Fridge" }));
    expect(spy).toHaveBeenCalledTimes(1);

    act(() => {
      clearApiCache();
    });
    expect(result.current.data).toBeNull();

    await waitFor(() => expect(result.current.data).toEqual({ id: 7, title: "Mini Fridge" }));
    expect(spy).toHaveBeenCalledTimes(2);
  });

  test("a consumer that unmounts with the cache clear does not refetch", async () => {
    const spy = mockJson({ id: 1 });
    const { result, unmount } = renderHook(() => useApi("/dashboard"));
    await waitFor(() => expect(result.current.data).toEqual({ id: 1 }));

    act(() => {
      clearApiCache();
    });
    unmount();
    await new Promise((resolve) => setTimeout(resolve, 10));

    expect(spy).toHaveBeenCalledTimes(1);
  });
});
