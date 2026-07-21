import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, test, vi } from "vitest";

import { axe } from "../test/setup";

import HandoffHubPage from "./HandoffHubPage";

describe("HandoffHubPage", () => {
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
