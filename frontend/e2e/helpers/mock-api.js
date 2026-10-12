/**
 * Shared Playwright route-mocking helpers.
 *
 * All tests run against the Vite dev server only; no Django backend needed.
 * page.route() intercepts every /api/* call and returns canned JSON so we can
 * exercise the full React rendering pipeline without network I/O.
 */

export const MOCK_USER = {
  id: 1,
  display_name: "Test User",
  email: "test@example.com",
  campus_name: "Test University",
  email_verified: true,
  onboarding_step: 3,
};

/** The token the mocked /api/auth/csrf endpoint hands out. */
export const CSRF_TOKEN = "e2e-csrf-token";

// Enum values below are the backend's own (listings/models.py). A fixture that
// invents one ("published", "pending") exercises a state the API cannot
// produce and hides the states it can.
//   Listing.Status:      available | reserved | picked_up | expired | donated
//   Listing.Condition:   new | good | fair
//   Reservation.Status:  requested | confirmed | cancelled | completed | expired_unresolved

/**
 * Stub the auth bootstrap endpoints so the app initialises as the given user
 * (or as logged-out when user is null).
 */
export async function mockAuth(page, user = MOCK_USER) {
  // Hermetic seal, registered FIRST so every route mocked later takes
  // precedence (Playwright matches newest-first). Any endpoint a test didn't
  // explicitly mock gets a clean 404 instead of leaking through the Vite
  // proxy to whatever real backend happens to be running on this machine.
  await page.route("**/api/**", (route) =>
    route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ detail: "unmocked endpoint (e2e)" }),
    }),
  );
  // Like Django's ensure_csrf_cookie view: the response sets the csrftoken
  // cookie the client echoes back as X-CSRFToken on every unsafe request.
  await page.route("**/api/auth/csrf", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      headers: { "Set-Cookie": `csrftoken=${CSRF_TOKEN}; Path=/; SameSite=Lax` },
      body: JSON.stringify({ detail: "CSRF cookie set" }),
    }),
  );
  await page.route("**/api/auth/me", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ user }),
    }),
  );
}

/**
 * Stub POST /api/auth/login to succeed and return the mock user.
 */
export async function mockLogin(page, user = MOCK_USER) {
  await page.route("**/api/auth/login", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ user }),
    }),
  );
}

/**
 * Stub POST /api/auth/register to succeed and return the mock user.
 */
export async function mockRegister(page, user = MOCK_USER) {
  await page.route("**/api/auth/register", (route) =>
    route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({ user }),
    }),
  );
}

/**
 * Stub notification badge polling so it doesn't interfere with other mocks.
 */
export async function mockNotifications(page, unreadCount = 0) {
  await page.route("**/api/notifications**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ unread_count: unreadCount, count: 0, results: [] }),
    }),
  );
}

export function makeListing(overrides = {}) {
  return {
    id: 42,
    title: "Desk Lamp",
    description: "Good sturdy desk lamp, barely used.",
    category: "lighting",
    condition: "good",
    price_type: "free",
    price_amount: "0.00",
    status: "available",
    pickup_zone: "North Hall lobby",
    available_until: new Date(Date.now() + 86400000 * 3).toISOString(),
    estimated_retail_value: "25.00",
    estimated_student_savings: "25.00",
    image_url: null,
    reservation_count: 0,
    is_saved: false,
    saved_count: 0,
    update_count: 0,
    can_post_update: true,
    owner: { id: 2, display_name: "Other User" },
    ...overrides,
  };
}

/** A reservation as ReservationSerializer returns it, freshly requested. */
export function makeReservation(overrides = {}) {
  return {
    id: 7,
    listing: 42,
    status: "requested",
    relationship: "claimant",
    pickup_time_window: "Tomorrow 6-7 pm",
    pickup_slots: [],
    confirmed_slot: null,
    pickup_zone: "North Hall lobby",
    move_out_deadline: new Date(Date.now() + 86400000 * 3).toISOString(),
    handoff_code: "DC-007-0042",
    handoff_pin_display: null,
    counterparty_name: "Other User",
    time_pressure: "normal",
    next_step: "Wait for the owner to confirm your pickup window.",
    handoff_checklist: [],
    // A claimant can only cancel. Completion is never a PATCHable action: it
    // happens when the owner enters the claimant's PIN (verify-pin).
    allowed_actions: ["cancelled"],
    can_leave_feedback: false,
    my_feedback: null,
    feedback_summary: { count: 0, average_rating: null },
    feedback_entries: [],
    unread_messages: 0,
    listing_detail: { id: 42, title: "Desk Lamp", available_until: new Date(Date.now() + 86400000 * 3).toISOString() },
    ...overrides,
  };
}

/**
 * GET /api/dashboard as DashboardView builds it. Every key the page reads is
 * present: an empty object here crashes the dashboard into its error boundary.
 */
export function makeDashboard(overrides = {}) {
  const now = new Date().toISOString();
  return {
    stats: {
      active_listings: 2,
      saved_items: 1,
      incoming_requests: 1,
      outgoing_claims: 0,
      rescued_total: 3,
      active_scans: 1,
      pending_scan_items: 2,
      donation_routes: 0,
      ready_to_publish_now: 1,
      blocked_scans: 0,
      listings_expiring_soon: 0,
      incoming_pickup_actions: 1,
      tasks_due_today: 0,
      overdue_tasks: 0,
      feedback_to_leave: 0,
      open_requests: 0,
      matched_requests: 0,
      fulfillable_needs: 0,
      requests_with_matches: 0,
      scan_savings_total: "48.50",
      unread_notifications: 1,
    },
    category_breakdown: [
      { name: "Lighting", value: 2 },
      { name: "Storage", value: 1 },
    ],
    status_breakdown: [
      { name: "Available", value: 2 },
      { name: "Picked Up", value: 1 },
    ],
    urgent_listings: [],
    saved_preview: [],
    scan_overview: [
      {
        id: 9,
        name: "Room 204 Move-Out",
        status: "in_progress",
        room_label: "North 204",
        room_type: "dorm_room",
        move_out_deadline: new Date(Date.now() + 86400000 * 4).toISOString(),
        progress_percent: 40,
        summary: { total_items: 5, ready_to_publish_count: 1, missing_info_count: 0 },
      },
    ],
    action_queues: {
      ready_to_publish_now: [
        { id: 31, title: "Clip-on Fan", category: "comfort", notes: "Works, a little dusty.", image_url: null, scan_session: 9 },
      ],
      scans_blocked_by_missing_info: [],
      listings_expiring_soon: [],
      incoming_pickup_actions: [
        {
          reservation_id: 7,
          listing_id: 42,
          listing_title: "Desk Lamp",
          pickup_time_window: "Tomorrow 6-7 pm",
          status: "requested",
          claimant_name: "Jordan Lee",
          updated_at: now,
        },
      ],
      handoffs_waiting_for_feedback: [],
      my_request_matches: [],
      campus_open_requests: [],
      fulfill_opportunities: [],
      request_recommendations: [],
      move_out_tasks: [],
    },
    notification_preview: [
      {
        id: 5,
        type: "reservation",
        title: "New pickup request",
        body: "Jordan Lee wants your Desk Lamp.",
        link_path: "/handoffs/7",
        priority: "high",
        is_read: false,
        read_at: null,
        created_at: now,
      },
    ],
    recent_activity: [],
    ...overrides,
  };
}

/** Stub GET /api/dashboard with a realistic payload. */
export async function mockDashboard(page, overrides = {}) {
  await page.route("**/api/dashboard", (route) => route.fulfill({ json: makeDashboard(overrides) }));
}
