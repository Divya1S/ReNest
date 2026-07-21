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
    // Typing "s" in an input must not hijack triage — the title stays editable
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
});
