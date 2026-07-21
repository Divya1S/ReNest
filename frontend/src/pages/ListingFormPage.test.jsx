import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import ListingFormPage from "./ListingFormPage";

function jsonResponse(body, status = 200) {
  return Promise.resolve({ ok: status < 400, status, json: async () => body });
}

function mockApi(overrides = {}) {
  return vi.spyOn(global, "fetch").mockImplementation((url, opts = {}) => {
    const method = (opts.method || "GET").toUpperCase();
    const key = `${method} ${url}`;
    for (const [pattern, body] of Object.entries(overrides)) {
      if (key.includes(pattern)) {
        return jsonResponse(typeof body === "function" ? body(opts) : body);
      }
    }
    if (key.includes("GET /api/listings/pricing-hint")) {
      return jsonResponse({ sample_size: 0, suggestion: "" });
    }
    return jsonResponse({});
  });
}

function renderCreateForm() {
  const router = createMemoryRouter(
    [
      { path: "/listings/new", element: <ListingFormPage /> },
      { path: "/listings/:listingId", element: <div>DETAIL STUB</div> },
    ],
    { initialEntries: ["/listings/new"] },
  );
  render(<RouterProvider router={router} />);
  return router;
}

const FUTURE_DEADLINE = (() => {
  const d = new Date(Date.now() + 5 * 86400000);
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0, 16);
})();

function fillRequiredFields() {
  fireEvent.change(screen.getByPlaceholderText(/dorm fan, storage bin/i), {
    target: { value: "Sturdy desk lamp" },
  });
  fireEvent.change(screen.getByPlaceholderText(/describe condition/i), {
    target: { value: "Bright LED lamp, barely used, includes bulb and cable." },
  });
  fireEvent.change(screen.getByPlaceholderText(/dorm a lobby/i), {
    target: { value: "North Hall front desk" },
  });
  fireEvent.change(document.querySelector('input[type="datetime-local"]'), {
    target: { value: FUTURE_DEADLINE },
  });
}

describe("ListingFormPage (create)", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test("submitting an empty form shows field errors and sends nothing", async () => {
    const spy = mockApi();
    renderCreateForm();

    fireEvent.click(screen.getByRole("button", { name: /create listing/i }));

    await waitFor(() => {
      expect(screen.getByText("Title is required.")).toBeInTheDocument();
      expect(screen.getByText("Description is required.")).toBeInTheDocument();
      expect(screen.getByText("Pickup zone is required.")).toBeInTheDocument();
      expect(screen.getByText("Move-out deadline is required.")).toBeInTheDocument();
    });

    const postCalls = spy.mock.calls.filter(
      ([url, opts]) => String(url).endsWith("/api/listings") && opts?.method === "POST",
    );
    expect(postCalls).toHaveLength(0);
  });

  test("low-cost pricing requires a price greater than zero", async () => {
    mockApi();
    renderCreateForm();
    fillRequiredFields();

    fireEvent.change(screen.getByDisplayValue("Free"), { target: { value: "low_cost" } });
    fireEvent.click(screen.getByRole("button", { name: /create listing/i }));

    await waitFor(() => {
      expect(screen.getByText(/price greater than \$0/i)).toBeInTheDocument();
    });
  });

  test("valid form POSTs the listing and navigates to its detail page", async () => {
    const spy = mockApi({
      "POST /api/listings": { id: 55, title: "Sturdy desk lamp" },
    });
    renderCreateForm();
    fillRequiredFields();

    fireEvent.click(screen.getByRole("button", { name: /create listing/i }));

    await waitFor(() => {
      const createCall = spy.mock.calls.find(
        ([url, opts]) => String(url).endsWith("/api/listings") && opts?.method === "POST",
      );
      expect(createCall).toBeDefined();
      const payload = JSON.parse(createCall[1].body);
      expect(payload).toMatchObject({
        title: "Sturdy desk lamp",
        pickup_zone: "North Hall front desk",
        price_type: "free",
      });
    });

    await waitFor(() => {
      expect(screen.getByText("DETAIL STUB")).toBeInTheDocument();
    });
  });
});
