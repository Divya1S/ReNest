import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { clearApiCache } from "../lib/apiCache";

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
});
