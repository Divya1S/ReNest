import { render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, describe, expect, test, vi } from "vitest";

import { AuthContext } from "../context/AuthContext";

import RequestsPage from "./RequestsPage";

describe("RequestsPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  test("renders campus requests and my requests", async () => {
    vi.spyOn(global, "fetch")
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        // Real /requests contract: DRF paginated envelope, never a bare array.
        json: async () => ({
          count: 2,
          next: null,
          previous: null,
          results: [
          {
            id: 1,
            seeker: { id: 10, display_name: "Leo", campus_name: "Pacific State" },
            matched_listing: null,
            matched_listing_detail: null,
            title: "Need a desk lamp",
            description: "Looking for a small lamp.",
            category: "lighting",
            pickup_zone: "Library east entrance",
            needed_by: new Date().toISOString(),
            budget_amount: 15,
            urgency: "soon",
            status: "open",
            can_edit: false,
            can_match: true,
            is_urgent: true,
            time_left_label: "12h left",
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
          },
          {
            id: 2,
            seeker: { id: 3, display_name: "Maya", campus_name: "Pacific State" },
            matched_listing: null,
            matched_listing_detail: null,
            title: "Need storage bins",
            description: "",
            category: "storage",
            pickup_zone: "North Hall lobby",
            needed_by: new Date().toISOString(),
            budget_amount: 0,
            urgency: "flexible",
            status: "open",
            can_edit: true,
            can_match: false,
            is_urgent: false,
            time_left_label: "2d left",
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
          },
          ],
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({
          count: 1,
          next: null,
          previous: null,
          results: [
            {
              id: 7,
              title: "Spare desk lamp",
              category: "lighting",
              owner: { id: 3, display_name: "Maya" },
            },
          ],
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({
          stats: {
            fulfillable_needs: 1,
            requests_with_matches: 1,
            campus_urgent_requests: 1,
          },
          fulfill_opportunities: [
            {
              listing: {
                id: 7,
                title: "Spare desk lamp",
                category: "lighting",
              },
              match_count: 1,
              matches: [
                {
                  id: 1,
                  title: "Need a desk lamp",
                  category: "lighting",
                  seeker: { id: 10, display_name: "Leo" },
                  pickup_zone: "Library east entrance",
                  time_left_label: "12h left",
                },
              ],
            },
          ],
          request_recommendations: [
            {
              request: {
                id: 2,
                title: "Need storage bins",
                category: "storage",
                time_left_label: "2d left",
              },
              match_count: 1,
              matches: [
                {
                  id: 8,
                  title: "Stackable storage bins",
                  owner: { id: 11, display_name: "Leo" },
                  pickup_zone: "North Hall lobby",
                },
              ],
            },
          ],
          urgent_requests: [],
        }),
      });

    const router = createMemoryRouter(
      [{ path: "/requests", element: <RequestsPage /> }],
      { initialEntries: ["/requests"] },
    );

    render(
      <AuthContext.Provider value={{ user: { id: 3, display_name: "Maya", campus_name: "Pacific State" } }}>
        <RouterProvider router={router} />
      </AuthContext.Provider>,
    );

    await waitFor(() => {
      expect(screen.getByText(/turn campus need into a rescue match/i)).toBeInTheDocument();
      expect(screen.getAllByText(/need a desk lamp/i).length).toBeGreaterThan(0);
      expect(screen.getAllByText(/need storage bins/i).length).toBeGreaterThan(0);
      expect(screen.getByText(/requests your listings can fulfill/i)).toBeInTheDocument();
    });
  });
});
