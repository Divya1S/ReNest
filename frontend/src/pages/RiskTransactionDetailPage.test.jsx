import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, test, vi } from "vitest";

import { TXN_DETAIL, mockRiskFetch } from "../test/riskFixtures";
import { axe } from "../test/setup";

import RiskTransactionDetailPage from "./RiskTransactionDetailPage";

vi.mock("../context/AuthContext", () => ({
  useAuth: () => ({ user: { id: 1, display_name: "Staff", is_staff: true } }),
}));

function renderPage() {
  return render(
    <MemoryRouter initialEntries={[`/risk/transactions/${TXN_DETAIL.public_id}`]}>
      <Routes>
        <Route path="/risk/transactions/:publicId" element={<RiskTransactionDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("RiskTransactionDetailPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  test("shows the decision, factors, explanation, event timeline and audit trail", async () => {
    mockRiskFetch([[(url) => url.includes(`/api/risk/payments/${TXN_DETAIL.public_id}`), TXN_DETAIL]]);
    const { container } = renderPage();

    await waitFor(() => expect(screen.getByText("74")).toBeInTheDocument());
    expect(screen.getByRole("meter", { name: /risk score 74/i })).toBeInTheDocument();
    expect(screen.getByText(/RISK SCORE: 74 \/ 100/)).toBeInTheDocument();
    expect(screen.getByText("+34")).toBeInTheDocument();
    expect(screen.getByText("New device")).toBeInTheDocument();
    expect(screen.getByText("Risk evaluated")).toBeInTheDocument();
    expect(screen.getByText("service:risk-engine")).toBeInTheDocument();
    expect(screen.getByText(/handlers: audit, notify_customer/)).toBeInTheDocument();
    expect(screen.queryByText(/manual review/i)).not.toBeInTheDocument();
    expect(await axe(container)).toHaveNoViolations();
  });

  test("staff can resolve a payment that is under review", async () => {
    const held = { ...TXN_DETAIL, public_id: "txn_01HELD", status: "review", decision: "review", risk_score: 52 };
    const posted = [];
    mockRiskFetch([
      [(url, method) => url.endsWith("/review") && method === "POST", (init) => { posted.push(JSON.parse(init.body)); return { ...held, status: "completed", decision: "allow", manual_review: { by: 1, outcome: "approved", note: "ok" } }; }],
      [(url) => url.includes("/api/risk/payments/txn_01HELD"), held],
    ]);
    render(
      <MemoryRouter initialEntries={["/risk/transactions/txn_01HELD"]}>
        <Routes>
          <Route path="/risk/transactions/:publicId" element={<RiskTransactionDetailPage />} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument());
    await userEvent.type(screen.getByLabelText(/reviewer note/i), "ok");
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(screen.getByText(/manually approved/)).toBeInTheDocument());
    expect(posted).toEqual([{ approve: true, note: "ok" }]);
  });
});
