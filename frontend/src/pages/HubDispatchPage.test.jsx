import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { clearApiCache } from "../lib/apiCache";

import HubDispatchPage from "./HubDispatchPage";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const HUB = { id: 7, name: "Main Hall Hub" };

const EVENT_SCHEDULED = {
  id: 101,
  title: "Morning Collection Run",
  status: "scheduled",
  scheduled_at: new Date(Date.now() + 3_600_000).toISOString(),
  holds_count: 4,
};
const EVENT_COMPLETED = { ...EVENT_SCHEDULED, id: 102, title: "Evening Run", status: "completed", holds_count: 2 };

const ROUTE = { stop_count: 2, stops: [{ building: "North Hall", items: [{ hold_id: 1, listing_title: "Lamp", pickup_zone: "Room 101" }] }] };

function mockOk(data) {
  return { ok: true, status: 200, json: async () => data };
}
function mockErr() {
  return { ok: false, status: 500, json: async () => ({ detail: "Server error" }) };
}

function renderPage() {
  render(<HubDispatchPage />);
}

describe("HubDispatchPage", () => {
  beforeEach(() => clearApiCache());
  afterEach(() => vi.restoreAllMocks());

  test("shows loading skeletons after the hub resolves but before events load", async () => {
    let resolveEvents;
    vi.spyOn(global, "fetch")
      .mockResolvedValueOnce(mockOk([HUB]))
      .mockReturnValueOnce(new Promise((r) => { resolveEvents = r; }));

    renderPage();
    // After hub resolves, events are still pending → skeletons visible
    await waitFor(() =>
      expect(document.querySelectorAll(".animate-pulse").length).toBeGreaterThan(0)
    );
    resolveEvents(mockOk([EVENT_SCHEDULED]));
  });

  test("renders collection events once both dependent fetches resolve", async () => {
    vi.spyOn(global, "fetch")
      .mockResolvedValueOnce(mockOk([HUB]))
      .mockResolvedValueOnce(mockOk([EVENT_SCHEDULED, EVENT_COMPLETED]));

    renderPage();
    await waitFor(() => {
      expect(screen.getByText("Morning Collection Run")).toBeInTheDocument();
      expect(screen.getByText("Evening Run")).toBeInTheDocument();
    });
  });

  test("shows empty state when the hub has no events", async () => {
    vi.spyOn(global, "fetch")
      .mockResolvedValueOnce(mockOk([HUB]))
      .mockResolvedValueOnce(mockOk([]));

    renderPage();
    await waitFor(() =>
      expect(screen.getByText(/no collection runs yet/i)).toBeInTheDocument()
    );
  });

  test("shows error panel with retry when events fetch fails", async () => {
    vi.spyOn(global, "fetch")
      .mockResolvedValueOnce(mockOk([HUB]))
      .mockResolvedValueOnce(mockErr());

    renderPage();
    await waitFor(() => {
      expect(screen.getByText(/server error/i)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
    });
  });

  test("selecting an event loads and renders its route stops", async () => {
    vi.spyOn(global, "fetch")
      .mockResolvedValueOnce(mockOk([HUB]))
      .mockResolvedValueOnce(mockOk([EVENT_SCHEDULED]))
      .mockResolvedValueOnce(mockOk(ROUTE)); // route fetch

    renderPage();
    await waitFor(() => screen.getByText("Morning Collection Run"));

    fireEvent.click(screen.getByText("Morning Collection Run"));

    await waitFor(() => {
      expect(screen.getByText("North Hall")).toBeInTheDocument();
      expect(screen.getByText("Lamp")).toBeInTheDocument();
    });
  });

  test("Complete button sends PATCH and updates the event status in the list", async () => {
    const completedEvent = { ...EVENT_SCHEDULED, status: "completed" };
    vi.spyOn(global, "fetch")
      .mockResolvedValueOnce(mockOk([HUB]))
      .mockResolvedValueOnce(mockOk([EVENT_SCHEDULED]))
      .mockResolvedValueOnce(mockOk(ROUTE))
      .mockResolvedValueOnce(mockOk(completedEvent)); // PATCH

    renderPage();
    await waitFor(() => screen.getByText("Morning Collection Run"));
    fireEvent.click(screen.getByText("Morning Collection Run"));
    await waitFor(() => screen.getByRole("button", { name: /complete/i }));

    fireEvent.click(screen.getByRole("button", { name: /complete/i }));

    await waitFor(() => expect(screen.getByText("Completed")).toBeInTheDocument());
    // Complete button should be gone after event is marked completed
    expect(screen.queryByRole("button", { name: /^complete$/i })).not.toBeInTheDocument();
  });

  test("New Run button is disabled until the hub ID resolves", async () => {
    vi.spyOn(global, "fetch")
      .mockReturnValueOnce(new Promise(() => {})); // hub fetch never resolves

    renderPage();
    expect(screen.getByRole("button", { name: /new run/i })).toBeDisabled();
  });

  test("creating a new event prepends it to the event list", async () => {
    const newEvent = { id: 999, title: "Afternoon Run", status: "scheduled", scheduled_at: new Date().toISOString(), holds_count: 0 };
    vi.spyOn(global, "fetch")
      .mockResolvedValueOnce(mockOk([HUB]))
      .mockResolvedValueOnce(mockOk([EVENT_SCHEDULED]))
      .mockResolvedValueOnce(mockOk(newEvent))  // POST create
      .mockResolvedValueOnce(mockOk({ stop_count: 0, stops: [] })); // route for new event

    renderPage();
    await waitFor(() => screen.getByText("Morning Collection Run"));

    fireEvent.click(screen.getByRole("button", { name: /new run/i }));
    await waitFor(() => screen.getByText("Schedule Collection Run"));

    // Fill in date and submit
    const dateInput = document.querySelector("input[type='datetime-local']");
    fireEvent.change(dateInput, { target: { value: "2026-06-01T10:00" } });
    fireEvent.click(screen.getByRole("button", { name: /^create$/i }));

    await waitFor(() => expect(screen.getAllByText("Afternoon Run").length).toBeGreaterThan(0));
  });
});
