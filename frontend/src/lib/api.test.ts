import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { ApiError, apiFetch, extractFieldErrors, fetchCsrfCookie, flattenErrorMessage } from "./api";

function clearCsrfCookie() {
  document.cookie = "csrftoken=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/";
}

function jsonResponse(body: unknown, status = 200) {
  return { ok: status < 400, status, json: async () => body } as Response;
}

describe("flattenErrorMessage", () => {
  test("prefers a string detail", () => {
    expect(flattenErrorMessage({ detail: "Not allowed.", title: ["ignored"] })).toBe("Not allowed.");
  });

  test("joins flat field errors", () => {
    expect(flattenErrorMessage({ title: ["Too short."], pickup_zone: ["Required."] })).toBe(
      "Too short. Required.",
    );
  });

  test("flattens nested serializers and lists of objects instead of printing [object Object]", () => {
    const message = flattenErrorMessage({
      keys: { p256dh: ["This field is required."], auth: ["Too long."] },
      slots: [{}, { start: ["Must be in the future."] }],
    });
    expect(message).toBe("This field is required. Too long. Must be in the future.");
    expect(message).not.toContain("[object Object]");
  });

  test("handles a non-string detail and an empty payload", () => {
    expect(flattenErrorMessage({ detail: { reason: ["Listing is gone."] } })).toBe("Listing is gone.");
    expect(flattenErrorMessage({})).toBe("Request failed.");
    expect(flattenErrorMessage(null)).toBe("Request failed.");
  });
});

describe("extractFieldErrors", () => {
  test("keys messages by top-level field and skips request-level keys", () => {
    expect(
      extractFieldErrors({
        detail: "Invalid input.",
        non_field_errors: ["Dates overlap."],
        title: ["Too short.", "No emoji."],
        keys: { auth: ["Required."] },
      }),
    ).toEqual({ title: "Too short. No emoji.", keys: "Required." });
  });

  test("returns an empty object for payloads that carry no fields", () => {
    expect(extractFieldErrors(null)).toEqual({});
    expect(extractFieldErrors(["Nope."])).toEqual({});
    expect(extractFieldErrors({ detail: "Nope." })).toEqual({});
  });
});

describe("apiFetch errors", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  test("throws an ApiError that keeps the status, the structured data and per-field messages", async () => {
    const payload = { title: ["Too short."], gallery: [{ image: ["Unsupported type."] }] };
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(payload, 400));

    const error = (await apiFetch("/listings", { method: "POST", body: {} }).catch((e) => e)) as ApiError;

    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(400);
    expect(error.data).toEqual(payload);
    expect(error.message).toBe("Too short. Unsupported type.");
    expect(error.fieldErrors).toEqual({ title: "Too short.", gallery: "Unsupported type." });
  });
});

describe("CSRF bootstrap", () => {
  beforeEach(() => {
    clearCsrfCookie();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    clearCsrfCookie();
  });

  test("an unsafe request made before the cookie exists fetches it first and sends the token", async () => {
    const calls: string[] = [];
    const spy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      calls.push(url);
      if (url === "/api/auth/csrf") {
        document.cookie = "csrftoken=fresh-token; path=/";
        return jsonResponse({});
      }
      return jsonResponse({ user: { id: 1 } });
    });

    await apiFetch("/auth/login", { method: "POST", body: { email: "a@b.c", password: "x" } });

    expect(calls).toEqual(["/api/auth/csrf", "/api/auth/login"]);
    const loginInit = spy.mock.calls[1][1] as RequestInit;
    expect((loginInit.headers as Record<string, string>)["X-CSRFToken"]).toBe("fresh-token");
  });

  test("a login submitted while the boot-time CSRF request is in flight waits for it", async () => {
    let releaseCsrf: () => void = () => undefined;
    const calls: string[] = [];
    const spy = vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      calls.push(url);
      if (url === "/api/auth/csrf") {
        return new Promise<Response>((resolve) => {
          releaseCsrf = () => {
            document.cookie = "csrftoken=boot-token; path=/";
            resolve(jsonResponse({}));
          };
        });
      }
      return Promise.resolve(jsonResponse({ user: { id: 1 } }));
    });

    const boot = fetchCsrfCookie(); // what AuthProvider does on mount
    const login = apiFetch("/auth/login", { method: "POST", body: {} });
    await Promise.resolve();

    // One shared CSRF request, and the login has not been sent yet.
    expect(calls).toEqual(["/api/auth/csrf"]);

    releaseCsrf();
    await Promise.all([boot, login]);

    expect(calls).toEqual(["/api/auth/csrf", "/api/auth/login"]);
    const loginInit = spy.mock.calls[1][1] as RequestInit;
    expect((loginInit.headers as Record<string, string>)["X-CSRFToken"]).toBe("boot-token");
  });

  test("safe requests and requests with a cookie never trigger the bootstrap", async () => {
    const spy = vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse({}));

    await apiFetch("/listings");
    expect(spy).toHaveBeenCalledTimes(1);
    expect(String(spy.mock.calls[0][0])).toBe("/api/listings");

    document.cookie = "csrftoken=already-here; path=/";
    await apiFetch("/auth/logout", { method: "POST", body: {} });
    expect(spy.mock.calls.map(([url]) => String(url))).toEqual(["/api/listings", "/api/auth/logout"]);
  });

  test("a failed CSRF request does not block the call: the server gets to reject it", async () => {
    const spy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      if (String(input) === "/api/auth/csrf") throw new TypeError("Failed to fetch");
      return jsonResponse({ detail: "CSRF Failed: CSRF token missing." }, 403);
    });

    const error = (await apiFetch("/auth/login", { method: "POST", body: {} }).catch((e) => e)) as ApiError;

    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(403);
    expect(spy).toHaveBeenCalledTimes(2);
  });
});
