import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import PartnerPortalPage from "./PartnerPortalPage";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const USAGE = {
  campus: "USC",
  listing_count: 42,
  reservation_count: 18,
  webhook_endpoints: 2,
  rate_limit_per_day: 1000,
  scopes: ["listing_read", "reservation_read"],
};

const WEBHOOK = {
  id: 1,
  url: "https://housing.usc.edu/webhooks/renest",
  events: ["listing_created", "reservation_confirmed"],
  secret: "abc123secretxyz",
};

function mockOk(data) {
  return { ok: true, status: 200, json: async () => data };
}
function mockErr(status = 500) {
  return { ok: false, status, json: async () => ({ detail: "Server error" }) };
}

function renderPage() {
  render(<PartnerPortalPage />);
}

describe("PartnerPortalPage", () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
  });
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
    sessionStorage.clear();
  });

  test("shows 'no API key provisioned' message when no key is held", () => {
    renderPage();
    expect(screen.getByText(/no api key provisioned yet/i)).toBeInTheDocument();
    expect(screen.queryByText(/•/)).not.toBeInTheDocument();
  });

  test("shows masked stored key when a key is held for this session", async () => {
    sessionStorage.setItem("renest_partner_key", "dc_live_abcdef1234567890");
    vi.spyOn(global, "fetch")
      .mockResolvedValueOnce(mockOk(USAGE))
      .mockResolvedValueOnce(mockOk({ results: [] }));

    renderPage();
    // The stored key is shown as first 12 chars + bullet mask
    await waitFor(() => expect(screen.getByText(/dc_live_abcd/)).toBeInTheDocument());
  });

  test("renders usage stats when a stored key is present", async () => {
    sessionStorage.setItem("renest_partner_key", "dc_live_testkey123");
    vi.spyOn(global, "fetch")
      .mockResolvedValueOnce(mockOk(USAGE))
      .mockResolvedValueOnce(mockOk({ results: [] }));

    renderPage();
    await waitFor(() => {
      expect(screen.getByText("42")).toBeInTheDocument(); // listing_count
      expect(screen.getByText(/Usage.*USC/i)).toBeInTheDocument();
    });
  });

  test("shows usage error panel with retry when the usage fetch fails", async () => {
    sessionStorage.setItem("renest_partner_key", "dc_live_testkey123");
    vi.spyOn(global, "fetch")
      .mockResolvedValueOnce(mockErr(401))
      .mockResolvedValueOnce(mockOk({ results: [] }));

    renderPage();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
    });
  });

  test("New key form appears when 'New key' button is clicked", () => {
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: /new key/i }));
    expect(screen.getByPlaceholderText(/partner name/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /create key/i })).toBeInTheDocument();
  });

  test("provisioning a key keeps it for the session and shows the masked key", async () => {
    const rawKey = "dc_live_newlygeneratedkey123";
    vi.spyOn(global, "fetch").mockResolvedValue(mockOk({ key: rawKey }));

    renderPage();
    fireEvent.click(screen.getByRole("button", { name: /new key/i }));

    fireEvent.change(screen.getByPlaceholderText(/partner name/i), {
      target: { value: "Housing Portal" },
    });
    fireEvent.click(screen.getByRole("button", { name: /create key/i }));

    await waitFor(() => {
      expect(sessionStorage.getItem("renest_partner_key")).toBe(rawKey);
      // Created key is displayed in the green banner
      expect(screen.getByText(rawKey)).toBeInTheDocument();
    });
  });

  test("New key form is hidden again after a key is created", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(mockOk({ key: "dc_live_xyz" }));

    renderPage();
    fireEvent.click(screen.getByRole("button", { name: /new key/i }));
    fireEvent.change(screen.getByPlaceholderText(/partner name/i), {
      target: { value: "Test Partner" },
    });
    fireEvent.click(screen.getByRole("button", { name: /create key/i }));

    await waitFor(() =>
      expect(screen.queryByPlaceholderText(/partner name/i)).not.toBeInTheDocument()
    );
  });

  test("renders webhook endpoints when a stored key is present", async () => {
    sessionStorage.setItem("renest_partner_key", "dc_live_testkey123");
    vi.spyOn(global, "fetch")
      .mockResolvedValueOnce(mockOk(USAGE))
      .mockResolvedValueOnce(mockOk({ results: [WEBHOOK] }));

    renderPage();
    await waitFor(() =>
      expect(screen.getByText("https://housing.usc.edu/webhooks/renest")).toBeInTheDocument()
    );
  });

  test("embed snippet shows campus slug from usage data", async () => {
    sessionStorage.setItem("renest_partner_key", "dc_live_testkey123");
    vi.spyOn(global, "fetch")
      .mockResolvedValueOnce(mockOk(USAGE))
      .mockResolvedValueOnce(mockOk({ results: [] }));

    renderPage();
    await waitFor(() =>
      expect(screen.getByText(/data-renest-campus="usc"/)).toBeInTheDocument()
    );
  });
});
