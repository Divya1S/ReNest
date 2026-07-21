import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, test, vi } from "vitest";

import NotificationsPage from "./NotificationsPage";

describe("NotificationsPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  test("renders unread alerts and marks one as read", async () => {
    vi.spyOn(global, "fetch")
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({
          unread_count: 1,
          results: [
            {
              id: 9,
              type: "ready_to_publish",
              title: "1 rescue draft ready to publish",
              body: "Ops Sweep has publish-ready items waiting in the queue.",
              link_path: "/publish/7",
              priority: "normal",
              is_read: false,
              read_at: null,
              created_at: new Date().toISOString(),
            },
          ],
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({
          id: 9,
          type: "ready_to_publish",
          title: "1 rescue draft ready to publish",
          body: "Ops Sweep has publish-ready items waiting in the queue.",
          link_path: "/publish/7",
          priority: "normal",
          is_read: true,
          read_at: new Date().toISOString(),
          created_at: new Date().toISOString(),
        }),
      });

    render(
      <MemoryRouter initialEntries={["/notifications"]}>
        <Routes>
          <Route path="/notifications" element={<NotificationsPage />} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText(/1 rescue draft ready to publish/i)).toBeInTheDocument();
    });

    await userEvent.click(screen.getByRole("button", { name: /mark read/i }));

    await waitFor(() => {
      expect(screen.getByText(/unread now/i)).toBeInTheDocument();
      expect(screen.getAllByText("0").length).toBeGreaterThan(0);
      expect(screen.getByText(/recently read/i)).toBeInTheDocument();
    });
  });
});
