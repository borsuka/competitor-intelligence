/**
 * API access.
 *
 * Two entry points, because the two runtimes have different constraints:
 *
 * - {@link serverFetch} runs in server components and route handlers. It forwards the
 *   incoming request's cookies to the API and never caches, since every response is
 *   tenant-scoped.
 * - {@link clientFetch} runs in the browser. It sends credentials and echoes the CSRF
 *   cookie in a header.
 *
 * The session itself is never handled here: access and refresh tokens live in HttpOnly
 * cookies that this code cannot read, which is the point.
 */

import type { ApiError } from "@/lib/types";

/** Browser-visible API origin. Server code prefers the internal one. */
export const PUBLIC_API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** Inside Docker the API is reachable at a service name, not at localhost. */
const INTERNAL_API_URL = process.env.API_URL ?? PUBLIC_API_URL;

export const CSRF_COOKIE = "sentinel_csrf";
export const CSRF_HEADER = "X-CSRF-Token";

export class ApiClientError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details?: Record<string, unknown>;
  readonly requestId?: string;

  constructor(
    status: number,
    code: string,
    message: string,
    details?: Record<string, unknown>,
    requestId?: string,
  ) {
    super(message);
    this.name = "ApiClientError";
    this.status = status;
    this.code = code;
    this.details = details;
    this.requestId = requestId;
  }

  /**
   * Field-level validation messages, when the API returned any.
   *
   * A 422 carries `details.fields`, which is far more useful than the generic summary —
   * "The part after the @-sign is a reserved name" tells a user what to change.
   */
  get fieldErrors(): { field: string; message: string }[] {
    const fields = this.details?.fields;
    return Array.isArray(fields) ? (fields as { field: string; message: string }[]) : [];
  }

  /** The most specific message available: field errors first, summary otherwise. */
  get displayMessage(): string {
    const fields = this.fieldErrors;
    if (fields.length === 0) return this.message;
    return fields.map((entry) => entry.message).join(" ");
  }

  /** True when the session is missing or expired and the user should sign in again. */
  get isUnauthenticated(): boolean {
    return this.status === 401;
  }

  get isNotFound(): boolean {
    return this.status === 404;
  }
}

async function toError(response: Response): Promise<ApiClientError> {
  let code = "http_error";
  let message = "Something went wrong.";
  let details: Record<string, unknown> | undefined;
  let requestId: string | undefined;

  try {
    const body = (await response.json()) as ApiError;
    if (body?.error) {
      code = body.error.code ?? code;
      message = body.error.message ?? message;
      details = body.error.details;
      requestId = body.error.request_id;
    }
  } catch {
    // A non-JSON error body (a proxy timeout page, say) leaves the defaults in place.
  }

  return new ApiClientError(response.status, code, message, details, requestId);
}

/** Connection-level failures worth one retry: the request never reached the server. */
function isTransientConnectionError(error: unknown): boolean {
  const cause = (error as { cause?: { code?: string } } | undefined)?.cause;
  const code = cause?.code ?? (error as { code?: string } | undefined)?.code;
  return ["ECONNRESET", "ECONNREFUSED", "EPIPE", "ETIMEDOUT", "UND_ERR_SOCKET"].includes(
    String(code),
  );
}

interface FetchOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
  /** Opt in to Next's data cache. Off by default: everything here is tenant data. */
  revalidate?: number;
}

async function request<T>(
  baseUrl: string,
  path: string,
  options: FetchOptions,
  extraHeaders: Record<string, string>,
): Promise<T> {
  const { body, revalidate, ...init } = options;

  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (body !== undefined) headers.set("Content-Type", "application/json");
  for (const [key, value] of Object.entries(extraHeaders)) headers.set(key, value);

  const requestInit: RequestInit = {
    ...init,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    ...(revalidate === undefined
      ? { cache: "no-store" as const }
      : { next: { revalidate } }),
  };

  const method = (init.method ?? "GET").toUpperCase();
  const isIdempotent = ["GET", "HEAD", "OPTIONS"].includes(method);

  let response: Response;
  try {
    response = await fetch(`${baseUrl}${path}`, requestInit);
  } catch (error) {
    // Node keeps HTTP connections alive longer than uvicorn does, so the first request
    // after an idle gap can land on a socket the server has already closed. Retrying a
    // safe method once turns a 500 page into a hiccup; anything with side effects is
    // rethrown, because a retried POST could duplicate the work it did.
    if (!isIdempotent || !isTransientConnectionError(error)) throw error;
    response = await fetch(`${baseUrl}${path}`, requestInit);
  }

  if (!response.ok) throw await toError(response);
  if (response.status === 204) return undefined as T;

  return (await response.json()) as T;
}

/**
 * Server-side fetch with the caller's cookies attached.
 *
 * Server components run per request, so the cookie store is the signed-in user's.
 */
export async function serverFetch<T>(path: string, options: FetchOptions = {}): Promise<T> {
  const { cookies } = await import("next/headers");
  const cookieStore = await cookies();
  const cookieHeader = cookieStore
    .getAll()
    .map((cookie) => `${cookie.name}=${cookie.value}`)
    .join("; ");

  const headers: Record<string, string> = {};
  if (cookieHeader) headers.Cookie = cookieHeader;

  // Unsafe methods still need the CSRF header, even server-side: the API applies the
  // same rule to every caller.
  const method = (options.method ?? "GET").toUpperCase();
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    const csrf = cookieStore.get(CSRF_COOKIE)?.value;
    if (csrf) headers[CSRF_HEADER] = csrf;
  }

  return request<T>(INTERNAL_API_URL, path, options, headers);
}

function readCookie(name: string): string | undefined {
  if (typeof document === "undefined") return undefined;
  const match = document.cookie.match(
    new RegExp(`(?:^|;\\s*)${name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}=([^;]*)`),
  );
  return match?.[1] ? decodeURIComponent(match[1]) : undefined;
}

/** Browser fetch: sends cookies and the double-submit CSRF header. */
export async function clientFetch<T>(path: string, options: FetchOptions = {}): Promise<T> {
  const headers: Record<string, string> = {};
  const method = (options.method ?? "GET").toUpperCase();
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    const csrf = readCookie(CSRF_COOKIE);
    if (csrf) headers[CSRF_HEADER] = csrf;
  }

  return request<T>(PUBLIC_API_URL, path, { ...options, credentials: "include" }, headers);
}

/** Path builders — one place to change when a route moves. */
export const api = {
  auth: {
    me: "/api/v1/auth/me",
    login: "/api/v1/auth/login",
    register: "/api/v1/auth/register",
    logout: "/api/v1/auth/logout",
    refresh: "/api/v1/auth/refresh",
    passwordReset: "/api/v1/auth/password-reset",
    passwordResetConfirm: "/api/v1/auth/password-reset/confirm",
    changePassword: "/api/v1/auth/password",
  },
  // Not organization-scoped: the invitee is not a member yet.
  invitations: { accept: "/api/v1/orgs/invitations/accept" },
  org: (orgId: string) => ({
    root: `/api/v1/orgs/${orgId}`,
    dashboard: `/api/v1/orgs/${orgId}/dashboard`,
    usage: `/api/v1/orgs/${orgId}/usage`,
    members: `/api/v1/orgs/${orgId}/members`,
    invitations: `/api/v1/orgs/${orgId}/invitations`,
    competitors: `/api/v1/orgs/${orgId}/competitors`,
    competitor: (id: string) => `/api/v1/orgs/${orgId}/competitors/${id}`,
    analyze: (id: string) => `/api/v1/orgs/${orgId}/competitors/${id}/analyze`,
    analyses: (id: string) => `/api/v1/orgs/${orgId}/competitors/${id}/analyses`,
    pricing: (id: string) => `/api/v1/orgs/${orgId}/competitors/${id}/pricing`,
    scores: (id: string) => `/api/v1/orgs/${orgId}/competitors/${id}/scores`,
    competitorChanges: (id: string) => `/api/v1/orgs/${orgId}/competitors/${id}/changes`,
    changes: `/api/v1/orgs/${orgId}/changes`,
    acknowledgeChange: (id: string) => `/api/v1/orgs/${orgId}/changes/${id}/acknowledge`,
    job: (id: string) => `/api/v1/orgs/${orgId}/jobs/${id}`,
    comparisons: `/api/v1/orgs/${orgId}/comparisons`,
    comparison: (id: string) => `/api/v1/orgs/${orgId}/comparisons/${id}`,
    alerts: `/api/v1/orgs/${orgId}/alerts`,
    alert: (id: string) => `/api/v1/orgs/${orgId}/alerts/${id}`,
    notifications: `/api/v1/orgs/${orgId}/notifications`,
    unreadCount: `/api/v1/orgs/${orgId}/notifications/unread-count`,
    markRead: `/api/v1/orgs/${orgId}/notifications/read`,
    reports: `/api/v1/orgs/${orgId}/reports`,
    report: (id: string) => `/api/v1/orgs/${orgId}/reports/${id}`,
    search: `/api/v1/orgs/${orgId}/search`,
  }),
};

/** Query-string helper that drops empty values instead of sending `?q=undefined`. */
export function withQuery(path: string, params: Record<string, unknown>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    if (Array.isArray(value)) {
      for (const item of value) search.append(key, String(item));
    } else {
      search.set(key, String(value));
    }
  }
  const query = search.toString();
  return query ? `${path}?${query}` : path;
}
