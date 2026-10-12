import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, test, vi } from "vitest";

import { SCENARIOS, SIM_RUN, mockRiskFetch } from "../test/riskFixtures";

import FraudLabPage from "./FraudLabPage";

describe("FraudLabPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  test("runs a scenario and shows measured metrics with the sensitivity sweep", async () => {
    const posted = [];
    mockRiskFetch([
      [(url) => url.includes("/api/risk/scenarios"), SCENARIOS],
      [(url, method) => url.includes("/api/risk/simulations") && method === "POST", (init) => { posted.push(JSON.parse(init.body)); return SIM_RUN; }],
      [(url) => url.includes("/api/risk/simulations?"), { count: 0, next: null, previous: null, results: [] }],
    ]);

    render(
      <MemoryRouter initialEntries={["/risk/lab"]}>
        <FraudLabPage />
      </MemoryRouter>,
    );

    await waitFor(() => expect(screen.getByRole("radio", { name: /account takeover/i })).toBeInTheDocument());
    expect(screen.getByText(/no runs yet/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("radio", { name: /account takeover/i }));
    await userEvent.click(screen.getByRole("button", { name: /run scenario/i }));

    await waitFor(() => expect(screen.getByText("Precision (flagged)")).toBeInTheDocument());
    expect(posted[0]).toMatchObject({ scenario: "account_takeover", count: 1000, sensitivity: 50 });
    expect(screen.getAllByText("80.0%").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("96.0%").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("8.0%").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/Synthetic data with known labels/)).toBeInTheDocument();
    // Two sliders: the run form and the "try another sensitivity" rescore control.
    expect(screen.getAllByRole("slider", { name: /sensitivity: 50/i })).toHaveLength(2);
    expect(screen.getByRole("link", { name: /open 1000 transactions/i })).toHaveAttribute("href", `/risk/transactions?simulation=${SIM_RUN.public_id}&include_simulation=1`);
  });
});
