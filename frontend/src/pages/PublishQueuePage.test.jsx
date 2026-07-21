import { render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, describe, expect, test, vi } from "vitest";

import PublishQueuePage from "./PublishQueuePage";

describe("PublishQueuePage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  test("loads publish readiness states and missing fields", async () => {
    vi.spyOn(global, "fetch")
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({
          session: {
            id: 7,
            name: "North Hall Publish Queue",
            pickup_zone: "North Hall lobby",
            move_out_deadline: new Date().toISOString(),
          },
          summary: {
            ready_to_publish_count: 1,
            missing_info_count: 1,
            donation_route_count: 0,
            published_count: 0,
          },
          items: [
            {
              id: 1,
              scan_session: 7,
              title: "Ready lamp",
              category: "lighting",
              price_type: "low_cost",
              price_amount: "8.00",
              estimated_retail_value: "24.00",
              notes: "Ready to post.",
              publish_readiness: "ready",
              recommended_action: "publish",
              missing_fields: [],
              source_image_detail: { id: 4, position: 1, image_url: "/room-1.svg" },
            },
            {
              id: 2,
              scan_session: 7,
              title: "Need price bin",
              category: "storage",
              price_type: "low_cost",
              price_amount: "0.00",
              estimated_retail_value: "18.00",
              notes: "Needs price.",
              publish_readiness: "needs_info",
              recommended_action: "publish",
              missing_fields: ["price_amount"],
              source_image_detail: { id: 4, position: 1, image_url: "/room-1.svg" },
            },
          ],
          published_listings: [],
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => [
          {
            key: "desk_lamp",
            label: "Desk Lamp",
            title: "Desk lamp",
            category: "lighting",
            default_condition: "good",
            default_price_type: "low_cost",
            default_price_amount: "8.00",
            default_estimated_retail_value: "24.00",
            default_notes: "Study lamp preset.",
          },
        ],
      });

    const router = createMemoryRouter(
      [{ path: "/publish/:sessionId", element: <PublishQueuePage /> }],
      { initialEntries: ["/publish/7"] },
    );

    render(<RouterProvider router={router} />);

    await waitFor(() => {
      expect(screen.getByText(/Ready lamp/i)).toBeInTheDocument();
      expect(screen.getByText(/Need price bin/i)).toBeInTheDocument();
    });

    expect(screen.getAllByText(/Ready to Publish/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Needs Info/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/price amount/i)).toBeInTheDocument();
  });
});
