import { test, expect } from "@playwright/test";
import { MOCK_USER, mockAuth, mockNotifications } from "./helpers/mock-api.js";

// ---------------------------------------------------------------------------
// Critical path 2: Scan → Publish
// ---------------------------------------------------------------------------

const SESSION_ID = "abc123";

const MOCK_SESSION = {
  id: SESSION_ID,
  name: "Room 204 Move-Out",
  pickup_zone: "North Hall lobby",
  move_out_deadline: new Date(Date.now() + 86400000 * 2).toISOString(),
  images: [
    { id: 1, image_url: null, position: "left" },
  ],
  items: [
    {
      id: 10,
      title: "Desk Lamp",
      description: "Good lamp",
      category: "lighting",
      condition: "good",
      price_type: "free",
      price_amount: null,
      estimated_retail_value: null,
      preset_key: "",
      notes: "",
      triage_status: "sell",
      publish_readiness: "ready",
      missing_fields: [],
      source_image: 1,
      hotspot_box: { x: 0.2, y: 0.2, width: 0.3, height: 0.3 },
      status: "draft",
    },
  ],
};

const MOCK_QUEUE = {
  session: { id: SESSION_ID, name: "Room 204 Move-Out" },
  items: [
    {
      id: 10,
      title: "Desk Lamp",
      description: "Good lamp",
      category: "lighting",
      condition: "good",
      price_type: "free",
      price_amount: null,
      triage_status: "sell",
      publish_readiness: "ready",
      missing_fields: [],
      source_image_detail: null,
      status: "draft",
    },
  ],
  summary: { total: 1, ready: 1, needs_info: 0, skip: 0 },
};

test.describe("Scan → Publish", () => {
  test("publish queue loads and shows ready draft count", async ({ page }) => {
    await mockAuth(page, MOCK_USER);
    await mockNotifications(page);

    await page.route(`**/api/scan-sessions/${SESSION_ID}/publish-queue`, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_QUEUE),
      }),
    );
    await page.route("**/api/publish-presets", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
    );

    await page.goto(`/publish/${SESSION_ID}`);

    await expect(page.getByRole("heading", { name: "Room 204 Move-Out" })).toBeVisible();
    await expect(page.getByText("Desk Lamp")).toBeVisible();
  });

  test("clicking Publish Ready Items sends the publish request and shows success", async ({ page }) => {
    await mockAuth(page, MOCK_USER);
    await mockNotifications(page);

    await page.route(`**/api/scan-sessions/${SESSION_ID}/publish-queue`, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_QUEUE),
      }),
    );
    await page.route("**/api/publish-presets", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
    );

    const publishedQueue = {
      ...MOCK_QUEUE,
      items: [{ ...MOCK_QUEUE.items[0], status: "published" }],
    };
    await page.route(`**/api/scan-sessions/${SESSION_ID}/publish-selected`, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ published_count: 1, queue: publishedQueue }),
      }),
    );

    await page.goto(`/publish/${SESSION_ID}`);
    await expect(page.getByText("Desk Lamp")).toBeVisible();

    await page.getByRole("button", { name: /publish ready items/i }).click();

    await expect(page.getByText(/1 draft.* published/i)).toBeVisible();
  });

  test("scan studio loads session with image and draft item", async ({ page }) => {
    await mockAuth(page, MOCK_USER);
    await mockNotifications(page);

    await page.route(`**/api/scan-sessions/${SESSION_ID}`, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_SESSION),
      }),
    );
    await page.route("**/api/publish-presets", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
    );

    const [, ] = await Promise.all([
      page.waitForResponse((res) => res.url().includes(`/api/scan-sessions/${SESSION_ID}`) && !res.url().includes("/"+ SESSION_ID +"/")),
      page.goto(`/scan/${SESSION_ID}`),
    ]);

    // The page header shows the session name once loaded
    await expect(page.getByRole("heading", { name: "Room 204 Move-Out" })).toBeVisible();
  });

  test("publish queue shows error state when API fails", async ({ page }) => {
    await mockAuth(page, MOCK_USER);
    await mockNotifications(page);

    await page.route(`**/api/scan-sessions/${SESSION_ID}/publish-queue`, (route) =>
      route.fulfill({ status: 500, contentType: "application/json", body: '{"detail":"Server error"}' }),
    );
    await page.route("**/api/publish-presets", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
    );

    await page.goto(`/publish/${SESSION_ID}`);

    await expect(page.getByText(/server error|publish queue not found/i)).toBeVisible();
  });
});
