import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { clearApiCache } from "../lib/apiCache";

import MyReservationsPage from "./MyReservationsPage";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const RESERVATION = {
  id: 12,
  status: "confirmed",
  relationship: "owner",
  pickup_time_window: "Friday 3-5pm",
  allowed_actions: ["cancelled"],
  can_leave_feedback: false,
  unread_messages: 0,
  next_step: "Meet the claimant at the pickup zone, then enter their PIN.",
  counterparty_name: "Leo",
  listing_detail: { id: 8, title: "Desk Lamp", available_until: new Date().toISOString() },
};

function json(body, status = 200) {
  return Promise.resolve({ ok: status < 400, status, json: async () => body });
}

function renderPage() {
  return render(
    <MemoryRouter>
      <MyReservationsPage />
    </MemoryRouter>,
  );
}

describe("MyReservationsPage", () => {
  beforeEach(() => clearApiCache());
  afterEach(() => vi.restoreAllMocks());

  test("never offers completion as a card action", async () => {
    // Completion goes through the PIN in the Handoff Hub. Even a response
    // that still listed "completed" must not produce a button that PATCHes it.
    vi.spyOn(global, "fetch").mockImplementation(() =>
      json({ count: 1, next: null, previous: null, results: [{ ...RESERVATION, allowed_actions: ["completed", "cancelled"] }] }),
    );
    renderPage();

    expect(await screen.findByRole("button", { name: "Cancel reservation" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /completed|mark handed off/i })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /open handoff hub/i })).toHaveAttribute("href", "/handoffs/12");
  });

  test("approving a request sends the confirmed status", async () => {
    const spy = vi.spyOn(global, "fetch").mockImplementation((url, opts = {}) => {
      if ((opts.method || "GET") === "PATCH") return json({ ...RESERVATION, status: "confirmed" });
      return json({ count: 1, next: null, previous: null, results: [{ ...RESERVATION, status: "requested", allowed_actions: ["confirmed", "cancelled"] }] });
    });
    renderPage();

    await userEvent.click(await screen.findByRole("button", { name: "Approve request" }));

    await waitFor(() => {
      const patch = spy.mock.calls.find(([, opts]) => opts?.method === "PATCH");
      expect(patch).toBeDefined();
      expect(patch[0]).toBe("/api/reservations/12");
      expect(JSON.parse(patch[1].body)).toEqual({ status: "confirmed" });
    });
  });
});
