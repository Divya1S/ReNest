import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { clearApiCache } from "../lib/apiCache";

import MyListingsPage from "./MyListingsPage";
import NotificationsPage from "./NotificationsPage";
import SavedPage from "./SavedPage";

function failing() {
  return { ok: false, status: 500, json: async () => ({ detail: "Server exploded." }) };
}

function ok(body) {
  return { ok: true, status: 200, json: async () => body };
}

/**
 * Regression: these three pages wired their error-state button as
 * onClick={refetch}. The click event became the hook's overridePath, so the
 * button requested "/api[object Object]" and could never recover the page.
 */
describe.each([
  {
    name: "NotificationsPage",
    Page: NotificationsPage,
    url: "/api/notifications",
    success: { unread_count: 0, results: [] },
    loaded: /you're all caught up|no unread alerts|unread now/i,
  },
  {
    name: "SavedPage",
    Page: SavedPage,
    url: "/api/saved-listings",
    success: { count: 0, results: [] },
    loaded: /nothing saved yet/i,
  },
  {
    name: "MyListingsPage",
    Page: MyListingsPage,
    url: "/api/listings?mine=1&page_size=100",
    success: { count: 0, next: null, previous: null, results: [] },
    loaded: /my listings/i,
  },
])("$name retry button", ({ Page, url, success, loaded }) => {
  beforeEach(() => clearApiCache());
  afterEach(() => vi.restoreAllMocks());

  test("re-requests the page's own endpoint and recovers", async () => {
    const spy = vi
      .spyOn(global, "fetch")
      .mockResolvedValueOnce(failing())
      .mockResolvedValue(ok(success));

    render(
      <MemoryRouter>
        <Page />
      </MemoryRouter>,
    );

    await userEvent.click(await screen.findByRole("button", { name: /try again/i }));

    await waitFor(() => expect(spy.mock.calls.length).toBeGreaterThanOrEqual(2));
    expect(spy.mock.calls[1][0]).toBe(url);
    expect(spy.mock.calls.map(([requested]) => requested).join(" ")).not.toContain("[object");
    await waitFor(() => expect(screen.getAllByText(loaded).length).toBeGreaterThan(0));
    expect(screen.queryByRole("button", { name: /try again/i })).not.toBeInTheDocument();
  });

  test("a retry that fails again stays on the error panel without an unhandled rejection", async () => {
    const unhandled = vi.fn();
    globalThis.process.on("unhandledRejection", unhandled);
    const spy = vi.spyOn(global, "fetch").mockResolvedValue(failing());

    render(
      <MemoryRouter>
        <Page />
      </MemoryRouter>,
    );

    await userEvent.click(await screen.findByRole("button", { name: /try again/i }));
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(await screen.findByText("Server exploded.")).toBeInTheDocument();
    expect(unhandled).not.toHaveBeenCalled();
    globalThis.process.off("unhandledRejection", unhandled);
  });
});
