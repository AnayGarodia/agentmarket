/**
 * Tiny stub fetch used across SDK tests. Routes by `${method} ${pathname}` and lets
 * each route return either a fixed response or a function over (url, init, callIndex).
 */

export interface RouteContext {
  url: URL;
  method: string;
  body: unknown;
  callIndex: number;
  callIndexForRoute: number;
}

export type RouteResponder =
  | { status?: number; body: unknown; headers?: Record<string, string> }
  | ((ctx: RouteContext) => Promise<RouteResponseInit> | RouteResponseInit);

export interface RouteResponseInit {
  status?: number;
  body: unknown;
  headers?: Record<string, string>;
}

export interface StubFetchOptions {
  baseUrl?: string;
  routes: Record<string, RouteResponder>;
  onUnknown?: (key: string, url: URL) => RouteResponseInit | void;
}

export interface StubFetch {
  fetch: typeof fetch;
  calls: Array<{ method: string; path: string; body: unknown }>;
  callsFor(method: string, path: string): Array<{ body: unknown }>;
}

const TEXT_DECODER = new TextDecoder();

export function makeStubFetch(options: StubFetchOptions): StubFetch {
  const calls: StubFetch["calls"] = [];
  const callsPerRoute = new Map<string, number>();
  let totalCalls = 0;

  const fetchFn: typeof fetch = async (input, init) => {
    const url = typeof input === "string" ? new URL(input) : new URL((input as URL | Request).toString());
    const method = (init?.method ?? "GET").toUpperCase();
    const key = `${method} ${url.pathname}`;

    let body: unknown = undefined;
    if (init?.body !== undefined && init.body !== null) {
      const raw = typeof init.body === "string" ? init.body : TEXT_DECODER.decode(init.body as Uint8Array);
      try {
        body = raw.length > 0 ? JSON.parse(raw) : undefined;
      } catch {
        body = raw;
      }
    }

    calls.push({ method, path: url.pathname, body });
    const callIndex = totalCalls++;
    const perRouteIndex = callsPerRoute.get(key) ?? 0;
    callsPerRoute.set(key, perRouteIndex + 1);

    const responder = options.routes[key];
    let resolved: RouteResponseInit | undefined;
    if (typeof responder === "function") {
      resolved = await responder({
        url,
        method,
        body,
        callIndex,
        callIndexForRoute: perRouteIndex,
      });
    } else if (responder) {
      resolved = responder;
    }
    if (!resolved) {
      const fallback = options.onUnknown?.(key, url);
      if (fallback) {
        resolved = fallback;
      } else {
        return new Response(JSON.stringify({ detail: `unknown route ${key}` }), {
          status: 404,
          headers: { "content-type": "application/json" },
        });
      }
    }
    const status = resolved.status ?? 200;
    const responseHeaders = {
      "content-type": "application/json",
      ...(resolved.headers ?? {}),
    };
    const payload = typeof resolved.body === "string" ? resolved.body : JSON.stringify(resolved.body);
    return new Response(payload, { status, headers: responseHeaders });
  };

  return {
    fetch: fetchFn,
    calls,
    callsFor(method, path) {
      return calls.filter((c) => c.method === method && c.path === path).map((c) => ({ body: c.body }));
    },
  };
}

/** Convenience: a JobResponse-shaped object with sensible defaults. */
export function jobResponse(overrides: Record<string, unknown>): Record<string, unknown> {
  return {
    job_id: "job_test",
    agent_id: "agent_test",
    status: "pending",
    input_payload: {},
    output_payload: null,
    price_cents: 0,
    max_attempts: 3,
    attempts: 0,
    created_at: "2026-05-15T00:00:00Z",
    updated_at: "2026-05-15T00:00:00Z",
    ...overrides,
  };
}
