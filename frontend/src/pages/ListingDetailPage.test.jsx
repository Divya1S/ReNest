import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { AuthContext } from "../context/AuthContext";
import { clearApiCache } from "../lib/apiCache";

import ListingDetailPage from "./ListingDetailPage";

const BUYER = { id: 1, display_name: "Buyer Beth", email: "beth@example.com", campus_name: "Test U" };

const LISTING = {
  id: 7,
  owner: { id: 2, display_name: "Seller Sam" },
  title: "Mini Fridge",
  description: "Cold and compact.",
  category: "comfort",
  condition: "good",
  price_type: "low_cost",
  price_amount: "45.00",
  estimated_retail_value: "120.00",
  estimated_student_savings: "75.00",
  pickup_zone: "North Hall lobby",
  building: "North Hall",
  available_until: new Date(Date.now() + 3 * 86400000).toISOString(),
  status: "available",
  image_url: "/media/fridge.jpg",
  gallery: [],
  is_urgent: false,
  time_left_label: "3d left",
  can_edit: false,
  can_reserve: true,
  can_post_update: false,
  reservation_count: 0,
  is_saved: false,
  saved_count: 2,
  update_count: 0,
  view_count: 12,
  owner_trust_summary: {
    average_rating: 4.8,
    total_feedback_received: 5,
    trust_badge: "Trusted handoff",
    completed_rescues: 3,
    successful_handoffs: 4,
    active_listings: 2,
  },
  has_reported: false,
  can_report: true,
  created_at: new Date().toISOString(),
};

function jsonResponse(body, status = 200) {
  return Promise.resolve({ ok: status < 400, status, json: async () => body });
}

/** Route fetch calls by method + path so each endpoint can be scripted. */
function mockApi(overrides = {}) {
  return vi.spyOn(global, "fetch").mockImplementation((url, opts = {}) => {
    const method = (opts.method || "GET").toUpperCase();
    const key = `${method} ${url}`;
    for (const [pattern, body] of Object.entries(overrides)) {
      if (key.includes(pattern)) {
        return jsonResponse(typeof body === "function" ? body(opts) : body);
      }
    }
    if (key.includes("GET /api/listings/7/updates")) return jsonResponse([]);
    if (key.includes("GET /api/listings/7")) return jsonResponse(LISTING);
    return jsonResponse({});
  });
}

const authedContext = {
  user: BUYER,
  loading: false,
  authNotice: "",
  clearAuthNotice: () => {},
  refreshUser: async () => BUYER,
  login: async () => BUYER,
  register: async () => BUYER,
  logout: async () => {},
};

function renderDetail({ user = BUYER } = {}) {
  const router = createMemoryRouter(
    [{ path: "/listings/:listingId", element: <ListingDetailPage /> }],
    { initialEntries: ["/listings/7"] },
  );
  const ctx = user ? { ...authedContext, user } : { ...authedContext, user: null };
  render(
    <AuthContext.Provider value={ctx}>
      <RouterProvider router={router} />
    </AuthContext.Provider>,
  );
  return router;
}

describe("ListingDetailPage", () => {
  beforeEach(() => {
    clearApiCache();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test("signed-in non-owner can reserve with a pickup window", async () => {
    const spy = mockApi({
      "POST /api/reservations": { id: 99 },
    });
    renderDetail();

    await waitFor(() => expect(screen.getByRole("heading", { name: "Mini Fridge" })).toBeInTheDocument());

    fireEvent.change(screen.getByPlaceholderText(/pickup window/i), {
      target: { value: "Tomorrow 6-7 pm" },
    });
    fireEvent.click(screen.getByRole("button", { name: /reserve this item/i }));

    await waitFor(() => {
      const reserveCall = spy.mock.calls.find(
        ([url, opts]) => url.includes("/api/reservations") && opts?.method === "POST",
      );
      expect(reserveCall).toBeDefined();
      expect(JSON.parse(reserveCall[1].body)).toMatchObject({
        listing: 7,
        pickup_time_window: "Tomorrow 6-7 pm",
      });
    });
  });

  test("guests see a sign-in CTA instead of the reserve form", async () => {
    mockApi();
    renderDetail({ user: null });

    await waitFor(() => expect(screen.getByRole("heading", { name: "Mini Fridge" })).toBeInTheDocument());

    expect(screen.getByRole("link", { name: /sign in to reserve/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /reserve this item/i })).not.toBeInTheDocument();
    // Guests also don't get the report panel
    expect(screen.queryByText(/report this listing/i)).not.toBeInTheDocument();
  });

  test("gallery thumbnails switch the hero image", async () => {
    mockApi({
      "GET /api/listings/7/updates": [],
      "GET /api/listings/7": {
        ...LISTING,
        gallery: [{ id: 31, image_url: "/media/fridge-side.jpg", position: 0 }],
      },
    });
    renderDetail();

    await waitFor(() => expect(screen.getByRole("heading", { name: "Mini Fridge" })).toBeInTheDocument());

    const thumbs = screen.getAllByRole("button", { name: /show photo/i });
    expect(thumbs).toHaveLength(2);

    fireEvent.click(thumbs[1]);
    const hero = screen.getByAltText("Mini Fridge");
    expect(hero).toHaveAttribute("src", "/media/fridge-side.jpg");
    expect(thumbs[1]).toHaveAttribute("aria-pressed", "true");
  });

  test("owner sees edit tools instead of the reserve form", async () => {
    mockApi();
    renderDetail({ user: { ...BUYER, id: 2, display_name: "Seller Sam" } });

    await waitFor(() => expect(screen.getByRole("heading", { name: "Mini Fridge" })).toBeInTheDocument());

    expect(screen.getAllByRole("link", { name: /edit/i }).length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: /reserve this item/i })).not.toBeInTheDocument();
  });
});
