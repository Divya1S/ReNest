import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";


import { clearApiCache } from "../lib/apiCache";

import LeaderboardPage from "./LeaderboardPage";

const PEOPLE = [
  { rank: 1, display_name: "Alice", is_me: false, milestone: "campus_hero",  item_count: 42, total_value: 380, completion_rate: 0.95 },
  { rank: 2, display_name: "Bob",   is_me: true,  milestone: "active_rescuer", item_count: 28, total_value: 210, completion_rate: 0.82 },
];
const BUILDINGS = [
  { building: "North Hall", item_count: 60, total_value: 500, contributors: 12 },
  { building: "South Dorm", item_count: 30, total_value: 220, contributors: 7  },
];

function mockOk(data) {
  return { ok: true, status: 200, json: async () => data };
}
function mockErr() {
  return { ok: false, status: 500, json: async () => ({ detail: "Server error" }) };
}

function renderPage() {
  // The page links to /settings for the leaderboard opt-out, so it needs
  // router context exactly as it has in the app.
  render(
    <MemoryRouter>
      <LeaderboardPage />
    </MemoryRouter>,
  );
}

describe("LeaderboardPage", () => {
  beforeEach(() => clearApiCache());
  afterEach(() => vi.restoreAllMocks());

  test("shows loading skeletons while fetch is in-flight", () => {
    vi.spyOn(global, "fetch").mockReturnValue(new Promise(() => {}));
    renderPage();
    expect(document.querySelectorAll(".animate-pulse").length).toBeGreaterThan(0);
  });

  test("renders leaderboard entries with display names and item counts", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(
      mockOk({ leaderboard: PEOPLE, my_rank: 2 })
    );
    renderPage();
    await waitFor(() => {
      expect(screen.getByText("Alice")).toBeInTheDocument();
      expect(screen.getByText("Bob")).toBeInTheDocument();
    });
  });

  test("shows '(you)' next to the current user's row", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(
      mockOk({ leaderboard: PEOPLE, my_rank: 2 })
    );
    renderPage();
    await waitFor(() => expect(screen.getByText("(you)")).toBeInTheDocument());
  });

  test("shows the user's campus rank below the list", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(
      mockOk({ leaderboard: PEOPLE, my_rank: 2 })
    );
    renderPage();
    await waitFor(() => expect(screen.getByText(/#2/)).toBeInTheDocument());
  });

  test("shows error panel with retry when fetch fails", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(mockErr());
    renderPage();
    await waitFor(() => {
      expect(screen.getByText(/server error/i)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
    });
  });

  test("retry button re-fetches the leaderboard", async () => {
    const fetchSpy = vi.spyOn(global, "fetch")
      .mockResolvedValueOnce(mockErr())
      .mockResolvedValue(mockOk({ leaderboard: PEOPLE, my_rank: 2 }));

    renderPage();
    await waitFor(() => screen.getByRole("button", { name: /try again/i }));
    fireEvent.click(screen.getByRole("button", { name: /try again/i }));

    await waitFor(() => expect(screen.getByText("Alice")).toBeInTheDocument());
    expect(fetchSpy.mock.calls.length).toBe(2);
  });

  test("switching to Buildings tab fetches the buildings endpoint", async () => {
    const fetchSpy = vi.spyOn(global, "fetch")
      .mockResolvedValueOnce(mockOk({ leaderboard: PEOPLE, my_rank: 1 }))
      .mockResolvedValue(mockOk({ buildings: BUILDINGS }));

    renderPage();
    await waitFor(() => screen.getByText("Alice"));

    fireEvent.click(screen.getByRole("button", { name: /buildings/i }));

    await waitFor(() => {
      expect(screen.getByText("North Hall")).toBeInTheDocument();
      expect(screen.getByText("South Dorm")).toBeInTheDocument();
    });
    expect(fetchSpy.mock.calls.at(-1)[0]).toContain("/leaderboard/buildings");
  });

  test("period toggle re-fetches with ?period=semester", async () => {
    const fetchSpy = vi.spyOn(global, "fetch")
      .mockResolvedValue(mockOk({ leaderboard: PEOPLE, my_rank: 1 }));

    renderPage();
    await waitFor(() => screen.getByText("Alice"));

    fireEvent.click(screen.getByRole("button", { name: /this semester/i }));

    await waitFor(() =>
      expect(fetchSpy.mock.calls.at(-1)[0]).toContain("period=semester")
    );
  });

  test("period toggle is hidden on the Buildings tab", async () => {
    vi.spyOn(global, "fetch")
      .mockResolvedValueOnce(mockOk({ leaderboard: PEOPLE, my_rank: 1 }))
      .mockResolvedValue(mockOk({ buildings: BUILDINGS }));

    renderPage();
    await waitFor(() => screen.getByText("Alice"));
    fireEvent.click(screen.getByRole("button", { name: /buildings/i }));

    await waitFor(() => screen.getByText("North Hall"));
    expect(screen.queryByRole("button", { name: /this semester/i })).not.toBeInTheDocument();
  });

  test("shows empty state message when the leaderboard has no entries", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(
      mockOk({ leaderboard: [], my_rank: null })
    );
    renderPage();
    await waitFor(() =>
      expect(screen.getByText(/no data yet/i)).toBeInTheDocument()
    );
  });
});
