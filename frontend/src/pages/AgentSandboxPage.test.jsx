import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, test, vi } from "vitest";

import { AGENT, AGENT_VERDICT, mockRiskFetch } from "../test/riskFixtures";

import AgentSandboxPage from "./AgentSandboxPage";

describe("AgentSandboxPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  test("selects an agent, shows its budget and explains a blocked attempt", async () => {
    const posted = [];
    mockRiskFetch([
      [(url, method) => url.includes("/transactions") && method === "POST", (init) => { posted.push(JSON.parse(init.body)); return AGENT_VERDICT; }],
      [(url) => url.includes("/transactions"), { count: 0, next: null, previous: null, results: [] }],
      [(url) => url.includes("/api/risk/agents"), { count: 1, next: null, previous: null, results: [AGENT] }],
    ]);

    render(
      <MemoryRouter initialEntries={["/risk/agents"]}>
        <AgentSandboxPage />
      </MemoryRouter>,
    );

    await waitFor(() => expect(screen.getByRole("button", { name: /dorm restock agent/i, pressed: true })).toBeInTheDocument());
    expect(screen.getByRole("meter", { name: /daily budget used/i })).toHaveAttribute("aria-valuenow", "18.5");
    expect(screen.getByText(/\$18\.50 of \$150\.00/)).toBeInTheDocument();

    await userEvent.clear(screen.getByLabelText(/amount/i));
    await userEvent.type(screen.getByLabelText(/amount/i), "75");
    await userEvent.clear(screen.getByLabelText(/category/i));
    await userEvent.type(screen.getByLabelText(/category/i), "electronics");
    await userEvent.click(screen.getByRole("button", { name: /attempt purchase/i }));

    await waitFor(() => expect(screen.getByText(/not in the allowed list/)).toBeInTheDocument());
    expect(posted[0]).toMatchObject({ amount: "75", category: "electronics" });
    expect(screen.getAllByText("block").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/\$131\.50 remaining/)).toBeInTheDocument();
  });
});
