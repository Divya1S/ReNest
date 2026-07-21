import { render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, test, vi } from "vitest";

import TrustPage from "./TrustPage";

describe("TrustPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  test("renders trust summary and report history", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({
        summary: {
          completed_rescues: 4,
          active_listings: 2,
          successful_handoffs: 3,
          claims_completed: 1,
          open_reports: 0,
          average_rating: 4.8,
          total_feedback_received: 5,
          positive_feedback_rate: 100,
          pending_feedback: 1,
          trust_badge: "Trusted handoff",
          member_since: new Date().toISOString(),
          campus_name: "Pacific State",
        },
        received_feedback: [
          {
            id: 4,
            reservation: 8,
            listing_title: "Desk lamp",
            reviewer: { id: 9, display_name: "Leo" },
            reviewee: { id: 2, display_name: "Maya" },
            rating: 5,
            tags: ["responsive", "easy_pickup"],
            note: "Super smooth handoff.",
            created_at: new Date().toISOString(),
          },
        ],
        pending_feedback: [
          {
            reservation_id: 12,
            listing_id: 4,
            listing_title: "Desk lamp",
            counterparty_name: "Leo",
            updated_at: new Date().toISOString(),
          },
        ],
        reports_filed: [
          {
            id: 12,
            listing: 4,
            listing_title: "Desk lamp",
            reporter: { id: 2, display_name: "Maya" },
            reason: "inaccurate",
            details: "Photos did not match the description.",
            status: "open",
            created_at: new Date().toISOString(),
          },
        ],
        reports_on_my_listings: [],
        active_rescues: [],
      }),
    });

    render(
      <MemoryRouter initialEntries={["/trust"]}>
        <Routes>
          <Route path="/trust" element={<TrustPage />} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText(/build confidence into every rescue/i)).toBeInTheDocument();
      expect(screen.getByText("4")).toBeInTheDocument();
      expect(screen.getAllByText(/desk lamp/i).length).toBeGreaterThan(0);
      expect(screen.getByText(/trusted handoff/i)).toBeInTheDocument();
    });
  });
});
