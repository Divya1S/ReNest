import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React, { useState } from "react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { apiFetch } from "../lib/api";
import { stubPushEnvironment } from "../test/pushStubs";

import { AuthProvider, useAuth } from "./AuthContext";

const USER = { id: 1, email: "maya@example.com", display_name: "Maya", campus_name: "Test U", is_staff: false };

function json(body: unknown, status = 200) {
  return Promise.resolve({ ok: status < 400, status, json: async () => body } as Response);
}

function mockAuthApi({ user = USER as typeof USER | null, log = [] as string[] } = {}) {
  return vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const url = String(input);
    log.push(`${(init?.method ?? "GET").toUpperCase()} ${url}`);
    if (url === "/api/auth/me") return json({ user });
    return json({});
  });
}

function Session() {
  const { user, loading, logout } = useAuth();
  if (loading) return <p>Checking session</p>;
  return user ? (
    <button type="button" onClick={() => logout()}>
      Log out {user.display_name}
    </button>
  ) : (
    <p>Signed out</p>
  );
}

describe("AuthProvider", () => {
  let restorePush: (() => void) | null = null;

  afterEach(() => {
    restorePush?.();
    restorePush = null;
    vi.restoreAllMocks();
  });

  test("logout removes this browser's push subscription before the session ends", async () => {
    // Regression: nothing unsubscribed on logout, so the next person to sign
    // in on a shared device kept receiving the previous user's notifications.
    const env = stubPushEnvironment({ endpoint: "https://push.example/shared-laptop" });
    restorePush = env.restore;
    const log: string[] = [];
    const spy = mockAuthApi({ log });

    render(
      <AuthProvider>
        <Session />
      </AuthProvider>,
    );
    await userEvent.click(await screen.findByRole("button", { name: /log out maya/i }));

    expect(await screen.findByText("Signed out")).toBeInTheDocument();
    const unsubscribeAt = log.indexOf("POST /api/push/unsubscribe");
    const logoutAt = log.indexOf("POST /api/auth/logout");
    expect(unsubscribeAt).toBeGreaterThan(-1);
    // The unsubscribe endpoint needs the session, so it has to come first.
    expect(logoutAt).toBeGreaterThan(unsubscribeAt);
    const unsubscribeCall = spy.mock.calls.find(([url]) => String(url) === "/api/push/unsubscribe");
    expect(JSON.parse(unsubscribeCall![1]!.body as string)).toEqual({
      endpoint: "https://push.example/shared-laptop",
    });
    expect(env.subscription!.unsubscribe).toHaveBeenCalledTimes(1);
  });

  test("logout still completes when the push clean-up fails", async () => {
    const env = stubPushEnvironment();
    restorePush = env.restore;
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url === "/api/auth/me") return json({ user: USER });
      if (url === "/api/push/unsubscribe") return Promise.reject(new TypeError("Failed to fetch"));
      return json({});
    });

    render(
      <AuthProvider>
        <Session />
      </AuthProvider>,
    );
    await userEvent.click(await screen.findByRole("button", { name: /log out maya/i }));

    expect(await screen.findByText("Signed out")).toBeInTheDocument();
    expect(env.subscription!.unsubscribe).toHaveBeenCalledTimes(1);
  });

  test("logout works without any push support", async () => {
    const log: string[] = [];
    mockAuthApi({ log });

    render(
      <AuthProvider>
        <Session />
      </AuthProvider>,
    );
    await userEvent.click(await screen.findByRole("button", { name: /log out maya/i }));

    expect(await screen.findByText("Signed out")).toBeInTheDocument();
    expect(log).toContain("POST /api/auth/logout");
    expect(log).not.toContain("POST /api/push/unsubscribe");
  });

  test("booting signed out drops a subscription left by a session that ended without logout", async () => {
    // The previous user's session expired while the browser was closed. The
    // next person to sign in here must not inherit their notifications.
    const env = stubPushEnvironment();
    restorePush = env.restore;
    const log: string[] = [];
    mockAuthApi({ user: null, log });

    render(
      <AuthProvider>
        <Session />
      </AuthProvider>,
    );

    expect(await screen.findByText("Signed out")).toBeInTheDocument();
    await waitFor(() => expect(env.subscription!.unsubscribe).toHaveBeenCalledTimes(1));
    // Nothing to authenticate with, so the server is not called.
    expect(log).not.toContain("POST /api/push/unsubscribe");
  });

  test("a failed session check leaves an existing subscription alone", async () => {
    const env = stubPushEnvironment();
    restorePush = env.restore;
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      if (String(input) === "/api/auth/me") return Promise.reject(new TypeError("Failed to fetch"));
      return json({});
    });

    render(
      <AuthProvider>
        <Session />
      </AuthProvider>,
    );

    expect(await screen.findByText("Signed out")).toBeInTheDocument();
    await act(async () => {});
    expect(env.subscription!.unsubscribe).not.toHaveBeenCalled();
  });

  test("an expired session drops the browser's subscription", async () => {
    const env = stubPushEnvironment();
    restorePush = env.restore;
    let expired = false;
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url === "/api/auth/me") return json({ user: USER });
      if (url === "/api/dashboard" && expired) {
        return json({ detail: "Authentication credentials were not provided." }, 401);
      }
      return json({});
    });

    function Expire() {
      const { user } = useAuth();
      return user ? (
        <button
          type="button"
          onClick={() => {
            expired = true;
            apiFetch("/dashboard").catch(() => undefined);
          }}
        >
          Load dashboard
        </button>
      ) : (
        <p>Signed out</p>
      );
    }

    render(
      <AuthProvider>
        <Expire />
      </AuthProvider>,
    );
    await userEvent.click(await screen.findByRole("button", { name: "Load dashboard" }));

    expect(await screen.findByText("Signed out")).toBeInTheDocument();
    await waitFor(() => expect(env.subscription!.unsubscribe).toHaveBeenCalledTimes(1));
  });

  test("the context value is stable across provider re-renders", async () => {
    // Regression: a new value object on every render re-rendered every
    // useAuth() consumer whenever the provider's parent rendered.
    mockAuthApi();
    const seen: unknown[] = [];

    const Probe = React.memo(function Probe() {
      seen.push(useAuth());
      return null;
    });

    function Parent() {
      const [tick, setTick] = useState(0);
      return (
        <div>
          <button type="button" onClick={() => setTick((value) => value + 1)}>
            Re-render parent {tick}
          </button>
          <AuthProvider>
            <Probe />
          </AuthProvider>
        </div>
      );
    }

    render(<Parent />);
    await waitFor(() => expect((seen.at(-1) as { loading: boolean }).loading).toBe(false));
    const settled = seen.length;
    const value = seen.at(-1);

    await userEvent.click(screen.getByRole("button", { name: /re-render parent/i }));
    await userEvent.click(screen.getByRole("button", { name: /re-render parent/i }));
    await act(async () => {});

    expect(seen.length).toBe(settled);
    expect(seen.at(-1)).toBe(value);
  });
});
