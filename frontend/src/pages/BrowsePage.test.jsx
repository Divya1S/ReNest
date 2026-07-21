import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, describe, expect, test, vi } from "vitest";

import { axe } from "../test/setup";

import BrowsePage from "./BrowsePage";

vi.mock("../components/TrendingStrip", () => ({ default: () => null }));

const LAMP = {
  id: 1,
  title: "Desk Lamp",
  description: "Good desk lamp",
  category: "lighting",
  price_type: "low_cost",
  price_amount: "5.00",
  status: "published",
  pickup_zone: "North Hall",
  available_until: new Date(Date.now() + 86400000).toISOString(),
  estimated_retail_value: "25.00",
  estimated_student_savings: "20.00",
  image_url: null,
  reservation_count: 0,
  is_saved: false,
  saved_count: 0,
  update_count: 0,
  can_post_update: false,
};
const BINS = { ...LAMP, id: 2, title: "Storage Bins", category: "storage" };

function mockResponse(results, { count, next = null } = {}) {
  return {
    ok: true,
    status: 200,
    json: async () => ({ count: count ?? results.length, next, previous: null, results }),
  };
}

function renderBrowsePage(initialEntry = "/browse") {
  const router = createMemoryRouter(
    [{ path: "/browse", element: <BrowsePage /> }],
    { initialEntries: [initialEntry] },
  );
  render(<RouterProvider router={router} />);
  return router;
}

describe("BrowsePage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  test("renders listings returned by the API", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(mockResponse([LAMP, BINS]));
    renderBrowsePage();

    await waitFor(() => {
      expect(screen.getByText("Desk Lamp")).toBeInTheDocument();
      expect(screen.getByText("Storage Bins")).toBeInTheDocument();
      expect(screen.getByText("2 Results")).toBeInTheDocument();
    });
  });

  test("reads initial filter state from the URL", async () => {
    const spy = vi.spyOn(global, "fetch").mockResolvedValue(mockResponse([BINS]));
    renderBrowsePage("/browse?category=storage&price_type=free");

    await waitFor(() => {
      const allUrls = spy.mock.calls.map((c) => c[0]);
      const listingsUrl = allUrls.find((u) => u.includes("category=storage"));
      expect(listingsUrl).toBeDefined();
      expect(listingsUrl).toContain("price_type=free");
    });
  });

  test("clicking a category chip re-fetches with that category and marks it pressed", async () => {
    const spy = vi.spyOn(global, "fetch")
      .mockResolvedValueOnce(mockResponse([LAMP, BINS]))
      .mockResolvedValueOnce(mockResponse([LAMP, BINS]))
      .mockResolvedValue(mockResponse([BINS]));
    renderBrowsePage();

    await waitFor(() => expect(screen.getByText("Desk Lamp")).toBeInTheDocument());

    const storageBtn = screen.getByRole("button", { name: "Storage" });
    expect(storageBtn).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(storageBtn);

    await waitFor(() => {
      const lastUrl = spy.mock.calls.at(-1)[0];
      expect(lastUrl).toContain("category=storage");
      expect(screen.getByRole("button", { name: "Storage" })).toHaveAttribute("aria-pressed", "true");
    });
  });

  test("clicking a price filter re-fetches with that price_type", async () => {
    const spy = vi.spyOn(global, "fetch")
      .mockResolvedValueOnce(mockResponse([LAMP, BINS]))
      .mockResolvedValue(mockResponse([LAMP]));
    renderBrowsePage();

    await waitFor(() => expect(screen.getByText("Desk Lamp")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Free" }));

    await waitFor(() => {
      const lastUrl = spy.mock.calls.at(-1)[0];
      expect(lastUrl).toContain("price_type=free");
    });
  });

  test("search input is debounced — no new fetch fires before 350ms", async () => {
    vi.useFakeTimers();
    const spy = vi.spyOn(global, "fetch").mockResolvedValue(mockResponse([LAMP]));
    renderBrowsePage();

    // Flush initial render and all pending timers/promises
    await act(() => vi.runAllTimersAsync());

    const callCountAfterMount = spy.mock.calls.length;

    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "lamp" } });

    // Debounce hasn't fired yet — call count unchanged
    expect(spy.mock.calls.length).toBe(callCountAfterMount);

    // Advance past the 350ms debounce window
    await act(() => vi.advanceTimersByTimeAsync(400));

    // A new fetch should have fired with the search term
    expect(spy.mock.calls.length).toBeGreaterThan(callCountAfterMount);
    expect(spy.mock.calls.at(-1)[0]).toContain("search=lamp");
  });

  test("Load More button appends page 2 results and hides when no further pages", async () => {
    const page2Item = { ...BINS, id: 9, title: "More Bins" };
    vi.spyOn(global, "fetch")
      .mockResolvedValueOnce(mockResponse([LAMP], { count: 2, next: "/api/listings?page=2" }))
      .mockResolvedValue(mockResponse([page2Item], { count: 2, next: null }));
    renderBrowsePage();

    // IntersectionObserver fires immediately on sentinel mount, auto-triggering page 2
    await waitFor(() => {
      expect(screen.getByText("Desk Lamp")).toBeInTheDocument();
      expect(screen.getByText("More Bins")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /load more/i })).not.toBeInTheDocument();
    });
  });

  test("shows error state with a retry button when the fetch fails", async () => {
    vi.spyOn(global, "fetch").mockRejectedValue(new Error("Network error"));
    renderBrowsePage();

    await waitFor(() => {
      expect(screen.getByText(/failed to load listings/i)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
    });
  });

  test("shows empty state with reset button when filtered but no results match", async () => {
    vi.spyOn(global, "fetch")
      .mockResolvedValueOnce(mockResponse([LAMP]))
      .mockResolvedValue(mockResponse([]));
    renderBrowsePage();

    await waitFor(() => expect(screen.getByText("Desk Lamp")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Storage" }));

    await waitFor(() => {
      expect(screen.getByText(/no matches found/i)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /reset filters/i })).toBeInTheDocument();
    });
  });

  test("passes axe accessibility audit on load", async () => {
    global.fetch = vi.fn().mockResolvedValue(
      mockResponse([LAMP, BINS]),
    );
    renderBrowsePage();
    await waitFor(() => expect(screen.getByText("Desk Lamp")).toBeInTheDocument());
    const results = await axe(document.body);
    expect(results).toHaveNoViolations();
  });
});
