import { test, expect } from "./helpers/test.js";

import { MOCK_USER, makeListing, mockAuth, mockNotifications } from "./helpers/mock-api.js";

// ---------------------------------------------------------------------------
// App shell behaviour that only shows up in a real browser: where the window
// scrolls and where focus goes on a client-side navigation, what the header
// shows while the session is still being checked, and that an error panel's
// retry button really retries.
// ---------------------------------------------------------------------------

async function mockBrowse(page) {
  const listings = Array.from({ length: 12 }, (_, i) =>
    makeListing({ id: i + 1, title: `Item ${i + 1}`, image_url: null }),
  );
  await page.route("**/api/listings?*", (route) =>
    route.fulfill({ json: { count: listings.length, next: null, previous: null, results: listings } }),
  );
  await page.route("**/api/listings/trending*", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/announcements*", (route) => route.fulfill({ json: [] }));
  for (const listing of listings) {
    await page.route(`**/api/listings/${listing.id}/updates*`, (route) => route.fulfill({ json: [] }));
    await page.route(`**/api/listings/${listing.id}`, (route) => route.fulfill({ json: listing }));
  }
  return listings;
}

test.describe("Route changes", () => {
  test("a new page starts at the top with focus on the main landmark", async ({ page }) => {
    await mockAuth(page, null);
    await mockBrowse(page);

    await page.goto("/browse");
    await expect(page.getByRole("heading", { name: "Item 12", exact: true })).toBeVisible();
    // First load: nothing has taken focus.
    expect(await page.evaluate(() => document.activeElement?.tagName)).toBe("BODY");

    const link = page
      .getByRole("article")
      .filter({ has: page.getByRole("heading", { name: "Item 12", exact: true }) })
      .getByRole("link", { name: "View Item" });
    await link.scrollIntoViewIfNeeded();
    await page.waitForFunction(() => window.scrollY > 200);

    await link.click();

    await expect(page).toHaveURL(/\/listings\/12$/);
    await expect(page.getByRole("heading", { name: "Item 12", level: 1 })).toBeVisible();
    // Back at the top. Not exactly 0 every time: the sticky header grows back
    // by 8px when the page returns to the top, and the browser's scroll
    // anchoring can compensate for part of that shift. Before scroll
    // restoration existed the window simply kept the previous page's offset.
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBeLessThanOrEqual(8);
    await expect(page.locator("#main-content")).toBeFocused();
  });

  test("typing in a filter neither scrolls the page nor moves focus", async ({ page }) => {
    await mockAuth(page, null);
    await mockBrowse(page);
    await page.goto("/browse");
    await expect(page.getByRole("heading", { name: "Item 1", exact: true })).toBeVisible();

    await page.mouse.wheel(0, 500);
    await page.waitForFunction(() => window.scrollY > 200);
    const before = await page.evaluate(() => window.scrollY);

    const building = page.getByRole("textbox", { name: "Filter by building" });
    const requests = [];
    page.on("request", (request) => {
      if (request.url().includes("/api/listings?") && request.url().includes("building=")) {
        requests.push(request.url());
      }
    });
    await building.pressSequentially("North Hall", { delay: 25 });

    await expect(page).toHaveURL(/building=North\+Hall/);
    await expect(building).toBeFocused();
    // Still where the reader was (give or take a pixel of reflow as the grid
    // reloads), not thrown back to the top by the URL update.
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(before - 50);
    // Debounced: one request for the finished word, not one per keystroke.
    expect(requests).toHaveLength(1);
    expect(requests[0]).toContain("building=North+Hall");
  });
});

test.describe("Header while the session is being checked", () => {
  test("a signed-in user never sees the guest navigation on a hard reload", async ({ page }) => {
    await mockAuth(page, MOCK_USER);
    await mockNotifications(page);
    await mockBrowse(page);
    let release;
    const gate = new Promise((resolve) => {
      release = resolve;
    });
    await page.route("**/api/auth/me", async (route) => {
      await gate;
      await route.fulfill({ json: { user: MOCK_USER } });
    });

    await page.goto("/browse");
    const banner = page.getByRole("banner");

    await expect(banner.getByRole("status")).toContainText("Checking your session");
    await expect(banner.getByRole("link", { name: "Log In" })).toHaveCount(0);
    await expect(banner.getByRole("link", { name: "Join" })).toHaveCount(0);

    release();

    await expect(banner.getByRole("button", { name: "Log Out" })).toBeVisible();
    await expect(banner.getByRole("link", { name: "Log In" })).toHaveCount(0);
    await expect(banner.getByRole("status")).toHaveCount(0);
  });
});

test.describe("Retry buttons", () => {
  for (const { name, path, api, heading } of [
    { name: "Saved items", path: "/saved", api: "**/api/saved-listings", heading: /nothing saved yet/i },
    { name: "Alerts", path: "/notifications", api: "**/api/notifications", heading: /caught up|no unread|unread now/i },
  ]) {
    test(`${name}: Try Again re-requests the page's own endpoint`, async ({ page }) => {
      let healthy = false;
      const requested = [];
      await mockAuth(page, MOCK_USER);
      await mockNotifications(page);
      page.on("request", (request) => {
        if (request.url().includes("/api/")) requested.push(request.url());
      });
      await page.route(api, (route) =>
        healthy
          ? route.fulfill({ json: { count: 0, unread_count: 0, results: [] } })
          : route.fulfill({ status: 500, json: { detail: "Server exploded." } }),
      );

      await page.goto(path);
      await expect(page.getByText("Server exploded.")).toBeVisible();

      healthy = true;
      await page.getByRole("button", { name: /try again/i }).click();

      await expect(page.getByText(heading).first()).toBeVisible();
      await expect(page.getByRole("button", { name: /try again/i })).toHaveCount(0);
      // The click event must never have been turned into a URL.
      expect(requested.filter((url) => url.includes("object"))).toEqual([]);
    });
  }
});
