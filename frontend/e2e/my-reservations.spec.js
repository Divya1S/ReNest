import { test, expect } from "@playwright/test";

import { MOCK_USER, mockAuth, mockNotifications } from "./helpers/mock-api.js";

// ---------------------------------------------------------------------------
// My Reservations against the REAL backend contract: the reservations list is
// a DRF paginated envelope, not a bare array. This page once crashed on
// exactly that (`.map is not a function`) — this spec pins the fix.
// ---------------------------------------------------------------------------

const RESERVATION = {
  id: 12,
  status: "confirmed",
  relationship: "claimant",
  pickup_time_window: "Friday 3-5pm",
  pickup_slots: [],
  confirmed_slot: null,
  allowed_actions: [],
  can_leave_feedback: false,
  my_feedback: null,
  feedback_summary: { count: 0, average_rating: null },
  feedback_entries: [],
  handoff_checklist: [],
  next_step: "Meet at the lobby with your PIN ready.",
  counterparty_name: "Maya",
  time_pressure: "normal",
  unread_messages: 2,
  listing_detail: { id: 8, title: "Desk Lamp", available_until: new Date().toISOString() },
};

test.describe("My Reservations", () => {
  test("renders cards and the unread-messages badge from a paginated payload", async ({ page }) => {
    await mockAuth(page, MOCK_USER);
    await mockNotifications(page);
    await page.route("**/api/reservations", (route) =>
      route.fulfill({
        json: { count: 1, next: null, previous: null, results: [RESERVATION] },
      }),
    );

    await page.goto("/my-reservations");

    await expect(page.getByRole("heading", { name: /coordinate handoff/i })).toBeVisible();
    await expect(page.getByText("Desk Lamp")).toBeVisible();
    const badge = page.getByRole("link", { name: /2 new messages/i });
    await expect(badge).toBeVisible();
    await expect(badge).toHaveAttribute("href", "/handoffs/12");
    await expect(page.getByText("Meet at the lobby with your PIN ready.")).toBeVisible();
  });
});
