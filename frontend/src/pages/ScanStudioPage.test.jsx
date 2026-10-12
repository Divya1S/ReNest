import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, describe, expect, test, vi } from "vitest";

import ScanStudioPage from "./ScanStudioPage";

const DRAFT = {
  id: 11,
  title: "Desk Lamp",
  category: "lighting",
  condition: "good",
  price_type: "free",
  price_amount: "0.00",
  estimated_retail_value: "0.00",
  preset_key: "",
  triage_status: "review",
  notes: "",
  source_image: 1,
  hotspot_box: { x: 0.1, y: 0.1, width: 0.2, height: 0.2 },
  publish_readiness: "missing_info",
  missing_fields: ["price"],
  suggestion_pack: null,
};

const SESSION = {
  id: 9,
  name: "Dorm scan",
  status: "in_progress",
  room_type: "dorm_room",
  room_label: "North 204",
  pickup_zone: "Lobby",
  move_out_deadline: new Date(Date.now() + 5 * 86400000).toISOString(),
  images: [{ id: 1, image_url: "/media/room.jpg", position: 0 }],
  items: [DRAFT],
  summary: { total_items: 1, ready_to_publish_count: 0, missing_info_count: 1 },
  progress_percent: 0,
  task_summary: { due_today_tasks: 0 },
};

function jsonResponse(body, status = 200) {
  return Promise.resolve({ ok: status < 400, status, json: async () => body });
}

function mockApi(overrides = {}) {
  return vi.spyOn(global, "fetch").mockImplementation((url, opts = {}) => {
    const method = (opts.method || "GET").toUpperCase();
    const key = `${method} ${url}`;
    for (const [pattern, body] of Object.entries(overrides)) {
      if (key.includes(pattern)) {
        return jsonResponse(typeof body === "function" ? body(opts) : body);
      }
    }
    if (key.includes("GET /api/publish-presets")) return jsonResponse([]);
    if (key.includes("GET /api/scan-sessions/9")) return jsonResponse(SESSION);
    return jsonResponse({});
  });
}

function renderStudio() {
  const router = createMemoryRouter(
    [{ path: "/scan/:sessionId", element: <ScanStudioPage /> }],
    { initialEntries: ["/scan/9"] },
  );
  render(<RouterProvider router={router} />);
  return router;
}

describe("ScanStudioPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  test("loads the session and shows the draft inspector", async () => {
    mockApi();
    renderStudio();

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Dorm scan" })).toBeInTheDocument(),
    );
    expect(screen.getByDisplayValue("Desk Lamp")).toBeInTheDocument();
    expect(screen.getByText(/1 drafts/)).toBeInTheDocument();
  });

  test("pressing 's' sets triage to sell and ⌘+Enter saves the draft", async () => {
    const spy = mockApi({
      "PATCH /api/scan-items/11": (opts) => JSON.parse(opts.body),
    });
    renderStudio();

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Dorm scan" })).toBeInTheDocument(),
    );

    fireEvent.keyDown(window, { key: "s" });
    fireEvent.keyDown(window, { key: "Enter", metaKey: true });

    await waitFor(() => {
      const patchCall = spy.mock.calls.find(
        ([url, opts]) => String(url).includes("/api/scan-items/11") && opts?.method === "PATCH",
      );
      expect(patchCall).toBeDefined();
      expect(JSON.parse(patchCall[1].body)).toMatchObject({ triage_status: "sell" });
    });
  });

  test("triage keys are ignored while typing in the inspector inputs", async () => {
    mockApi();
    renderStudio();

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Dorm scan" })).toBeInTheDocument(),
    );

    const titleInput = screen.getByDisplayValue("Desk Lamp");
    fireEvent.keyDown(titleInput, { key: "s" });
    // Typing "s" in an input must not hijack triage; the title stays editable
    fireEvent.change(titleInput, { target: { value: "Desk Lamps" } });
    expect(screen.getByDisplayValue("Desk Lamps")).toBeInTheDocument();
  });

  test("delete uses the styled confirm dialog, not window.confirm", async () => {
    const spy = mockApi({
      "DELETE /api/scan-items/11": {},
    });
    renderStudio();

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Dorm scan" })).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() =>
      expect(screen.getByText("Delete this draft item?")).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByRole("button", { name: /delete draft/i }));
    await waitFor(() => {
      const deleteCall = spy.mock.calls.find(
        ([url, opts]) => String(url).includes("/api/scan-items/11") && opts?.method === "DELETE",
      );
      expect(deleteCall).toBeDefined();
    });
  });

  test("thumbnail strip renders each room photo from image_url", async () => {
    // Regression: the strip read image.url, which the API never sends, so
    // every thumbnail fell back to the placeholder icon.
    mockApi();
    renderStudio();

    const thumbnail = await screen.findByRole("button", { name: /room photo 0, 1 tagged item/i });
    const img = thumbnail.querySelector("img");
    expect(img).not.toBeNull();
    expect(img).toHaveAttribute("src", "/media/room.jpg");
    expect(thumbnail).toHaveAttribute("aria-pressed", "true");
  });

  test("the inspector and scan settings fields have real labels", async () => {
    mockApi();
    renderStudio();
    await screen.findByRole("heading", { name: "Dorm scan" });

    expect(screen.getByLabelText("Item title")).toHaveValue("Desk Lamp");
    expect(screen.getByLabelText("Category")).toHaveValue("lighting");
    expect(screen.getByLabelText("Condition")).toHaveValue("good");
    expect(screen.getByLabelText("Pricing")).toHaveValue("free");
    expect(screen.getByLabelText("Price ($)")).toBeDisabled();
    expect(screen.getByLabelText("Estimated retail value ($)")).toBeInTheDocument();
    expect(screen.getByLabelText("Notes")).toBeInTheDocument();
    expect(screen.getByLabelText("Pickup zone")).toHaveValue("Lobby");
    expect(screen.getByLabelText("Move-out deadline")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /sell/i })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("button", { name: /^review$/i })).toHaveAttribute("aria-pressed", "true");
  });

  describe("with two drafts", () => {
    const FRIDGE = { ...DRAFT, id: 12, title: "Mini Fridge", category: "comfort", hotspot_box: { x: 0.5, y: 0.5, width: 0.2, height: 0.2 } };
    const TWO = { ...SESSION, items: [DRAFT, FRIDGE], summary: { ...SESSION.summary, total_items: 2 } };

    function mockTwoDrafts(overrides = {}) {
      return mockApi({ ...overrides, "GET /api/scan-sessions/9": TWO });
    }

    test("']' switches drafts directly while the inspector is clean", async () => {
      mockTwoDrafts();
      renderStudio();
      await screen.findByRole("heading", { name: "Dorm scan" });

      fireEvent.keyDown(window, { key: "]" });

      await waitFor(() => expect(screen.getByLabelText("Item title")).toHaveValue("Mini Fridge"));
      expect(screen.queryByText("Switch rescue draft without saving?")).not.toBeInTheDocument();
    });

    test("']' and '[' after an edit ask before discarding it", async () => {
      // Regression: the keydown listener captured the render it was
      // registered in, where the inspector was still clean, so the shortcut
      // switched drafts with no prompt and the edit was lost.
      mockTwoDrafts();
      renderStudio();
      await screen.findByRole("heading", { name: "Dorm scan" });

      fireEvent.change(screen.getByLabelText("Item title"), { target: { value: "Desk Lamp (brass)" } });
      fireEvent.keyDown(window, { key: "]" });

      expect(await screen.findByText("Switch rescue draft without saving?")).toBeInTheDocument();
      expect(screen.getByLabelText("Item title")).toHaveValue("Desk Lamp (brass)");

      fireEvent.click(screen.getByRole("button", { name: /keep editing/i }));
      await waitFor(() =>
        expect(screen.queryByText("Switch rescue draft without saving?")).not.toBeInTheDocument(),
      );
      expect(screen.getByLabelText("Item title")).toHaveValue("Desk Lamp (brass)");

      fireEvent.keyDown(window, { key: "[" });
      expect(await screen.findByText("Switch rescue draft without saving?")).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: /switch draft/i }));
      await waitFor(() => expect(screen.getByLabelText("Item title")).toHaveValue("Mini Fridge"));
    });

    test("a triage shortcut counts as an edit for the next-draft shortcut", async () => {
      mockTwoDrafts();
      renderStudio();
      await screen.findByRole("heading", { name: "Dorm scan" });

      fireEvent.keyDown(window, { key: "s" });
      fireEvent.keyDown(window, { key: "]" });

      expect(await screen.findByText("Switch rescue draft without saving?")).toBeInTheDocument();
      expect(screen.getByLabelText("Item title")).toHaveValue("Desk Lamp");
    });

    test("page shortcuts are ignored while the unsaved-changes dialog is open", async () => {
      mockTwoDrafts();
      renderStudio();
      await screen.findByRole("heading", { name: "Dorm scan" });

      fireEvent.keyDown(window, { key: "s" });
      fireEvent.keyDown(window, { key: "]" });
      await screen.findByText("Switch rescue draft without saving?");

      fireEvent.keyDown(window, { key: "t" });
      fireEvent.click(screen.getByRole("button", { name: /keep editing/i }));

      // Still "sell" from before the dialog, not "toss".
      await waitFor(() =>
        expect(screen.getByRole("button", { name: /sell/i })).toHaveAttribute("aria-pressed", "true"),
      );
      expect(screen.getByRole("button", { name: /toss/i })).toHaveAttribute("aria-pressed", "false");
    });

    test("Confirm & Next saves the edit and moves on without an unsaved-changes prompt", async () => {
      const spy = mockTwoDrafts({ "PATCH /api/scan-items/11": (opts) => JSON.parse(opts.body) });
      renderStudio();
      await screen.findByRole("heading", { name: "Dorm scan" });

      fireEvent.change(screen.getByLabelText("Item title"), { target: { value: "Desk Lamp (brass)" } });
      fireEvent.click(screen.getByRole("button", { name: /confirm & next item/i }));

      await waitFor(() => expect(screen.getByLabelText("Item title")).toHaveValue("Mini Fridge"));
      expect(screen.queryByText("Switch rescue draft without saving?")).not.toBeInTheDocument();
      const patch = spy.mock.calls.find(
        ([url, opts]) => String(url).includes("/api/scan-items/11") && opts?.method === "PATCH",
      );
      expect(JSON.parse(patch[1].body)).toMatchObject({ title: "Desk Lamp (brass)" });
    });

    test("Confirm & Next stays on the draft when the save fails", async () => {
      vi.spyOn(global, "fetch").mockImplementation((url, opts = {}) => {
        const method = (opts.method || "GET").toUpperCase();
        if (method === "PATCH") return jsonResponse({ title: ["Too long."] }, 400);
        if (String(url).includes("/api/publish-presets")) return jsonResponse([]);
        return jsonResponse(TWO);
      });
      renderStudio();
      await screen.findByRole("heading", { name: "Dorm scan" });

      fireEvent.change(screen.getByLabelText("Item title"), { target: { value: "Desk Lamp (brass)" } });
      fireEvent.click(screen.getByRole("button", { name: /confirm & next item/i }));

      await waitFor(() =>
        expect(screen.getByRole("button", { name: /confirm & next item/i })).not.toBeDisabled(),
      );
      expect(screen.getByLabelText("Item title")).toHaveValue("Desk Lamp (brass)");
    });
  });

  describe("hotspot edits and unsaved inspector changes", () => {
    test("adding a box while the inspector is dirty asks before switching to the new draft", async () => {
      const created = { ...DRAFT, id: 13, title: "New item", hotspot_box: { x: 0.41, y: 0.41, width: 0.18, height: 0.18 } };
      let posted = false;
      vi.spyOn(global, "fetch").mockImplementation((url, opts = {}) => {
        const method = (opts.method || "GET").toUpperCase();
        if (method === "POST" && String(url).includes("/api/scan-sessions/9/items")) {
          posted = true;
          return jsonResponse(created, 201);
        }
        if (String(url).includes("/api/publish-presets")) return jsonResponse([]);
        return jsonResponse(posted ? { ...SESSION, items: [DRAFT, created] } : SESSION);
      });
      renderStudio();
      await screen.findByRole("heading", { name: "Dorm scan" });

      fireEvent.change(screen.getByLabelText("Item title"), { target: { value: "Desk Lamp (brass)" } });
      fireEvent.click(screen.getByRole("button", { name: "Add box" }));

      expect(await screen.findByText("Switch rescue draft without saving?")).toBeInTheDocument();
      // The new box is on the stage, and the edit is still in the inspector.
      expect(screen.getByRole("button", { name: "Hotspot: New item" })).toBeInTheDocument();
      expect(screen.getByLabelText("Item title")).toHaveValue("Desk Lamp (brass)");
    });

    test("nudging a box from the keyboard saves its geometry and keeps unsaved inspector edits", async () => {
      const spy = mockApi({ "PATCH /api/scan-items/11": (opts) => JSON.parse(opts.body) });
      renderStudio();
      await screen.findByRole("heading", { name: "Dorm scan" });

      fireEvent.change(screen.getByLabelText("Item title"), { target: { value: "Desk Lamp (brass)" } });
      const hotspot = screen.getByRole("button", { name: "Hotspot: Desk Lamp" });
      fireEvent.keyDown(hotspot, { key: "ArrowRight" });
      fireEvent.blur(hotspot);

      await waitFor(() => {
        const patch = spy.mock.calls.find(
          ([url, opts]) => String(url).includes("/api/scan-items/11") && opts?.method === "PATCH",
        );
        expect(patch).toBeDefined();
        expect(JSON.parse(patch[1].body)).toEqual({ hotspot_box: { x: 0.11, y: 0.1, width: 0.2, height: 0.2 } });
      });
      // The session reload after the move must not reset the form.
      await waitFor(() => expect(spy.mock.calls.filter(([url]) => String(url).endsWith("/api/scan-sessions/9")).length).toBeGreaterThanOrEqual(2));
      expect(screen.getByLabelText("Item title")).toHaveValue("Desk Lamp (brass)");
    });
  });

  describe("keyboard shortcuts panel", () => {
    test("opens as a labelled modal dialog, traps focus and restores it on Escape", async () => {
      mockApi();
      renderStudio();
      await screen.findByRole("heading", { name: "Dorm scan" });

      const trigger = screen.getByRole("button", { name: /shortcuts/i });
      trigger.focus();
      fireEvent.click(trigger);

      const dialog = await screen.findByRole("dialog", { name: /keyboard shortcuts/i });
      expect(dialog).toHaveAttribute("aria-modal", "true");
      const close = screen.getByRole("button", { name: /close keyboard shortcuts/i });
      expect(close).toHaveFocus();

      // The only focusable control: Tab and Shift+Tab stay on it.
      fireEvent.keyDown(document, { key: "Tab" });
      expect(close).toHaveFocus();
      fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
      expect(close).toHaveFocus();

      fireEvent.keyDown(document, { key: "Escape" });
      await waitFor(() => expect(screen.queryByRole("dialog", { name: /keyboard shortcuts/i })).not.toBeInTheDocument());
      expect(trigger).toHaveFocus();
    });

    test("'?' toggles the panel and triage keys do nothing while it is open", async () => {
      mockApi();
      renderStudio();
      await screen.findByRole("heading", { name: "Dorm scan" });

      fireEvent.keyDown(window, { key: "?" });
      await screen.findByRole("dialog", { name: /keyboard shortcuts/i });

      fireEvent.keyDown(window, { key: "s" });
      fireEvent.keyDown(window, { key: "?" });

      await waitFor(() => expect(screen.queryByRole("dialog", { name: /keyboard shortcuts/i })).not.toBeInTheDocument());
      expect(screen.getByRole("button", { name: /sell/i })).toHaveAttribute("aria-pressed", "false");
      expect(screen.queryByText("Unsaved changes")).not.toBeInTheDocument();
    });
  });
});
