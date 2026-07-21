import { test, expect } from "@playwright/test";

import { mockAuth, mockNotifications, makeListing } from "./helpers/mock-api.js";

/**
 * The header must stay pinned while the page scrolls, and the browse
 * search bar must sit flush beneath it — no see-through seam.
 *
 * Regression: an unlayered `.nav-shell { position: relative }` once beat
 * Tailwind's layered `sticky` utility, and a wrapper div gave the header a
 * containing block exactly its own height. Both left the nav scrolling away
 * while the search bar stayed pinned 72px down, with raw cards sliding
 * through the transparent gap between them.
 */
test.describe("Sticky chrome", () => {
  test("nav stays pinned and search bar sits flush under it while scrolling", async ({ page }) => {
    await mockAuth(page);
    await mockNotifications(page);
    const listings = Array.from({ length: 12 }, (_, i) =>
      makeListing({ id: i + 1, title: `Item ${i + 1}`, image_url: null }),
    );
    await page.route("**/api/listings?*", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ count: listings.length, next: null, previous: null, results: listings }),
      }),
    );

    await page.goto("/browse");
    await expect(page.getByRole("heading", { name: "Item 1", exact: true })).toBeVisible();

    await page.mouse.wheel(0, 800);
    await page.waitForFunction(() => window.scrollY > 500);

    const { navTop, navBottom, navPosition, barTop } = await page.evaluate(() => {
      const nav = document.querySelector("header");
      const bar = document.querySelector("input[type=search]").closest(".sticky");
      const navRect = nav.getBoundingClientRect();
      return {
        navTop: navRect.top,
        navBottom: navRect.bottom,
        navPosition: getComputedStyle(nav).position,
        barTop: bar.getBoundingClientRect().top,
      };
    });

    expect(navPosition).toBe("sticky");
    expect(navTop).toBe(0);
    // Flush: the search bar's top within 1px of the nav's bottom edge.
    expect(Math.abs(barTop - navBottom)).toBeLessThanOrEqual(1);
  });
});
