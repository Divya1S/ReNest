import { test, expect } from "@playwright/test";
import { MOCK_USER, mockAuth, mockNotifications } from "./helpers/mock-api.js";

// ---------------------------------------------------------------------------
// Critical path 3: Handoff → Feedback
// ---------------------------------------------------------------------------

const RESERVATION_ID = 5;

const COMPLETED_RESERVATION = {
  id: RESERVATION_ID,
  status: "completed",
  relationship: "claimant",
  pickup_time_window: "Tonight from 6pm to 7pm",
  pickup_zone: "North Hall lobby",
  move_out_deadline: new Date().toISOString(),
  handoff_code: "DC-005-0005",
  next_step: "Leave feedback to close the handoff.",
  handoff_checklist: ["Meet at North Hall lobby.", "Confirm item condition."],
  counterparty_name: "Jordan",
  time_pressure: "normal",
  allowed_actions: [],
  can_leave_feedback: true,
  my_feedback: null,
  feedback_summary: { count: 0, average_rating: null },
  feedback_entries: [],
  listing_detail: { id: 8, title: "Desk Lamp" },
};

const FEEDBACK_RESPONSE = {
  id: 9,
  reservation: RESERVATION_ID,
  listing_title: "Desk Lamp",
  reviewer: { id: MOCK_USER.id, display_name: MOCK_USER.display_name },
  reviewee: { id: 2, display_name: "Jordan" },
  rating: 5,
  tags: ["responsive"],
  note: "Great handoff, very smooth.",
  created_at: new Date().toISOString(),
};

const RESERVATION_AFTER_FEEDBACK = {
  ...COMPLETED_RESERVATION,
  can_leave_feedback: false,
  my_feedback: FEEDBACK_RESPONSE,
  feedback_summary: { count: 1, average_rating: 5 },
  feedback_entries: [FEEDBACK_RESPONSE],
};

test.describe("Handoff → Feedback", () => {
  test("completed handoff shows feedback form", async ({ page }) => {
    await mockAuth(page, MOCK_USER);
    await mockNotifications(page);

    await page.route(`**/api/reservations/${RESERVATION_ID}`, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(COMPLETED_RESERVATION),
      }),
    );

    await page.goto(`/handoffs/${RESERVATION_ID}`);

    await expect(page.getByRole("button", { name: /save feedback/i })).toBeVisible();
    await expect(page.getByRole("button", { name: /5 stars/i })).toBeVisible();
  });

  test("user submits feedback and sees their submitted feedback", async ({ page }) => {
    await mockAuth(page, MOCK_USER);
    await mockNotifications(page);

    // Switch the GET response to "after feedback" only once the POST has been made.
    let feedbackPosted = false;

    await page.route(`**/api/reservations/${RESERVATION_ID}/feedback`, (route) => {
      feedbackPosted = true;
      return route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify(FEEDBACK_RESPONSE),
      });
    });

    await page.route(`**/api/reservations/${RESERVATION_ID}`, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(feedbackPosted ? RESERVATION_AFTER_FEEDBACK : COMPLETED_RESERVATION),
      }),
    );

    await page.goto(`/handoffs/${RESERVATION_ID}`);

    // Wait for feedback form
    await expect(page.getByRole("button", { name: /save feedback/i })).toBeVisible();

    // Select 5 stars
    await page.getByRole("button", { name: /5 stars/i }).click();

    // Fill in note
    await page.getByPlaceholder(/short note about how the pickup went/i).fill("Great handoff, very smooth.");

    // Submit
    await page.getByRole("button", { name: /save feedback/i }).click();

    // The submitted feedback note should be visible
    await expect(page.getByText("Great handoff, very smooth.").first()).toBeVisible();
  });

  test("feedback form is not shown when feedback already submitted", async ({ page }) => {
    await mockAuth(page, MOCK_USER);
    await mockNotifications(page);

    await page.route(`**/api/reservations/${RESERVATION_ID}`, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(RESERVATION_AFTER_FEEDBACK),
      }),
    );

    await page.goto(`/handoffs/${RESERVATION_ID}`);

    // Feedback form should not be present
    await expect(page.getByRole("button", { name: /save feedback/i })).not.toBeVisible();
    // Previously submitted note should be shown instead
    await expect(page.getByText("Great handoff, very smooth.").first()).toBeVisible();
  });

  test("confirmed reservation shows the stage-gated pickup card", async ({ page }) => {
    const activeReservation = {
      ...COMPLETED_RESERVATION,
      status: "confirmed",
      can_leave_feedback: false,
      next_step: "Meet at North Hall lobby to complete the handoff.",
      allowed_actions: ["complete"],
    };

    await mockAuth(page, MOCK_USER);
    await mockNotifications(page);

    await page.route(`**/api/reservations/${RESERVATION_ID}`, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(activeReservation),
      }),
    );

    await page.goto(`/handoffs/${RESERVATION_ID}`);

    // Stage card: claimant side shows the at-pickup section with en-route action.
    await expect(page.getByText("At pickup", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: /on my way/i })).toBeVisible();
  });
});
