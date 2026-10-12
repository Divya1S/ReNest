import { test as base, expect } from "@playwright/test";

/**
 * The `test` every spec imports.
 *
 * It adds one automatic check: a spec fails if a React error boundary caught a
 * render crash while it ran. A page that throws while rendering does not
 * change the URL, so a spec that only asserts navigation ("we reached
 * /dashboard") passes while the user is looking at "Something went wrong".
 * That is exactly how the login spec stayed green over a crashing dashboard.
 *
 * Two signals, because either can be missed on its own:
 *   - the boundary's console.error ("[ErrorBoundary] ..."), which also covers
 *     boundaries that render nothing (the concierge widget) and crashes the
 *     test has since navigated away from;
 *   - the fallback itself still being on screen when the test ends.
 */
export const test = base.extend({
  errorBoundaryGuard: [
    async ({ page }, use) => {
      const crashes = [];
      page.on("console", (message) => {
        if (message.type() === "error" && message.text().includes("[ErrorBoundary]")) {
          crashes.push(message.text());
        }
      });

      await use();

      // A test that ends on a URL assertion can finish before the lazy route
      // chunk has rendered. Give that render a moment so a crash in it is seen.
      if (!page.isClosed()) {
        await page
          .getByRole("status", { name: "Loading page" })
          .waitFor({ state: "detached", timeout: 3000 })
          .catch(() => {});
      }

      expect(crashes, "a React error boundary caught a render crash during this test").toEqual([]);
      if (!page.isClosed()) {
        await expect(
          page.getByRole("heading", { name: "Something went wrong" }),
          "the ErrorBoundary fallback is on screen",
        ).toHaveCount(0);
      }
    },
    { auto: true },
  ],
});

export { expect };
