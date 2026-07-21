/**
 * Shared Playwright route-mocking helpers.
 *
 * All tests run against the Vite dev server only — no Django backend needed.
 * page.route() intercepts every /api/* call and returns canned JSON so we can
 * exercise the full React rendering pipeline without network I/O.
 */

export const MOCK_USER = {
  id: 1,
  display_name: "Test User",
  email: "test@example.com",
  campus_name: "Test University",
};

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
  await page.route("**/api/auth/csrf", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "{}" }),
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
    price_amount: null,
    status: "published",
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

export function makeReservation(overrides = {}) {
  return {
    id: 7,
    status: "pending",
    relationship: "claimant",
    listing_detail: { id: 42, title: "Desk Lamp" },
    ...overrides,
  };
}
