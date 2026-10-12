import { test, expect } from "./helpers/test.js";
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
      next_step: "Bring the handoff code and arrive during the confirmed pickup window.",
      handoff_pin_display: "482913",
      // The claimant's only status action. Completion is never one of them.
      allowed_actions: ["cancelled"],
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
    await expect(page.getByText("482913")).toBeVisible();
    await expect(page.getByRole("button", { name: /on my way/i })).toBeVisible();
    await expect(page.getByRole("button", { name: /mark handed off/i })).toHaveCount(0);
  });
});

// ---------------------------------------------------------------------------
// Completion contract: a handoff is completed only by the owner entering the
// claimant's PIN (POST /reservations/:id/verify-pin). There is no status PATCH
// to "completed" and no button that would send one.
// ---------------------------------------------------------------------------

test.describe("Handoff completion by PIN", () => {
  const OWNER_RESERVATION = {
    ...COMPLETED_RESERVATION,
    status: "confirmed",
    relationship: "owner",
    counterparty_name: "Jordan",
    can_leave_feedback: false,
    confirmed_slot: null,
    pickup_slots: [],
    handoff_pin_display: null,
    next_step: "Meet the claimant at the pickup zone, then enter their PIN.",
    allowed_actions: ["cancelled"],
  };

  async function mockHandoff(page, getReservation) {
    await mockAuth(page, MOCK_USER);
    await mockNotifications(page);
    await page.route(`**/api/reservations/${RESERVATION_ID}/messages`, (route) =>
      route.fulfill({ json: { count: 0, next: null, previous: null, results: [] } }),
    );
    await page.route(`**/api/reservations/${RESERVATION_ID}`, (route) => {
      // Fail loudly if anything tries the retired completion path.
      if (route.request().method() === "PATCH" && route.request().postDataJSON()?.status === "completed") {
        return route.fulfill({ status: 400, json: { status: ["Complete a handoff with the pickup PIN."] } });
      }
      return route.fulfill({ json: getReservation() });
    });
  }

  for (const status of ["confirmed", "expired_unresolved"]) {
    test(`owner completes a ${status} handoff by entering the PIN`, async ({ page }) => {
      let completed = false;
      const statusPatches = [];
      page.on("request", (request) => {
        if (request.method() === "PATCH" && request.url().endsWith(`/api/reservations/${RESERVATION_ID}`)) {
          statusPatches.push(request.postDataJSON());
        }
      });
      await mockHandoff(page, () =>
        completed
          ? { ...OWNER_RESERVATION, status: "completed", allowed_actions: [], can_leave_feedback: true }
          : { ...OWNER_RESERVATION, status },
      );
      await page.route(`**/api/reservations/${RESERVATION_ID}/verify-pin`, (route) => {
        completed = true;
        return route.fulfill({ json: { detail: "Handoff complete." } });
      });

      await page.goto(`/handoffs/${RESERVATION_ID}`);

      const pin = page.getByLabel("Pickup PIN");
      await expect(pin).toBeVisible();
      await expect(page.getByRole("button", { name: /mark handed off/i })).toHaveCount(0);
      await expect(page.getByRole("button", { name: /^completed?$/i })).toHaveCount(0);
      if (status === "expired_unresolved") {
        await expect(page.getByText(/passed 48 hours without completion/i)).toBeVisible();
      }

      const verify = page.waitForRequest(`**/api/reservations/${RESERVATION_ID}/verify-pin`);
      await pin.fill("482913");

      const request = await verify;
      expect(request.method()).toBe("POST");
      expect(request.postDataJSON()).toEqual({ pin: "482913" });
      await expect(page.getByText(/handoff complete\. nice rescue/i)).toBeVisible();
      await expect(page.getByRole("button", { name: /save feedback/i })).toBeVisible();
      expect(statusPatches).toEqual([]);
    });
  }

  test("a status change made by the other party appears without a reload", async ({ page }) => {
    let status = "requested";
    await page.clock.install();
    await mockHandoff(page, () => ({
      ...OWNER_RESERVATION,
      relationship: "claimant",
      counterparty_name: "Maya",
      status,
      handoff_pin_display: status === "confirmed" ? "482913" : null,
    }));

    await page.goto(`/handoffs/${RESERVATION_ID}`);
    await expect(page.getByText(/waiting for maya to approve your request/i)).toBeVisible();

    status = "confirmed"; // the owner approved on their own device
    await page.clock.runFor(16_000);

    await expect(page.getByText("482913")).toBeVisible();
    await expect(page.getByText(/waiting for maya to approve your request/i)).toHaveCount(0);
  });

  test("a server error offers a retry instead of claiming the handoff is missing", async ({ page }) => {
    let healthy = false;
    await mockAuth(page, MOCK_USER);
    await mockNotifications(page);
    await page.route(`**/api/reservations/${RESERVATION_ID}/messages`, (route) =>
      route.fulfill({ json: { count: 0, next: null, previous: null, results: [] } }),
    );
    await page.route(`**/api/reservations/${RESERVATION_ID}`, (route) =>
      healthy
        ? route.fulfill({ json: OWNER_RESERVATION })
        : route.fulfill({ status: 500, json: { detail: "Internal server error." } }),
    );

    await page.goto(`/handoffs/${RESERVATION_ID}`);

    await expect(page.getByRole("heading", { name: /could not be loaded/i })).toBeVisible();
    await expect(page.getByText(/handoff not found/i)).toHaveCount(0);

    healthy = true;
    await page.getByRole("button", { name: /try again/i }).click();

    await expect(page.getByLabel("Pickup PIN")).toBeVisible();
  });

  test("a missing reservation reads as not found", async ({ page }) => {
    await mockAuth(page, MOCK_USER);
    await mockNotifications(page);
    // No route for the reservation: the hermetic seal answers 404.
    await page.goto(`/handoffs/${RESERVATION_ID}`);

    await expect(page.getByRole("heading", { name: /handoff not found/i })).toBeVisible();
    await expect(page.getByRole("button", { name: /try again/i })).toHaveCount(0);
  });
});
