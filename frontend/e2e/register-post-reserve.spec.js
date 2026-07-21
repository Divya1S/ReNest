import { test, expect } from "@playwright/test";
import {
  MOCK_USER,
  makeListing,
  makeReservation,
  mockAuth,
  mockLogin,
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

    await page.getByRole("button", { name: /create account/i }).click();

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

  test("logged-in user can create a listing and is redirected to browse", async ({ page }) => {
    const newListing = makeListing({ id: 42, title: "Study Chair", status: "published" });

    await mockAuth(page, MOCK_USER);
    await mockNotifications(page);

    // Stub CSRF and the POST to create the listing
    await page.route("**/api/auth/csrf", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: "{}" }),
    );
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

    await page.getByRole("button", { name: /create listing/i }).click();

    // ListingFormPage navigates to the new listing's detail page after creation
    await expect(page).toHaveURL(/\/listings\/42/);
  });

  // ── 3. Reserve flow ───────────────────────────────────────────────────────

  test("user can open a listing and submit a reservation request", async ({ page }) => {
    const listing = makeListing({ id: 42, can_reserve: true });
    const reservation = makeReservation({ id: 7, status: "pending" });

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
    // /login is a public route — authenticated users see it normally (no forced redirect)
    await expect(page).toHaveURL(/\/login/);
    await expect(page.getByRole("button", { name: /sign in/i })).toBeVisible();
  });

  test("user can log in with credentials and reach the dashboard", async ({ page }) => {
    // Start as logged-out, then simulate login success
    await mockAuth(page, null);
    await mockLogin(page);
    await mockNotifications(page);
    await page.route("**/api/dashboard**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({}) }),
    );
    // After login, /auth/me will return the user
    await page.route("**/api/auth/me", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ user: MOCK_USER }),
      }),
    );

    await page.goto("/login");
    await page.getByLabel("Email Address").fill("test@example.com");
    await page.getByLabel("Password").fill("StrongPass123!");
    await page.getByRole("button", { name: /sign in/i }).click();

    await expect(page).toHaveURL(/\/dashboard/);
  });
});
