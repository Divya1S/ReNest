import { render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, test, vi } from "vitest";

import { OVERVIEW, POLICY, TXN, mockRiskFetch } from "../test/riskFixtures";
import { axe } from "../test/setup";

import RiskOverviewPage from "./RiskOverviewPage";

vi.mock("../context/AuthContext", () => ({
  useAuth: () => ({ user: { id: 1, display_name: "Staff", is_staff: true } }),
}));

describe("RiskOverviewPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  test("renders overview cards, distribution, policy controls and the live feed", async () => {
    mockRiskFetch([
      [(url) => url.includes("/api/risk/overview"), OVERVIEW],
      [(url) => url.includes("/api/risk/policy"), POLICY],
      [(url) => url.includes("/api/risk/feed"), { results: [TXN], generated_at: OVERVIEW.generated_at }],
    ]);

    const { container } = render(
      <MemoryRouter initialEntries={["/risk"]}>
        <RiskOverviewPage />
      </MemoryRouter>,
    );

    await waitFor(() => expect(screen.getByText("42")).toBeInTheDocument());
    expect(screen.getByText(/7\.1% of traffic/)).toBeInTheDocument();
    expect(screen.getByText("Review queue (all time)")).toBeInTheDocument();
    expect(screen.getByText(/Low/)).toBeInTheDocument();
    expect(screen.getByRole("slider", { name: /sensitivity/i })).toHaveValue("50");
    expect(screen.getByRole("button", { name: /apply to live policy/i })).toBeDisabled();
    await waitFor(() => expect(screen.getByRole("link", { name: /txn_01TESTAAAA/ })).toBeInTheDocument());
    expect(screen.getAllByText("Blocked").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByRole("navigation", { name: /risk intelligence sections/i })).toBeInTheDocument();
    expect(await axe(container)).toHaveNoViolations();
  });
});
