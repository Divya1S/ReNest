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

  describe("attempts held for review", () => {
    const HELD = { id: 21, amount: "45.00", currency: "USD", category: "supplies", merchant_id: "mkt_supplies", description: "Bulk paper", decision: "review", reasons: [], risk_score: 22, counts_toward_spend: true, created_at: "2026-09-09T14:05:00Z" };
    const ALLOWED = { ...HELD, id: 20, amount: "12.00", description: "Pens", decision: "allow" };

    function mockSandbox({ resolve, posted }) {
      let attempts = [HELD, ALLOWED];
      let agent = AGENT;
      return mockRiskFetch([
        [
          (url, method) => url.endsWith("/transactions/21/resolve") && method === "POST",
          (init) => {
            const body = JSON.parse(init.body);
            posted.push(body);
            const updated = resolve(body);
            attempts = attempts.map((item) => (item.id === updated.id ? updated : item));
            // The server moves the budget with the decision.
            agent = { ...AGENT, spent_today: body.approve ? "63.50" : "18.50" };
            return updated;
          },
        ],
        [(url) => url.includes("/transactions"), () => ({ count: attempts.length, next: null, previous: null, results: attempts })],
        [(url) => url.includes("/api/risk/agents"), () => ({ count: 1, next: null, previous: null, results: [agent] })],
      ]);
    }

    function renderSandbox() {
      render(
        <MemoryRouter initialEntries={["/risk/agents"]}>
          <AgentSandboxPage />
        </MemoryRouter>,
      );
    }

    test("only review attempts get Approve and Reject", async () => {
      mockSandbox({ resolve: () => HELD, posted: [] });
      renderSandbox();

      expect(await screen.findByRole("button", { name: "Approve $45.00 at mkt_supplies" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Reject $45.00 at mkt_supplies" })).toBeInTheDocument();
      expect(screen.getAllByRole("button", { name: /^approve /i })).toHaveLength(1);
      expect(screen.getAllByRole("button", { name: /^reject /i })).toHaveLength(1);
    });

    test("Approve posts to the resolve endpoint, then refreshes the attempts and the budget", async () => {
      const posted = [];
      const spy = mockSandbox({ resolve: () => ({ ...HELD, decision: "allow" }), posted });
      renderSandbox();

      const approve = await screen.findByRole("button", { name: "Approve $45.00 at mkt_supplies" });
      expect(screen.getByRole("meter", { name: /daily budget used/i })).toHaveAttribute("aria-valuenow", "18.5");
      const getsBefore = spy.mock.calls.filter(([, init]) => !init?.method).length;

      await userEvent.type(screen.getByLabelText(/review note for \$45\.00 at mkt_supplies/i), "Checked with the RA");
      await userEvent.click(approve);

      await waitFor(() => expect(posted).toEqual([{ approve: true, note: "Checked with the RA" }]));
      const resolveCall = spy.mock.calls.find(([url]) => String(url).endsWith("/resolve"));
      expect(resolveCall[0]).toBe(`/api/risk/agents/${AGENT.public_id}/transactions/21/resolve`);

      // The row is no longer actionable and the budget reflects the approval.
      await waitFor(() =>
        expect(screen.queryByRole("button", { name: /^approve /i })).not.toBeInTheDocument(),
      );
      await waitFor(() =>
        expect(screen.getByRole("meter", { name: /daily budget used/i })).toHaveAttribute("aria-valuenow", "63.5"),
      );
      const urlsAfter = spy.mock.calls.filter(([, init]) => !init?.method).slice(getsBefore).map(([url]) => String(url));
      expect(urlsAfter.some((url) => url.includes("/transactions?"))).toBe(true);
      expect(urlsAfter.some((url) => url.includes("/api/risk/agents?"))).toBe(true);
    });

    test("Reject sends approve=false", async () => {
      const posted = [];
      mockSandbox({ resolve: () => ({ ...HELD, decision: "block", counts_toward_spend: false }), posted });
      renderSandbox();

      await userEvent.click(await screen.findByRole("button", { name: "Reject $45.00 at mkt_supplies" }));

      await waitFor(() => expect(posted).toEqual([{ approve: false, note: "" }]));
      await waitFor(() =>
        expect(screen.queryByRole("button", { name: /^reject /i })).not.toBeInTheDocument(),
      );
    });

    test("a 409 (already resolved elsewhere) re-reads the list instead of leaving stale buttons", async () => {
      let resolved = false;
      const spy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init = {}) => {
        const url = String(input);
        const method = (init.method || "GET").toUpperCase();
        if (url.endsWith("/resolve") && method === "POST") {
          resolved = true;
          return { ok: false, status: 409, json: async () => ({ detail: "This attempt is no longer awaiting review." }) };
        }
        if (url.includes("/transactions")) {
          const results = resolved ? [{ ...HELD, decision: "allow" }, ALLOWED] : [HELD, ALLOWED];
          return { ok: true, status: 200, json: async () => ({ count: 2, next: null, previous: null, results }) };
        }
        return { ok: true, status: 200, json: async () => ({ count: 1, next: null, previous: null, results: [AGENT] }) };
      });
      renderSandbox();

      await userEvent.click(await screen.findByRole("button", { name: "Approve $45.00 at mkt_supplies" }));

      await waitFor(() =>
        expect(screen.queryByRole("button", { name: /^approve /i })).not.toBeInTheDocument(),
      );
      expect(spy.mock.calls.filter(([url]) => String(url).endsWith("/resolve"))).toHaveLength(1);
    });
  });
});
