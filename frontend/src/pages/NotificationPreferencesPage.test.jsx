import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { clearApiCache } from "../lib/apiCache";

import NotificationPreferencesPage from "./NotificationPreferencesPage";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const PREFS = [
  { notification_type: "reservation", channel: "both", quiet_hours_start: null, quiet_hours_end: null },
  { notification_type: "handoff_urgent", channel: "push", quiet_hours_start: "23:00", quiet_hours_end: "08:00" },
  { notification_type: "ai_match", channel: "off", quiet_hours_start: null, quiet_hours_end: null },
];

function mockOk(data) {
  return { ok: true, status: 200, json: async () => data };
}
function mockErr() {
  return { ok: false, status: 500, json: async () => ({ detail: "Server error" }) };
}

function renderPage() {
  render(<NotificationPreferencesPage />);
}

describe("NotificationPreferencesPage", () => {
  beforeEach(() => clearApiCache());
  afterEach(() => vi.restoreAllMocks());

  test("shows loading skeletons while fetch is in-flight", () => {
    vi.spyOn(global, "fetch").mockReturnValue(new Promise(() => {}));
    renderPage();
    expect(document.querySelectorAll(".animate-pulse").length).toBeGreaterThan(0);
  });

  test("renders a row for each notification type with human-readable labels", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(mockOk({ preferences: PREFS }));
    renderPage();
    await waitFor(() => {
      expect(screen.getByText("Reservation confirmed")).toBeInTheDocument();
      expect(screen.getByText("Handoff reminders")).toBeInTheDocument();
      expect(screen.getByText("Match alerts")).toBeInTheDocument();
    });
  });

  test("shows error panel with retry button when fetch fails", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(mockErr());
    renderPage();
    await waitFor(() => {
      expect(screen.getByText(/couldn't load preferences/i)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
    });
  });

  test("retry button re-fetches successfully", async () => {
    vi.spyOn(global, "fetch")
      .mockResolvedValueOnce(mockErr())
      .mockResolvedValue(mockOk({ preferences: PREFS }));
    renderPage();

    await waitFor(() => screen.getByRole("button", { name: /try again/i }));
    fireEvent.click(screen.getByRole("button", { name: /try again/i }));

    await waitFor(() => expect(screen.getByText("Reservation confirmed")).toBeInTheDocument());
  });

  test("quiet hours inputs are hidden when channel is 'off'", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(mockOk({ preferences: PREFS }));
    renderPage();
    await waitFor(() => screen.getByText("Match alerts"));

    // "off" channel pref should not render quiet hours
    const timeInputs = document.querySelectorAll("input[type='time']");
    // reservation (both) and handoff_urgent (push) show quiet hours — ai_match (off) does not
    // 2 prefs × 2 inputs (start + end) = 4 total
    expect(timeInputs.length).toBe(4);
  });

  test("clicking a channel button updates the aria-pressed state", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(mockOk({ preferences: PREFS }));
    renderPage();
    await waitFor(() => screen.getByText("Reservation confirmed"));

    // For the "reservation" row, current channel is "both" — switch to "Push only"
    const pushButtons = screen.getAllByRole("button", { name: "Push only" });
    fireEvent.click(pushButtons[0]);

    await waitFor(() =>
      expect(pushButtons[0]).toHaveAttribute("aria-pressed", "true")
    );
  });

  test("save button sends PATCH with the current preferences array", async () => {
    const fetchSpy = vi.spyOn(global, "fetch")
      .mockResolvedValueOnce(mockOk({ preferences: PREFS }))
      .mockResolvedValue(mockOk({ preferences: PREFS })); // PATCH response

    renderPage();
    await waitFor(() => screen.getByText("Reservation confirmed"));

    fireEvent.click(screen.getByRole("button", { name: /save preferences/i }));

    await waitFor(() => {
      const patchCall = fetchSpy.mock.calls.find(
        ([url, opts]) => url.includes("/notifications/preferences") && opts?.method === "PATCH",
      );
      expect(patchCall).toBeTruthy();
      const body = JSON.parse(patchCall[1].body);
      expect(body.preferences).toHaveLength(3);
    });
  });

  test("save button is not rendered when preferences failed to load", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(mockErr());
    renderPage();
    await waitFor(() => screen.getByText(/couldn't load preferences/i));
    expect(screen.queryByRole("button", { name: /save preferences/i })).not.toBeInTheDocument();
  });

  test("save button shows 'Saved' confirmation briefly after a successful PATCH", async () => {
    vi.spyOn(global, "fetch")
      .mockResolvedValueOnce(mockOk({ preferences: PREFS }))
      .mockResolvedValue(mockOk({}));
    renderPage();
    await waitFor(() => screen.getByText("Reservation confirmed"));

    fireEvent.click(screen.getByRole("button", { name: /save preferences/i }));

    await waitFor(() => expect(screen.getByText("Saved")).toBeInTheDocument());
  });
});
