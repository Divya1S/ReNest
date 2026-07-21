import { test, expect } from "@playwright/test";

import { MOCK_USER, mockAuth, mockNotifications } from "./helpers/mock-api.js";

// ---------------------------------------------------------------------------
// Nest Concierge: widget + full-page assistant against a mocked API.
// The streaming endpoint is mocked as unavailable so the client exercises its
// JSON fallback — the contract path.
// ---------------------------------------------------------------------------

const HISTORY_EMPTY = { enabled: true, has_more: false, messages: [] };

const CHAT_REPLY = {
  reply: "Found a Clip-on lamp in Maple Hall lobby.",
  used_tools: ["search_listings", "show_listing_cards", "suggest_followups"],
  degraded: false,
  message_id: 41,
  meta: {
    route: { name: "task" },
    plan: { steps: [{ tool: "search_listings", why: "find lamps" }] },
    reflection: { passed: true },
    revised: false,
    cached: false,
    ui_blocks: [
      {
        type: "listing_cards",
        props: {
          listings: [
            {
              id: 7,
              title: "Clip-on lamp",
              price_type: "free",
              pickup_zone: "Maple Hall lobby",
              thumb: null,
            },
          ],
        },
      },
      { type: "suggestions", props: { suggestions: [{ label: "Reserve it?", path: "/listings/7" }] } },
    ],
  },
};

async function mockConcierge(page) {
  await page.route("**/api/concierge/history/**", (route) =>
    route.fulfill({ json: HISTORY_EMPTY }),
  );
  await page.route("**/api/concierge/chat/stream/", (route) =>
    route.fulfill({ status: 503, json: { detail: "stream off in e2e" } }),
  );
  await page.route("**/api/concierge/chat/", (route) => route.fulfill({ json: CHAT_REPLY }));
}

test.describe("Nest Concierge", () => {
  test("widget answers with generative listing cards and action chips", async ({ page }) => {
    await mockAuth(page, MOCK_USER);
    await mockNotifications(page);
    await mockConcierge(page);

    await page.goto("/dashboard");
    await page.getByRole("button", { name: /open nest concierge/i }).click();
    await expect(page.getByText(/i can search live listings/i)).toBeVisible();

    await page.getByLabel(/message the concierge/i).fill("any lamps?");
    await page.getByRole("button", { name: /send message/i }).click();

    const dialog = page.getByRole("dialog", { name: /nest concierge/i });
    await expect(dialog.getByText("Found a Clip-on lamp in Maple Hall lobby.")).toBeVisible();
    // Server-hydrated generative card links to the real listing…
    await expect(dialog.getByRole("link", { name: /clip-on lamp/i })).toHaveAttribute(
      "href",
      "/listings/7",
    );
    // …the plan/reflexion caption is shown…
    await expect(dialog.getByText(/planned 1 step · self-checked/i)).toBeVisible();
    // …and the action chip deep-links into the app.
    await expect(dialog.getByRole("link", { name: /reserve it\?/i })).toHaveAttribute(
      "href",
      "/listings/7",
    );
  });

  test("full-page assistant shares the same thread and the widget hides itself", async ({ page }) => {
    await mockAuth(page, MOCK_USER);
    await mockNotifications(page);
    await page.route("**/api/concierge/history/**", (route) =>
      route.fulfill({
        json: {
          enabled: true,
          has_more: false,
          messages: [
            { id: 1, role: "user", content: "any lamps?", used_tools: [], meta: {} },
            {
              id: 2,
              role: "assistant",
              content: "Found a Clip-on lamp in Maple Hall lobby.",
              used_tools: [],
              meta: {},
            },
          ],
        },
      }),
    );

    await page.goto("/assistant");
    await expect(page.getByRole("heading", { name: /your move-out assistant/i })).toBeVisible();
    await expect(page.getByText("Found a Clip-on lamp in Maple Hall lobby.")).toBeVisible();
    // One surface at a time: no floating launcher on the assistant page.
    await expect(page.getByRole("button", { name: /open nest concierge/i })).toHaveCount(0);
  });

  test("concierge outage degrades to a calm inline reply, app shell unaffected", async ({ page }) => {
    await mockAuth(page, MOCK_USER);
    await mockNotifications(page);
    await page.route("**/api/concierge/history/**", (route) =>
      route.fulfill({ json: HISTORY_EMPTY }),
    );
    await page.route("**/api/concierge/chat/stream/", (route) => route.abort());
    await page.route("**/api/concierge/chat/", (route) => route.abort());

    await page.goto("/dashboard");
    await page.getByRole("button", { name: /open nest concierge/i }).click();
    await page.getByLabel(/message the concierge/i).fill("hello?");
    await page.getByRole("button", { name: /send message/i }).click();

    await expect(page.getByText(/couldn't get a reply just now/i)).toBeVisible();
    // The shell is alive: navigation still works.
    await expect(page.getByRole("navigation").first()).toBeVisible();
  });
});
