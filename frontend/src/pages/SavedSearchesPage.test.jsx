import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { clearApiCache } from "../lib/apiCache";

import SavedSearchesPage from "./SavedSearchesPage";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const SEARCH_LAMPS = { id: 1, label: "Lamps under $10", keyword: "lamp", category: "lighting", price_type: "low_cost" };
const SEARCH_BINS  = { id: 2, label: "Storage bins",    keyword: "bins", category: "storage",  price_type: null };

function mockOk(data) {
  return { ok: true, status: 200, json: async () => data };
}
function mockErr(status = 500) {
  return { ok: false, status, json: async () => ({ detail: "Server error" }) };
}

function renderPage() {
  const router = createMemoryRouter(
    [
      { path: "/saved-searches", element: <SavedSearchesPage /> },
      { path: "/browse", element: <div>browse</div> },
    ],
    { initialEntries: ["/saved-searches"] },
  );
  render(<RouterProvider router={router} />);
}

describe("SavedSearchesPage", () => {
  beforeEach(() => clearApiCache());
  afterEach(() => vi.restoreAllMocks());

  test("shows loading skeletons while fetch is in-flight", () => {
    vi.spyOn(global, "fetch").mockReturnValue(new Promise(() => {}));
    renderPage();
    expect(document.querySelectorAll(".animate-pulse").length).toBeGreaterThan(0);
  });

  test("renders a row for each saved search", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(mockOk({ results: [SEARCH_LAMPS, SEARCH_BINS] }));
    renderPage();
    await waitFor(() => {
      expect(screen.getByText("Lamps under $10")).toBeInTheDocument();
      expect(screen.getByText("Storage bins")).toBeInTheDocument();
    });
  });

  test("handles a plain-array response (no results wrapper)", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(mockOk([SEARCH_LAMPS]));
    renderPage();
    await waitFor(() => expect(screen.getByText("Lamps under $10")).toBeInTheDocument());
  });

  test("shows error panel with retry button when fetch fails", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(mockErr());
    renderPage();
    await waitFor(() => {
      expect(screen.getByText(/couldn't load saved searches/i)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
    });
  });

  test("retry button re-fetches after an error", async () => {
    const fetchSpy = vi.spyOn(global, "fetch")
      .mockResolvedValueOnce(mockErr())
      .mockResolvedValue(mockOk({ results: [SEARCH_LAMPS] }));
    renderPage();

    await waitFor(() => screen.getByRole("button", { name: /try again/i }));
    fireEvent.click(screen.getByRole("button", { name: /try again/i }));

    await waitFor(() => expect(screen.getByText("Lamps under $10")).toBeInTheDocument());
    expect(fetchSpy.mock.calls.length).toBeGreaterThan(1);
  });

  test("shows empty state with a Browse link when no searches exist", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(mockOk({ results: [] }));
    renderPage();
    await waitFor(() => {
      expect(screen.getByText(/no saved searches yet/i)).toBeInTheDocument();
      expect(screen.getByRole("link", { name: /browse listings/i })).toBeInTheDocument();
    });
  });

  test("delete button removes the row optimistically before the API responds", async () => {
    // The DELETE response is deliberately delayed so we can assert the optimistic removal.
    let resolveDelete;
    vi.spyOn(global, "fetch")
      .mockResolvedValueOnce(mockOk({ results: [SEARCH_LAMPS, SEARCH_BINS] }))
      .mockReturnValueOnce(new Promise((res) => { resolveDelete = res; }));

    renderPage();
    await waitFor(() => expect(screen.getByText("Storage bins")).toBeInTheDocument());

    const deleteButtons = screen.getAllByRole("button", { name: /delete saved search/i });
    fireEvent.click(deleteButtons[1]); // delete "Storage bins"

    // Row gone immediately — DELETE hasn't resolved yet
    await waitFor(() => expect(screen.queryByText("Storage bins")).not.toBeInTheDocument());
    expect(screen.getByText("Lamps under $10")).toBeInTheDocument();

    // Allow DELETE to complete cleanly
    resolveDelete({ ok: true, status: 204, json: async () => ({}) });
  });

  test("each row links to /browse with correct query params", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(mockOk({ results: [SEARCH_LAMPS] }));
    renderPage();
    await waitFor(() => screen.getByText("Lamps under $10"));

    const link = screen.getByRole("link", { name: /lamps under/i });
    expect(link.getAttribute("href")).toContain("search=lamp");
    expect(link.getAttribute("href")).toContain("category=lighting");
    expect(link.getAttribute("href")).toContain("price_type=low_cost");
  });
});
