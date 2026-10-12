import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { clearApiCache } from "../lib/apiCache";
import { axe } from "../test/setup";

import HandoffHubPage from "./HandoffHubPage";

const BASE_RESERVATION = {
  id: 5,
  status: "confirmed",
  relationship: "owner",
  pickup_time_window: "Tonight from 6pm to 7pm",
  pickup_slots: [],
  confirmed_slot: null,
  pickup_zone: "North Hall lobby",
  move_out_deadline: new Date(Date.now() + 86400000).toISOString(),
  handoff_code: "DC-005-0005",
  handoff_pin_display: null,
  next_step: "Meet the claimant at the pickup zone, then enter their PIN.",
  handoff_checklist: [],
  counterparty_name: "Leo",
  time_pressure: "normal",
  allowed_actions: ["cancelled"],
  can_leave_feedback: false,
  my_feedback: null,
  feedback_summary: { count: 0, average_rating: null },
  feedback_entries: [],
  listing_detail: { id: 8, title: "Desk lamp" },
};

function json(body, status = 200) {
  return Promise.resolve({ ok: status < 400, status, json: async () => body });
}

/**
 * Scripted API for /handoffs/5. `reservation` may be a function so a test can
 * change what the server returns between polls.
 */
function mockHandoffApi({ reservation = BASE_RESERVATION, routes = {} } = {}) {
  return vi.spyOn(global, "fetch").mockImplementation((url, opts = {}) => {
    const method = (opts.method || "GET").toUpperCase();
    const key = `${method} ${url}`;
    for (const [pattern, handler] of Object.entries(routes)) {
      if (key.includes(pattern)) return handler(opts);
    }
    if (key.includes("/messages")) return json({ results: [], count: 0 });
    if (key === "GET /api/reservations/5") {
      return json(typeof reservation === "function" ? reservation() : reservation);
    }
    return json({ detail: `unmocked ${key}` }, 404);
  });
}

function renderHub() {
  return render(
    <MemoryRouter initialEntries={["/handoffs/5"]}>
      <Routes>
        <Route path="/handoffs/:reservationId" element={<HandoffHubPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

function callsTo(spy, method, fragment) {
  return spy.mock.calls.filter(
    ([url, opts]) => String(url).includes(fragment) && (opts?.method || "GET").toUpperCase() === method,
  );
}

describe("HandoffHubPage completion contract", () => {
  beforeEach(() => clearApiCache());
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  test("owner completes a confirmed handoff only by entering the PIN", async () => {
    let completed = false;
    const spy = mockHandoffApi({
      reservation: () =>
        completed ? { ...BASE_RESERVATION, status: "completed", allowed_actions: [] } : BASE_RESERVATION,
      routes: {
        "POST /api/reservations/5/verify-pin": () => {
          completed = true;
          return json({ detail: "Handoff complete." });
        },
      },
    });
    renderHub();

    const pin = await screen.findByLabelText("Pickup PIN");
    expect(screen.queryByRole("button", { name: /mark handed off/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^completed?$/i })).not.toBeInTheDocument();

    await userEvent.type(pin, "123456");

    await waitFor(() => expect(screen.getByText(/handoff complete\. nice rescue/i)).toBeInTheDocument());
    const verify = callsTo(spy, "POST", "/api/reservations/5/verify-pin");
    expect(verify).toHaveLength(1);
    expect(JSON.parse(verify[0][1].body)).toEqual({ pin: "123456" });
    // Completion never goes through a status PATCH.
    expect(callsTo(spy, "PATCH", "/api/reservations/5")).toHaveLength(0);
  });

  test("owner sees the PIN entry, not a completion button, on an overdue handoff", async () => {
    const spy = mockHandoffApi({
      // A backend that still advertised "completed" must not resurrect the button.
      reservation: { ...BASE_RESERVATION, status: "expired_unresolved", allowed_actions: ["completed", "cancelled"] },
      routes: { "POST /api/reservations/5/verify-pin": () => json({ detail: "Handoff complete." }) },
    });
    renderHub();

    expect(await screen.findByLabelText("Pickup PIN")).toBeInTheDocument();
    expect(screen.getByText(/passed 48 hours without completion/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /mark handed off/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^completed?$/i })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /cancel this reservation/i })).toBeInTheDocument();

    await userEvent.type(screen.getByLabelText("Pickup PIN"), "654321");

    await waitFor(() => expect(callsTo(spy, "POST", "/verify-pin")).toHaveLength(1));
    expect(JSON.parse(callsTo(spy, "POST", "/verify-pin")[0][1].body)).toEqual({ pin: "654321" });
    expect(callsTo(spy, "PATCH", "/api/reservations/5")).toHaveLength(0);
  });

  test("claimant on an overdue handoff still sees the PIN to show, without the en-route button", async () => {
    mockHandoffApi({
      reservation: {
        ...BASE_RESERVATION,
        status: "expired_unresolved",
        relationship: "claimant",
        counterparty_name: "Maya",
        handoff_pin_display: "482913",
      },
    });
    renderHub();

    expect(await screen.findByText("482913")).toBeInTheDocument();
    expect(screen.queryByLabelText("Pickup PIN")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /on my way/i })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /cancel this reservation/i })).toBeInTheDocument();
  });

  test("a wrong PIN is reported next to the field and can be retried", async () => {
    const spy = mockHandoffApi({
      routes: { "POST /api/reservations/5/verify-pin": () => json({ detail: "Incorrect PIN." }, 400) },
    });
    renderHub();

    const pin = await screen.findByLabelText("Pickup PIN");
    await userEvent.type(pin, "000000");

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Incorrect PIN.");
    expect(pin).toHaveAttribute("aria-invalid", "true");
    expect(pin).toHaveAccessibleDescription(/incorrect pin/i);
    expect(callsTo(spy, "POST", "/verify-pin")).toHaveLength(1);

    await userEvent.type(pin, "{backspace}");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  test("approving a request still uses the status PATCH", async () => {
    let status = "requested";
    const spy = mockHandoffApi({
      reservation: () => ({ ...BASE_RESERVATION, status, allowed_actions: ["confirmed", "cancelled"] }),
      routes: {
        "PATCH /api/reservations/5": (opts) => {
          status = JSON.parse(opts.body).status;
          return json({ ...BASE_RESERVATION, status });
        },
      },
    });
    renderHub();

    await userEvent.click(await screen.findByRole("button", { name: /approve request/i }));

    await waitFor(() => expect(screen.getByLabelText("Pickup PIN")).toBeInTheDocument());
    expect(JSON.parse(callsTo(spy, "PATCH", "/api/reservations/5")[0][1].body)).toEqual({ status: "confirmed" });
  });
});

describe("HandoffHubPage refresh", () => {
  beforeEach(() => {
    clearApiCache();
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    Object.defineProperty(document, "hidden", { configurable: true, value: false });
  });

  test("picks up a status change made by the other party without a reload", async () => {
    // Regression: only the chat polled. The claimant kept seeing "waiting for
    // approval" until they reloaded the page.
    let status = "requested";
    const spy = mockHandoffApi({
      reservation: () => ({
        ...BASE_RESERVATION,
        status,
        relationship: "claimant",
        counterparty_name: "Maya",
        handoff_pin_display: status === "confirmed" ? "482913" : null,
      }),
    });
    renderHub();

    expect(await screen.findByText(/waiting for maya to approve/i)).toBeInTheDocument();
    expect(callsTo(spy, "GET", "/api/reservations/5").filter(([url]) => String(url).endsWith("/5"))).toHaveLength(1);

    status = "confirmed"; // the owner approved on their device
    await act(async () => {
      vi.advanceTimersByTime(15_000);
    });

    expect(await screen.findByText("482913")).toBeInTheDocument();
    expect(screen.queryByText(/waiting for maya to approve/i)).not.toBeInTheDocument();
  });

  test("does not poll while the tab is hidden and catches up when it returns", async () => {
    const spy = mockHandoffApi();
    renderHub();
    await screen.findByLabelText("Pickup PIN");
    const reservationGets = () =>
      callsTo(spy, "GET", "/api/reservations/5").filter(([url]) => String(url).endsWith("/5")).length;
    expect(reservationGets()).toBe(1);

    Object.defineProperty(document, "hidden", { configurable: true, value: true });
    await act(async () => {
      vi.advanceTimersByTime(45_000);
    });
    expect(reservationGets()).toBe(1);

    Object.defineProperty(document, "hidden", { configurable: true, value: false });
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await waitFor(() => expect(reservationGets()).toBe(2));
  });

  test("a failed background refresh keeps the handoff on screen", async () => {
    let fail = false;
    vi.spyOn(global, "fetch").mockImplementation((url) => {
      if (String(url).includes("/messages")) return json({ results: [], count: 0 });
      return fail ? json({ detail: "Server error." }, 500) : json(BASE_RESERVATION);
    });
    renderHub();
    await screen.findByLabelText("Pickup PIN");

    fail = true;
    await act(async () => {
      vi.advanceTimersByTime(15_000);
    });
    await act(async () => {});

    expect(screen.getByLabelText("Pickup PIN")).toBeInTheDocument();
    expect(screen.queryByText(/could not be loaded/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/handoff not found/i)).not.toBeInTheDocument();
  });
});

describe("HandoffHubPage load failures", () => {
  beforeEach(() => clearApiCache());
  afterEach(() => vi.restoreAllMocks());

  test("a 404 reads as not found", async () => {
    mockHandoffApi({ routes: { "GET /api/reservations/5": () => json({ detail: "Not found." }, 404) } });
    renderHub();

    expect(await screen.findByRole("heading", { name: /handoff not found/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /try again/i })).not.toBeInTheDocument();
  });

  test("a 500 offers a retry instead of claiming the handoff does not exist", async () => {
    let healthy = false;
    vi.spyOn(global, "fetch").mockImplementation((url) => {
      if (String(url).includes("/messages")) return json({ results: [], count: 0 });
      return healthy ? json(BASE_RESERVATION) : json({ detail: "Internal server error." }, 500);
    });
    renderHub();

    expect(await screen.findByRole("heading", { name: /could not be loaded/i })).toBeInTheDocument();
    expect(screen.getByText(/internal server error\./i)).toBeInTheDocument();
    expect(screen.queryByText(/handoff not found/i)).not.toBeInTheDocument();

    healthy = true;
    fireEvent.click(screen.getByRole("button", { name: /try again/i }));

    expect(await screen.findByLabelText("Pickup PIN")).toBeInTheDocument();
  });

  test("a network failure also offers a retry", async () => {
    vi.spyOn(global, "fetch").mockRejectedValue(new TypeError("Failed to fetch"));
    renderHub();

    expect(await screen.findByRole("heading", { name: /could not be loaded/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
  });
});

describe("HandoffHubPage", () => {
  beforeEach(() => clearApiCache());
  afterEach(() => {
    vi.restoreAllMocks();
  });

  test("shows feedback form for completed handoffs and saves feedback", async () => {
    const completedReservation = {
      id: 5,
      status: "completed",
      relationship: "owner",
      pickup_time_window: "Tonight from 6pm to 7pm",
      pickup_zone: "North Hall lobby",
      move_out_deadline: new Date().toISOString(),
      handoff_code: "DC-005-0005",
      next_step: "This handoff is complete.",
      handoff_checklist: ["Meet at North Hall lobby."],
      counterparty_name: "Leo",
      time_pressure: "normal",
      allowed_actions: [],
      can_leave_feedback: true,
      my_feedback: null,
      feedback_summary: { count: 0, average_rating: null },
      feedback_entries: [],
      listing_detail: { id: 8, title: "Desk lamp" },
    };

    const updatedReservation = {
      ...completedReservation,
      can_leave_feedback: false,
      my_feedback: {
        id: 9,
        reservation: 5,
        listing_title: "Desk lamp",
        reviewer: { id: 1, display_name: "Maya" },
        reviewee: { id: 2, display_name: "Leo" },
        rating: 5,
        tags: ["responsive"],
        note: "Great handoff.",
        created_at: new Date().toISOString(),
      },
      feedback_summary: { count: 1, average_rating: 5 },
      feedback_entries: [
        {
          id: 9,
          reservation: 5,
          listing_title: "Desk lamp",
          reviewer: { id: 1, display_name: "Maya" },
          reviewee: { id: 2, display_name: "Leo" },
          rating: 5,
          tags: ["responsive"],
          note: "Great handoff.",
          created_at: new Date().toISOString(),
        },
      ],
    };

    vi.spyOn(global, "fetch")
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => completedReservation,
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ results: [], count: 0 }),
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 201,
        json: async () => ({
          id: 9,
          rating: 5,
          tags: ["responsive"],
          note: "Great handoff.",
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => updatedReservation,
      });

    render(
      <MemoryRouter initialEntries={["/handoffs/5"]}>
        <Routes>
          <Route path="/handoffs/:reservationId" element={<HandoffHubPage />} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /save feedback/i })).toBeInTheDocument();
    });

    await userEvent.click(screen.getByRole("button", { name: /5 stars/i }));
    await userEvent.type(screen.getByPlaceholderText(/short note about how the pickup went/i), "Great handoff.");
    await userEvent.click(screen.getByRole("button", { name: /save feedback/i }));

    await waitFor(() => {
      expect(screen.getByText(/^saved$/i)).toBeInTheDocument();
      expect(screen.getAllByText(/great handoff\./i).length).toBeGreaterThan(0);
    });
  });

  test("passes axe accessibility audit on feedback form", async () => {
    const completedReservation = {
      id: 6,
      status: "completed",
      relationship: "claimant",
      pickup_time_window: "Tomorrow 3pm",
      pickup_zone: "South Hall",
      move_out_deadline: new Date().toISOString(),
      handoff_code: "DC-006-0006",
      next_step: "Leave feedback.",
      handoff_checklist: [],
      counterparty_name: "Maya",
      time_pressure: "normal",
      allowed_actions: [],
      can_leave_feedback: true,
      my_feedback: null,
      feedback_summary: { count: 0, average_rating: null },
      feedback_entries: [],
      listing_detail: { id: 9, title: "Blue lamp" },
    };

    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => completedReservation,
    });

    const { container } = render(
      <MemoryRouter initialEntries={["/handoffs/6"]}>
        <Routes>
          <Route path="/handoffs/:reservationId" element={<HandoffHubPage />} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => expect(screen.getByRole("button", { name: /save feedback/i })).toBeInTheDocument());

    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });
});
