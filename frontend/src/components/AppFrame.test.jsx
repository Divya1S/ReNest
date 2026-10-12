import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import React from "react";
import { createMemoryRouter, Link, RouterProvider } from "react-router-dom";
import { toast } from "sonner";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { AuthContext } from "../context/AuthContext";
import { clearApiCache } from "../lib/apiCache";

import AppFrame from "./AppFrame";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));
// The concierge is a lazy chunk with its own tests; keep it out of the shell's.
vi.mock("./concierge/ConciergeWidget", () => ({ default: () => null }));

const USER = { id: 1, display_name: "Maya Chen", email: "maya@example.com", campus_name: "Test U", email_verified: true };

function authValue(overrides = {}) {
  return {
    user: null,
    loading: false,
    authNotice: "",
    clearAuthNotice: () => {},
    refreshUser: async () => null,
    login: async () => USER,
    register: async () => ({ user: USER }),
    logout: vi.fn(async () => {}),
    ...overrides,
  };
}

function PageOne() {
  return (
    <div>
      <h1>Page one</h1>
      <Link to="/two">Go to page two</Link>
      <Link to="/one?filter=free">Filter page one</Link>
    </div>
  );
}

function renderFrame(auth = authValue(), initialEntries = ["/one"]) {
  const router = createMemoryRouter(
    [
      {
        element: <AppFrame />,
        children: [
          { path: "/one", element: <PageOne /> },
          { path: "/two", element: <h1>Page two</h1> },
        ],
      },
    ],
    { initialEntries },
  );
  const view = render(
    <AuthContext.Provider value={auth}>
      <RouterProvider router={router} />
    </AuthContext.Provider>,
  );
  return { router, ...view };
}

describe("AppFrame", () => {
  beforeEach(() => {
    clearApiCache();
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe() {}
        disconnect() {}
      },
    );
    window.scrollTo = vi.fn();
    vi.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ unread_count: 0, count: 0, results: [] }),
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    vi.clearAllMocks();
  });

  describe("route changes", () => {
    test("the first load leaves focus alone", async () => {
      renderFrame();
      await screen.findByRole("heading", { name: "Page one" });

      expect(screen.getByRole("main")).not.toHaveFocus();
      expect(document.body).toHaveFocus();
    });

    test("navigating to another page moves focus to the main landmark and resets scroll", async () => {
      // Regression: focus stayed on the clicked link and the window kept the
      // previous page's scroll offset.
      renderFrame();
      const link = await screen.findByRole("link", { name: "Go to page two" });
      link.focus();

      fireEvent.click(link);

      await screen.findByRole("heading", { name: "Page two" });
      const main = screen.getByRole("main");
      expect(main).toHaveAttribute("id", "main-content");
      await waitFor(() => expect(main).toHaveFocus());
      expect(window.scrollTo).toHaveBeenCalledWith(0, 0);
    });

    test("a query-only change on the same page does not move focus", async () => {
      const { router } = renderFrame();
      const link = await screen.findByRole("link", { name: "Filter page one" });
      link.focus();

      fireEvent.click(link);

      await waitFor(() => expect(router.state.location.search).toBe("?filter=free"));
      expect(screen.getByRole("main")).not.toHaveFocus();
      expect(link).toHaveFocus();
    });

    test("going back restores the scroll position the page was left at", async () => {
      const { router } = renderFrame();
      const link = await screen.findByRole("link", { name: "Go to page two" });

      Object.defineProperty(window, "scrollY", { configurable: true, value: 640 });
      fireEvent.click(link);
      await screen.findByRole("heading", { name: "Page two" });
      Object.defineProperty(window, "scrollY", { configurable: true, value: 0 });
      window.scrollTo.mockClear();

      await act(async () => {
        await router.navigate(-1);
      });

      await screen.findByRole("heading", { name: "Page one" });
      expect(window.scrollTo).toHaveBeenCalledWith(0, 640);
    });
  });

  describe("navigation while the session is still being checked", () => {
    test("shows neither the guest nor the signed-in controls", async () => {
      // Regression: only `user` was read, so every hard reload flashed
      // "Log In / Join" at someone who was signed in.
      renderFrame(authValue({ user: null, loading: true }));
      await screen.findByRole("heading", { name: "Page one" });

      const banner = screen.getByRole("banner");
      expect(within(banner).queryByRole("link", { name: "Log In" })).not.toBeInTheDocument();
      expect(within(banner).queryByRole("link", { name: "Join" })).not.toBeInTheDocument();
      expect(within(banner).queryByRole("button", { name: /log out/i })).not.toBeInTheDocument();
      expect(within(banner).getByRole("status")).toHaveTextContent("Checking your session");
      // Only the destination both audiences share.
      const nav = within(banner).getByRole("navigation", { name: "Main navigation" });
      expect(within(nav).getAllByRole("link").map((node) => node.textContent)).toEqual(["Browse"]);
    });

    test("a guest gets Log In and Join once the check has finished", async () => {
      renderFrame(authValue({ user: null, loading: false }));
      await screen.findByRole("heading", { name: "Page one" });

      const banner = screen.getByRole("banner");
      expect(within(banner).getByRole("link", { name: "Log In" })).toHaveAttribute("href", "/login");
      expect(within(banner).getByRole("link", { name: "Join" })).toHaveAttribute("href", "/register");
      expect(within(banner).queryByRole("status")).not.toBeInTheDocument();
    });

    test("a signed-in user gets their navigation and a named Log Out button", async () => {
      renderFrame(authValue({ user: USER, loading: false }));
      await screen.findByRole("heading", { name: "Page one" });

      const banner = screen.getByRole("banner");
      expect(within(banner).getByText("Maya Chen")).toBeInTheDocument();
      expect(within(banner).getByRole("button", { name: "Log Out" })).toBeInTheDocument();
      expect(within(banner).getByRole("link", { name: "Dashboard" })).toBeInTheDocument();
      expect(within(banner).queryByRole("link", { name: "Log In" })).not.toBeInTheDocument();
    });
  });

  describe("logging out", () => {
    test("a failed logout is reported instead of being an unhandled rejection", async () => {
      const unhandled = vi.fn();
      globalThis.process.on("unhandledRejection", unhandled);
      const logout = vi.fn(() => Promise.reject(new Error("Request timed out.")));
      renderFrame(authValue({ user: USER, logout }));
      const button = within(await screen.findByRole("banner")).getByRole("button", { name: "Log Out" });

      fireEvent.click(button);

      await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Request timed out."));
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(logout).toHaveBeenCalledTimes(1);
      // Called as an action, not handed the click event.
      expect(logout.mock.calls[0]).toHaveLength(0);
      expect(button).not.toBeDisabled();
      expect(unhandled).not.toHaveBeenCalled();
      globalThis.process.off("unhandledRejection", unhandled);
    });
  });
});
