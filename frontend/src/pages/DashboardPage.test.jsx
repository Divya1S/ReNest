import { act, render, screen, within } from "@testing-library/react";
import React from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { AuthContext } from "../context/AuthContext";
import { clearApiCache } from "../lib/apiCache";

import DashboardPage from "./DashboardPage";

const USER = { id: 1, display_name: "Maya Chen", email: "maya@example.com", campus_name: "Test U", onboarding_step: 3 };

// The shape DashboardView returns; the charts are left empty because jsdom
// cannot lay them out.
const DASHBOARD = {
  stats: { incoming_pickup_actions: 1, tasks_due_today: 0, ready_to_publish_now: 0, scan_savings_total: "48.50" },
  category_breakdown: [],
  status_breakdown: [],
  scan_overview: [],
  notification_preview: [],
  action_queues: {
    ready_to_publish_now: [],
    incoming_pickup_actions: [
      {
        reservation_id: 7,
        listing_id: 42,
        listing_title: "Desk Lamp",
        pickup_time_window: "Tomorrow 6-7 pm",
        status: "requested",
        claimant_name: "Jordan Lee",
        updated_at: "2026-09-09T14:00:00Z",
      },
      {
        reservation_id: 8,
        listing_id: 43,
        listing_title: "Mini Fridge",
        pickup_time_window: "",
        status: "confirmed",
        claimant_name: "Sam Park",
        updated_at: "2026-09-09T15:00:00Z",
      },
    ],
    move_out_tasks: [],
    handoffs_waiting_for_feedback: [],
    fulfill_opportunities: [],
    request_recommendations: [],
  },
};

function renderDashboard() {
  const auth = {
    user: USER,
    loading: false,
    authNotice: "",
    clearAuthNotice: () => {},
    refreshUser: async () => USER,
    login: async () => USER,
    register: async () => ({ user: USER }),
    logout: async () => {},
  };
  return render(
    <AuthContext.Provider value={auth}>
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>
    </AuthContext.Provider>,
  );
}

describe("DashboardPage", () => {
  beforeEach(() => {
    clearApiCache();
    window.localStorage.clear();
    vi.spyOn(global, "fetch").mockResolvedValue({ ok: true, status: 200, json: async () => DASHBOARD });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test("the pickup table shows each request's pickup window and status from the API", async () => {
    // Regression: the column read a pickup_zone the payload never carried, so
    // every row said "TBD".
    renderDashboard();

    const lamp = (await screen.findByText("Desk Lamp")).closest("tr");
    expect(screen.getByRole("columnheader", { name: "Pickup window" })).toBeInTheDocument();
    expect(within(lamp).getByText("Tomorrow 6-7 pm")).toBeInTheDocument();
    expect(within(lamp).getByText("Requested")).toBeInTheDocument();
    expect(within(lamp).getByRole("link", { name: /manage handoff/i })).toHaveAttribute("href", "/handoffs/7");

    const fridge = screen.getByText("Mini Fridge").closest("tr");
    expect(within(fridge).getByText("To be arranged")).toBeInTheDocument();
    expect(within(fridge).getByText("Confirmed")).toBeInTheDocument();
    expect(screen.queryByText("TBD")).not.toBeInTheDocument();
  });

  test("the install banner does not promise offline use", async () => {
    // Offline, the service worker serves only a static "You're offline" page.
    renderDashboard();
    await screen.findByText("Desk Lamp");

    act(() => {
      window.dispatchEvent(new Event("beforeinstallprompt"));
    });

    const banner = (await screen.findByText("Add ReNest to your home screen")).parentElement;
    expect(banner).toHaveTextContent(/home screen/i);
    expect(banner).not.toHaveTextContent(/offline/i);
    expect(screen.getByRole("button", { name: "Install" })).toBeInTheDocument();
  });
});
