const ALLOWED_ROOTS = new Set(["v1", "healthz"]);
const FORWARDED_REQUEST_HEADERS = ["accept", "content-type"] as const;

type RouteContext = { params: Promise<{ path: string[] }> };

function jsonError(status: number, error: string): Response {
  return Response.json({ error }, { status });
}

function upstreamOrigin(): URL {
  const configuredOrigin = process.env.SIGNALFORGE_API_ORIGIN ?? "http://localhost:8000";
  const origin = new URL(configuredOrigin);
  if (!/^https?:$/.test(origin.protocol)) throw new Error("invalid API origin");
  origin.pathname = "/";
  origin.search = "";
  origin.hash = "";
  return origin;
}

async function proxy(request: Request, context: RouteContext): Promise<Response> {
  const { path } = await context.params;
  const segments = Array.isArray(path) ? path : [];
  if (segments.length === 0 || !ALLOWED_ROOTS.has(segments[0])) return jsonError(404, "NOT_FOUND");

  let target: URL;
  try {
    target = upstreamOrigin();
  } catch {
    return jsonError(500, "PROXY_CONFIG_INVALID");
  }
  const upstreamSegments = segments[0] === "v1" ? ["api", ...segments] : segments;
  target.pathname = `/${upstreamSegments.map((segment) => encodeURIComponent(segment)).join("/")}`;
  target.search = new URL(request.url).search;

  const headers = new Headers();
  for (const headerName of FORWARDED_REQUEST_HEADERS) {
    const value = request.headers.get(headerName);
    if (value) headers.set(headerName, value);
  }

  const method = request.method.toUpperCase();
  const init: RequestInit = { method, headers, redirect: "error" };
  if (method !== "GET" && method !== "HEAD" && method !== "OPTIONS") {
    init.body = await request.arrayBuffer();
  }

  let upstream: Response;
  try {
    upstream = await fetch(target, init);
  } catch {
    return jsonError(502, "UPSTREAM_UNAVAILABLE");
  }

  const responseHeaders = new Headers();
  const contentType = upstream.headers.get("content-type");
  if (contentType) responseHeaders.set("content-type", contentType);
  return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
}

export function GET(request: Request, context: RouteContext): Promise<Response> {
  return proxy(request, context);
}

export function HEAD(request: Request, context: RouteContext): Promise<Response> {
  return proxy(request, context);
}

export function POST(request: Request, context: RouteContext): Promise<Response> {
  return proxy(request, context);
}

export function OPTIONS(request: Request, context: RouteContext): Promise<Response> {
  return proxy(request, context);
}
