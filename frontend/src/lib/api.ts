export interface ApiFetchOptions extends Omit<RequestInit, "body" | "headers" | "signal"> {
  /** Accepts a plain object (JSON-serialised automatically), FormData, or raw BodyInit. */
  body?: FormData | Record<string, unknown> | unknown[] | BodyInit | null;
  headers?: Record<string, string>;
  signal?: AbortSignal;
  /** Milliseconds before the request is aborted. Set to 0 to disable. Default 15 000. */
  timeout?: number;
}

interface AuthEventHandlers {
  onSessionExpired: ((ctx: { message: string; path: string }) => void) | null;
}

function getCookie(name: string): string {
  const cookie = document.cookie
    .split("; ")
    .find((part) => part.startsWith(`${name}=`));
  return cookie ? decodeURIComponent(cookie.split("=")[1]) : "";
}

let authEventHandlers: AuthEventHandlers = { onSessionExpired: null };

function isAuthFailure(response: Response, data: Record<string, unknown>): boolean {
  const detail = `${data?.detail || ""}`.toLowerCase();
  if (response.status === 401) return true;
  if (response.status !== 403) return false;
  return [
    "authentication credentials were not provided",
    "csrf",
    "not authenticated",
    "session",
  ].some((fragment) => detail.includes(fragment));
}

export class ApiError extends Error {
  readonly status: number | undefined;
  readonly data: unknown;
  readonly isAuthError: boolean;

  constructor(
    message: string,
    { status, data, isAuthError = false }: { status?: number; data?: unknown; isAuthError?: boolean } = {},
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.data = data;
    this.isAuthError = isAuthError;
  }
}

export function setApiAuthHandlers(handlers: Partial<AuthEventHandlers>): void {
  authEventHandlers = { ...authEventHandlers, ...handlers };
}

const DEFAULT_TIMEOUT_MS = 15_000;

/**
 * Typed fetch wrapper for the ReNest API.
 *
 * Path MUST start with "/" and MUST NOT include "/api/" — the prefix is
 * prepended internally. Example: apiFetch("/listings") → GET /api/listings
 */
export async function apiFetch<T = unknown>(
  path: string,
  options: ApiFetchOptions = {},
): Promise<T> {
  const { body, headers, signal: callerSignal, timeout = DEFAULT_TIMEOUT_MS, ...rest } = options;
  const method = (rest.method ?? "GET").toUpperCase();
  const finalHeaders: Record<string, string> = { ...(headers ?? {}) };

  const timeoutController = new AbortController();
  let timedOut = false;
  const timeoutId =
    timeout > 0
      ? setTimeout(() => {
          timedOut = true;
          timeoutController.abort();
        }, timeout)
      : null;

  callerSignal?.addEventListener("abort", () => timeoutController.abort(), {
    once: true,
    signal: timeoutController.signal,
  });

  const config: RequestInit = {
    credentials: "include",
    ...rest,
    signal: timeoutController.signal,
    headers: finalHeaders,
  };

  if (body instanceof FormData) {
    config.body = body;
  } else if (typeof body === "string") {
    // Already-serialised payload — send as-is (never double-stringify).
    if (!finalHeaders["Content-Type"]) finalHeaders["Content-Type"] = "application/json";
    config.body = body;
  } else if (body !== undefined && body !== null) {
    finalHeaders["Content-Type"] = "application/json";
    config.body = JSON.stringify(body);
  }

  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    finalHeaders["X-CSRFToken"] = getCookie("csrftoken");
  }

  try {
    const response = await fetch(`/api${path}`, config);
    const data: Record<string, unknown> =
      response.status === 204
        ? {}
        : await response.json().catch(() => ({}));

    if (!response.ok) {
      const message =
        (data?.detail as string) ||
        Object.values(data ?? {}).flat().join(" ") ||
        "Request failed.";
      const authError = isAuthFailure(response, data) && !path.startsWith("/auth/");
      if (authError && authEventHandlers.onSessionExpired) {
        authEventHandlers.onSessionExpired({
          message: "Your session expired. Please sign in again.",
          path: `${window.location.pathname}${window.location.search}${window.location.hash}`,
        });
      }
      throw new ApiError(message, { status: response.status, data, isAuthError: authError });
    }

    return data as T;
  } catch (err) {
    if (timedOut) {
      throw new ApiError("Request timed out. Check your connection and try again.", {
        status: 408,
      });
    }
    throw err;
  } finally {
    if (timeoutId !== null) clearTimeout(timeoutId);
  }
}

export function toLocalDateTimeInput(value: string | null | undefined): string {
  if (!value) return "";
  const date = new Date(value);
  const offset = date.getTimezoneOffset();
  const local = new Date(date.getTime() - offset * 60 * 1000);
  return local.toISOString().slice(0, 16);
}

/**
 * Normalise a list response to a bare array.
 *
 * Every DRF generic list endpoint returns the paginated envelope
 * `{count, next, previous, results}` (global PAGE_SIZE 24); custom APIViews
 * return bare arrays. Consumers that render "all of it" should go through
 * this so a paginated envelope can never reach `.map()` — the exact crash
 * class that hit the listing-updates panel.
 */
export function asResults<T = unknown>(data: unknown): T[] {
  if (Array.isArray(data)) return data as T[];
  const results = (data as { results?: unknown })?.results;
  return Array.isArray(results) ? (results as T[]) : [];
}
