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

const CSRF_FETCH_TIMEOUT_MS = 10_000;
let csrfRequest: Promise<void> | null = null;

/**
 * Ask the server to set the `csrftoken` cookie. Concurrent callers share one
 * request, so a login submitted while the auth bootstrap is still fetching the
 * cookie waits for that same response instead of racing it.
 */
export function fetchCsrfCookie(): Promise<void> {
  if (!csrfRequest) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), CSRF_FETCH_TIMEOUT_MS);
    csrfRequest = fetch("/api/auth/csrf", { credentials: "include", signal: controller.signal })
      .then(
        () => undefined,
        () => undefined,
      )
      .finally(() => {
        clearTimeout(timer);
        csrfRequest = null;
      });
  }
  return csrfRequest;
}

/**
 * The CSRF token for an unsafe request. Login, register and password reset
 * are reachable before any other request has run, so when the cookie is not
 * there yet it is fetched first rather than sending an empty header.
 */
async function resolveCsrfToken(): Promise<string> {
  const existing = getCookie("csrftoken");
  if (existing) return existing;
  await fetchCsrfCookie();
  return getCookie("csrftoken");
}

function collectErrorMessages(value: unknown): string[] {
  if (value === null || value === undefined) return [];
  if (typeof value === "string") return value.trim() ? [value] : [];
  if (typeof value === "number" || typeof value === "boolean") return [String(value)];
  if (Array.isArray(value)) return value.flatMap(collectErrorMessages);
  if (typeof value === "object") return Object.values(value).flatMap(collectErrorMessages);
  return [];
}

/**
 * One readable sentence from a DRF error payload. Handles the flat
 * `{field: ["msg"]}` shape as well as nested serializers and lists of objects
 * (`{keys: {p256dh: ["msg"]}}`), which a plain `.flat().join()` rendered as
 * "[object Object]".
 */
export function flattenErrorMessage(data: unknown, fallback = "Request failed."): string {
  const detail = (data as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  const messages = collectErrorMessages(data);
  return messages.length ? messages.join(" ") : fallback;
}

/** Keys of a DRF error payload that describe the request, not one field. */
const NON_FIELD_KEYS = new Set(["detail", "non_field_errors", "code", "status_code"]);

/**
 * Per-field messages from a DRF validation payload: `{title: "Too short."}`.
 * Nested values are flattened into the top-level field they belong to.
 */
export function extractFieldErrors(data: unknown): Record<string, string> {
  if (!data || typeof data !== "object" || Array.isArray(data)) return {};
  const result: Record<string, string> = {};
  for (const [field, value] of Object.entries(data as Record<string, unknown>)) {
    if (NON_FIELD_KEYS.has(field)) continue;
    const messages = collectErrorMessages(value);
    if (messages.length) result[field] = messages.join(" ");
  }
  return result;
}

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

  /** Server validation messages keyed by field name; empty when there are none. */
  get fieldErrors(): Record<string, string> {
    return extractFieldErrors(this.data);
  }
}

export function setApiAuthHandlers(handlers: Partial<AuthEventHandlers>): void {
  authEventHandlers = { ...authEventHandlers, ...handlers };
}

const DEFAULT_TIMEOUT_MS = 15_000;
const UPLOAD_TIMEOUT_MS = 120_000;

/**
 * Typed fetch wrapper for the ReNest API.
 *
 * Path MUST start with "/" and MUST NOT include "/api/"; the prefix is
 * prepended internally. Example: apiFetch("/listings") → GET /api/listings
 */
export async function apiFetch<T = unknown>(
  path: string,
  options: ApiFetchOptions = {},
): Promise<T> {
  const { body, headers, signal: callerSignal, ...rest } = options;
  // Multipart bodies are photo uploads: several megabytes on a phone uplink,
  // then server-side image compression. The 15s default aborts those while the
  // server is still working, so the user sees a timeout for an upload that
  // actually succeeds.
  const isUpload = body instanceof FormData;
  const timeout = options.timeout ?? (isUpload ? UPLOAD_TIMEOUT_MS : DEFAULT_TIMEOUT_MS);
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
    // Already-serialised payload: send as-is (never double-stringify).
    if (!finalHeaders["Content-Type"]) finalHeaders["Content-Type"] = "application/json";
    config.body = body;
  } else if (body !== undefined && body !== null) {
    finalHeaders["Content-Type"] = "application/json";
    config.body = JSON.stringify(body);
  }

  try {
    if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
      finalHeaders["X-CSRFToken"] = await resolveCsrfToken();
    }

    const response = await fetch(`/api${path}`, config);
    const data: Record<string, unknown> =
      response.status === 204
        ? {}
        : await response.json().catch(() => ({}));

    if (!response.ok) {
      const message = flattenErrorMessage(data);
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
 * this so a paginated envelope can never reach `.map()`, the exact crash
 * class that hit the listing-updates panel.
 */
export function asResults<T = unknown>(data: unknown): T[] {
  if (Array.isArray(data)) return data as T[];
  const results = (data as { results?: unknown })?.results;
  return Array.isArray(results) ? (results as T[]) : [];
}
