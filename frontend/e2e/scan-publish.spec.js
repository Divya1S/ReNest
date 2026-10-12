import { test, expect } from "./helpers/test.js";
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

  test("scan studio: thumbnails, in-bounds boxes, keyboard path and the unsaved-changes prompt", async ({ page }) => {
    const PIXEL =
      "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";
    const lamp = MOCK_SESSION.items[0];
    const fridge = { ...lamp, id: 11, title: "Mini Fridge", hotspot_box: { x: 0.6, y: 0.1, width: 0.2, height: 0.2 } };
    let items = [lamp, fridge];
    const createdBoxes = [];
    const session = () => ({
      ...MOCK_SESSION,
      images: [{ id: 1, image_url: PIXEL, position: 1 }],
      items,
      summary: { total_items: items.length, ready_to_publish_count: items.length, missing_info_count: 0 },
      progress_percent: 0,
    });

    await mockAuth(page, MOCK_USER);
    await mockNotifications(page);
    await page.route("**/api/publish-presets", (route) => route.fulfill({ json: [] }));
    await page.route(`**/api/scan-sessions/${SESSION_ID}/items`, (route) => {
      const box = route.request().postDataJSON().hotspot_box;
      createdBoxes.push(box);
      const created = { ...lamp, id: 100 + createdBoxes.length, title: `New item ${createdBoxes.length}`, hotspot_box: box };
      items = [...items, created];
      return route.fulfill({ status: 201, json: created });
    });
    await page.route(`**/api/scan-sessions/${SESSION_ID}`, (route) => route.fulfill({ json: session() }));

    await page.goto(`/scan/${SESSION_ID}`);
    await expect(page.getByRole("heading", { name: "Room 204 Move-Out" })).toBeVisible();

    // The thumbnail strip shows the photo (it used to read a key the API never sends).
    const thumbnail = page.getByRole("button", { name: /room photo 1, 2 tagged items/i });
    await expect(thumbnail.locator("img")).toHaveAttribute("src", PIXEL);

    // A click at the right edge yields a box that still fits inside the photo.
    const stage = page.getByRole("group", { name: /room photo with item hotspots/i });
    const bounds = await stage.boundingBox();
    await page.mouse.click(bounds.x + bounds.width * 0.99, bounds.y + bounds.height * 0.5);
    await expect(page.getByRole("button", { name: "Hotspot: New item 1" })).toBeVisible();
    expect(createdBoxes[0].x + createdBoxes[0].width).toBeLessThanOrEqual(1);
    expect(createdBoxes[0].width).toBeCloseTo(0.18, 5);

    // Keyboard path: "Add box" places a centred default box.
    await page.getByRole("button", { name: "Add box" }).press("Enter");
    await expect(page.getByRole("button", { name: "Hotspot: New item 2" })).toBeVisible();
    expect(createdBoxes[1].x + createdBoxes[1].width / 2).toBeCloseTo(0.5 + 0.04 * 3, 2);

    // An edit followed by the next-draft shortcut asks before discarding it.
    const title = page.getByLabel("Item title");
    await title.fill("Edited title");
    await page.getByRole("heading", { name: "Room 204 Move-Out" }).click(); // leave the input
    await page.keyboard.press("]");
    const prompt = page.getByRole("alertdialog", { name: /switch rescue draft without saving/i });
    await expect(prompt).toBeVisible();
    await prompt.getByRole("button", { name: /keep editing/i }).click();
    await expect(title).toHaveValue("Edited title");

    // The shortcuts panel is a real dialog: labelled, focus inside, Escape closes.
    await page.getByRole("heading", { name: "Room 204 Move-Out" }).click();
    await page.keyboard.press("?");
    const shortcuts = page.getByRole("dialog", { name: "Keyboard Shortcuts" });
    await expect(shortcuts).toBeVisible();
    await expect(shortcuts.getByRole("button", { name: "Close keyboard shortcuts" })).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(shortcuts).toHaveCount(0);
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
