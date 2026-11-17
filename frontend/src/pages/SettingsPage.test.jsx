import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { apiFetch } from "../lib/api";

import SettingsPage from "./SettingsPage";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock("../lib/api", () => ({ apiFetch: vi.fn() }));

const refreshUser = vi.fn().mockResolvedValue(null);
let currentUser;

vi.mock("../context/AuthContext", () => ({
  useAuth: () => ({ user: currentUser, refreshUser }),
}));

function renderPage() {
  render(
    <MemoryRouter>
      <SettingsPage />
    </MemoryRouter>,
  );
}

describe("SettingsPage", () => {
  beforeEach(() => {
    currentUser = {
      id: 1,
      email: "maya@example.com",
      display_name: "Maya",
      campus_name: "Pacific State",
      email_verified: true,
      show_on_leaderboard: true,
      email_notifications: false,
    };
    apiFetch.mockResolvedValue({});
  });

  afterEach(() => vi.clearAllMocks());

  test("emails switched off by an unsubscribe link can be switched back on", async () => {
    renderPage();
    const toggle = screen.getByRole("switch", { name: /email me about my listings/i });
    expect(toggle).toHaveAttribute("aria-checked", "false");

    await userEvent.click(toggle);

    expect(apiFetch).toHaveBeenCalledWith("/auth/me", {
      method: "PATCH",
      body: { email_notifications: true },
    });
    await waitFor(() => expect(refreshUser).toHaveBeenCalled());
  });

  test("the leaderboard switch still sends its own field", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("switch", { name: /campus leaderboard/i }));
    expect(apiFetch).toHaveBeenCalledWith("/auth/me", {
      method: "PATCH",
      body: { show_on_leaderboard: false },
    });
  });

  test("deleting the account asks for the password before anything is sent", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: /delete account/i }));

    const confirm = screen.getByRole("button", { name: /delete my account/i });
    expect(confirm).toBeDisabled();
    expect(apiFetch).not.toHaveBeenCalled();

    await userEvent.type(screen.getByLabelText(/enter your password/i), "pw-Str0ng!x");
    await userEvent.click(confirm);

    expect(apiFetch).toHaveBeenCalledWith("/auth/account", {
      method: "DELETE",
      body: { password: "pw-Str0ng!x" },
    });
  });

  test("a wrong password is reported and the account is kept", async () => {
    apiFetch.mockRejectedValueOnce(new Error("Incorrect password."));
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: /delete account/i }));
    await userEvent.type(screen.getByLabelText(/enter your password/i), "nope");
    await userEvent.click(screen.getByRole("button", { name: /delete my account/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Incorrect password.");
    expect(screen.getByRole("button", { name: /keep my account/i })).toBeInTheDocument();
  });

  test("download my data fetches the export", async () => {
    globalThis.URL.createObjectURL = vi.fn(() => "blob:renest");
    globalThis.URL.revokeObjectURL = vi.fn();
    // jsdom cannot follow a download link.
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    apiFetch.mockResolvedValueOnce({ user: { email: "maya@example.com" }, listings: [] });
    renderPage();

    await userEvent.click(screen.getByRole("button", { name: /download my data/i }));

    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith("/auth/data-export"));
    expect(globalThis.URL.createObjectURL).toHaveBeenCalledTimes(1);
  });
});
