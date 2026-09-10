import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, test, vi } from "vitest";

import { INVESTIGATION, TXN, mockRiskFetch } from "../test/riskFixtures";

import RiskInvestigatorPage from "./RiskInvestigatorPage";

describe("RiskInvestigatorPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  test("asks a suggested question and separates facts from inferences with evidence links", async () => {
    const posted = [];
    mockRiskFetch([
      [(url, method) => url.includes("/api/risk/investigations") && method === "POST", (init) => { posted.push(JSON.parse(init.body)); return INVESTIGATION; }],
      [(url) => url.includes("/api/risk/investigations"), { count: 0, next: null, previous: null, results: [] }],
      [(url) => url.includes("/api/risk/investigator"), { mode: "deterministic", intents: [], suggested_questions: [INVESTIGATION.question], allowed_queries: ["decision_counts", "top_factors"] }],
    ]);

    render(
      <MemoryRouter initialEntries={["/risk/investigator"]}>
        <RiskInvestigatorPage />
      </MemoryRouter>,
    );

    await waitFor(() => expect(screen.getByRole("button", { name: INVESTIGATION.question })).toBeInTheDocument());
    expect(screen.getByText(/deterministic templates/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: INVESTIGATION.question }));

    await waitFor(() => expect(screen.getByRole("article", { name: /investigation result/i })).toBeInTheDocument());
    expect(posted).toEqual([{ question: INVESTIGATION.question }]);
    expect(screen.getByText("From the data")).toBeInTheDocument();
    expect(screen.getByText("Possible interpretation")).toBeInTheDocument();
    expect(screen.getByText(/signature of account takeover/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: TXN.public_id })).toHaveAttribute("href", `/risk/transactions/${TXN.public_id}`);
    expect(screen.getByText(/decision_counts\(window_hours=24\)/)).toBeInTheDocument();
  });
});
