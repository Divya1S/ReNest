import { test, expect } from "./helpers/test.js";
import {
  CSRF_TOKEN,
  MOCK_USER,
  makeDashboard,
  makeListing,
  makeReservation,
  mockAuth,
  mockDashboard,
  mockNotifications,
  mockRegister,
} from "./helpers/mock-api.js";

// ---------------------------------------------------------------------------
// Critical path 1: Register → Post listing → Reserve
// ---------------------------------------------------------------------------

test.describe("Register → Post listing → Reserve", () => {
  // ── 1. Registration flow ────────────────────────────────────────────────

  test("user can register a new account and is redirected to verify-email", async ({ page }) => {
    // Start as logged-out
    await mockAuth(page, null);
    await mockRegister(page);

    await page.goto("/register");
    await expect(page.getByRole("heading", { name: /start rescuing smarter/i })).toBeVisible();

    await page.getByLabel("Full Name").fill("Test User");
    await page.getByLabel("Email Address").fill("test@example.com");
    await page.getByLabel("Campus Name").fill("Test University");
    // Use nth to disambiguate the two password fields
    await page.getByLabel("Password").nth(0).fill("StrongPass123!");
    await page.getByLabel("Confirm Password").fill("StrongPass123!");

    const registerRequest = page.waitForRequest("**/api/auth/register");
    await page.getByRole("button", { name: /create account/i }).click();

    expect((await registerRequest).headers()["x-csrftoken"]).toBe(CSRF_TOKEN);
    await expect(page).toHaveURL(/\/verify-email/);
    await expect(page.getByRole("heading", { name: /check your inbox|verify your email/i }).first()).toBeVisible();
  });

  test("a registration submitted before the CSRF cookie has arrived still sends the token", async ({ page }) => {
    // The register form renders while the auth bootstrap is still fetching
    // /api/auth/csrf. The server now rejects register, login and password
    // reset without the header, so the request has to wait for the cookie.
    await mockAuth(page, null);
    await mockRegister(page);
    let csrfRequests = 0;
    await page.route("**/api/auth/csrf", async (route) => {
      csrfRequests += 1;
      await new Promise((resolve) => setTimeout(resolve, 1500));
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        headers: { "Set-Cookie": `csrftoken=${CSRF_TOKEN}; Path=/; SameSite=Lax` },
        body: "{}",
      });
    });

    await page.goto("/register");
    await page.getByLabel("Full Name").fill("Test User");
    await page.getByLabel("Email Address").fill("test@example.com");
    await page.getByLabel("Password").nth(0).fill("StrongPass123!");
    await page.getByLabel("Confirm Password").fill("StrongPass123!");

    // The cookie is not there yet: the bootstrap response is still pending.
    expect(await page.evaluate(() => document.cookie)).not.toContain("csrftoken");
    const registerRequest = page.waitForRequest("**/api/auth/register");
    await page.getByRole("button", { name: /create account/i }).click();

    expect((await registerRequest).headers()["x-csrftoken"]).toBe(CSRF_TOKEN);
    // It waited for the in-flight bootstrap request rather than firing its own.
    expect(csrfRequests).toBe(1);
    await expect(page).toHaveURL(/\/verify-email/);
  });

  test("register form shows error when passwords do not match", async ({ page }) => {
    await mockAuth(page, null);

    await page.goto("/register");

    await page.getByLabel("Full Name").fill("Test User");
    await page.getByLabel("Email Address").fill("test@example.com");
    await page.getByLabel("Password").nth(0).fill("StrongPass123!");
    await page.getByLabel("Confirm Password").fill("DifferentPass!");
    // Trigger blur validation
    await page.getByLabel("Full Name").click();

    await expect(page.getByText(/passwords do not match/i)).toBeVisible();
  });

  // ── 2. Post listing flow ─────────────────────────────────────────────────

  test("logged-in user can create a listing and lands on its detail page", async ({ page }) => {
    const newListing = makeListing({
      id: 42,
      title: "Study Chair",
      status: "available",
      owner: { id: MOCK_USER.id, display_name: MOCK_USER.display_name },
    });

    await mockAuth(page, MOCK_USER);
    await mockNotifications(page);

    await page.route("**/api/listings/pricing-hint*", (route) =>
      route.fulfill({ json: { sample_size: 0, suggestion: "" } }),
    );
    await page.route("**/api/listings/42/updates*", (route) => route.fulfill({ json: [] }));
    await page.route("**/api/listings/42", (route) => route.fulfill({ json: newListing }));
    await page.route("**/api/listings", (route) => {
      if (route.request().method() === "POST") {
        return route.fulfill({
          status: 201,
          contentType: "application/json",
          body: JSON.stringify(newListing),
        });
      }
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ count: 0, next: null, previous: null, results: [] }),
      });
    });

    await page.goto("/listings/new");
    await expect(page.getByRole("button", { name: /create listing/i })).toBeVisible();

    // Labels in ListingFormPage use span text inside wrapping <label> elements
    await page.getByLabel(/item title/i).fill("Study Chair");
    await page.getByLabel(/description/i).fill("Barely used study chair, great condition.");
    await page.getByLabel(/pickup zone/i).fill("South Hall 2B");

    // "Available Until" is the actual label text for the datetime-local field
    const future = new Date(Date.now() + 86400000 * 2);
    const isoLocal = future.toISOString().slice(0, 16);
    await page.getByLabel(/available until/i).fill(isoLocal);

    const createRequest = page.waitForRequest(
      (request) => request.url().endsWith("/api/listings") && request.method() === "POST",
    );
    await page.getByRole("button", { name: /create listing/i }).click();

    const created = await createRequest;
    expect(created.headers()["x-csrftoken"]).toBe(CSRF_TOKEN);
    expect(created.postDataJSON()).toMatchObject({ title: "Study Chair", condition: "good", price_type: "free" });

    // ListingFormPage navigates to the new listing's detail page after creation
    await expect(page).toHaveURL(/\/listings\/42$/);
    await expect(page.getByRole("heading", { name: "Study Chair" })).toBeVisible();
  });

  test("server validation errors land on the fields they are about", async ({ page }) => {
    await mockAuth(page, MOCK_USER);
    await mockNotifications(page);
    await page.route("**/api/listings/pricing-hint*", (route) =>
      route.fulfill({ json: { sample_size: 0, suggestion: "" } }),
    );
    await page.route("**/api/listings", (route) =>
      route.fulfill({
        status: 400,
        json: {
          title: ["A listing with this title already exists."],
          pickup_zone: ["Pick a zone on your campus."],
        },
      }),
    );

    await page.goto("/listings/new");
    await page.getByLabel(/item title/i).fill("Study Chair");
    await page.getByLabel(/description/i).fill("Barely used study chair, great condition.");
    await page.getByLabel(/pickup zone/i).fill("South Hall 2B");
    await page.getByLabel(/available until/i).fill(new Date(Date.now() + 86400000 * 2).toISOString().slice(0, 16));
    await page.getByRole("button", { name: /create listing/i }).click();

    const title = page.getByLabel(/item title/i);
    await expect(title).toHaveAttribute("aria-invalid", "true");
    await expect(title).toBeFocused();
    await expect(page.getByText("A listing with this title already exists.")).toBeVisible();
    await expect(page.getByLabel(/pickup zone/i)).toHaveAttribute("aria-invalid", "true");
    await expect(page.getByText("Pick a zone on your campus.")).toBeVisible();
    await expect(page).toHaveURL(/\/listings\/new/);
  });

  // ── 3. Reserve flow ───────────────────────────────────────────────────────

  test("user can open a listing and submit a reservation request", async ({ page }) => {
    const listing = makeListing({ id: 42, can_reserve: true });
    const reservation = makeReservation({ id: 7, status: "requested" });

    await mockAuth(page, { ...MOCK_USER, id: 99 }); // different user so they're not the owner
    await mockNotifications(page);

    await page.route("**/api/listings/42/updates*", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
    );
    await page.route("**/api/listings/42", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(listing),
      }),
    );
    await page.route("**/api/reservations", (route) =>
      route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify(reservation),
      }),
    );

    // Wait for the listing API call to complete before asserting DOM
    const [, ] = await Promise.all([
      page.waitForResponse((res) => res.url().endsWith("/api/listings/42")),
      page.goto("/listings/42"),
    ]);
    await expect(page.getByRole("heading", { name: "Desk Lamp" })).toBeVisible();

    await page.getByPlaceholder(/tell the seller your pickup window/i).fill("Tomorrow 6–7 pm");
    await page.getByRole("button", { name: /reserve this item/i }).click();

    // Sonner toast confirms the reservation was requested
    await expect(page.getByText(/reservation requested/i)).toBeVisible();
  });

  // ── 4. Login → Dashboard redirect ────────────────────────────────────────

  test("logged-in user who visits /login sees the login page (no forced redirect)", async ({ page }) => {
    await mockAuth(page, MOCK_USER);
    await mockNotifications(page);

    await page.goto("/login");
    // /login is a public route; authenticated users see it normally (no forced redirect)
    await expect(page).toHaveURL(/\/login/);
    await expect(page.getByRole("button", { name: /sign in/i })).toBeVisible();
  });

  test("user can log in with credentials and reach a working dashboard", async ({ page }) => {
    // The session starts signed out and becomes signed in when the login POST
    // succeeds, as it would against the real API.
    let signedIn = false;
    await mockAuth(page, null);
    await mockNotifications(page);
    await page.route("**/api/push/vapid-key", (route) => route.fulfill({ json: { enabled: false, public_key: "" } }));
    await page.route("**/api/auth/me", (route) =>
      route.fulfill({ json: { user: signedIn ? MOCK_USER : null } }),
    );
    await page.route("**/api/auth/login", (route) => {
      signedIn = true;
      return route.fulfill({ json: { user: MOCK_USER } });
    });
    // A realistic payload. An empty object here crashes the dashboard into the
    // error boundary; the old spec never noticed because it only checked the URL.
    await mockDashboard(page);

    await page.goto("/login");
    await expect(page.getByRole("banner").getByRole("link", { name: "Log In" })).toBeVisible();
    await page.getByLabel("Email Address").fill("test@example.com");
    await page.getByLabel("Password").fill("StrongPass123!");
    const loginRequest = page.waitForRequest("**/api/auth/login");
    await page.getByRole("button", { name: /sign in/i }).click();

    expect((await loginRequest).headers()["x-csrftoken"]).toBe(CSRF_TOKEN);
    await expect(page).toHaveURL(/\/dashboard/);

    // Real dashboard content, rendered from the payload.
    const dashboard = makeDashboard();
    await expect(page.getByRole("heading", { name: "Hi, Test!" })).toBeVisible();
    await expect(page.getByText("You have 1 pending pickup right now.")).toBeVisible();
    await expect(page.getByText("$49", { exact: true })).toBeVisible(); // scan_savings_total "48.50", rounded
    await expect(page.getByRole("heading", { name: "Pickup coordination" })).toBeVisible();
    const pickupRow = page.getByRole("row", { name: /Desk Lamp/ });
    await expect(pickupRow).toContainText("Jordan Lee");
    await expect(pickupRow).toContainText(dashboard.action_queues.incoming_pickup_actions[0].pickup_time_window);
    await expect(pickupRow).toContainText("Requested");
    await expect(pickupRow.getByRole("link", { name: /manage handoff/i })).toHaveAttribute("href", "/handoffs/7");
    await expect(page.getByRole("heading", { name: "Clip-on Fan" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Room 204 Move-Out" })).toBeVisible();
    await expect(page.getByText("New pickup request")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Something went wrong" })).toHaveCount(0);
    // And the shell now shows the signed-in navigation.
    await expect(page.getByRole("banner").getByRole("button", { name: "Log Out" })).toBeVisible();
  });
});
