import { afterEach, describe, expect, it, vi } from "vitest";

import { getOverview } from "../src/lib/api";
import { GET } from "../src/app/api/[...path]/route";

describe("same-origin API client", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("uses the same-origin proxy when no public API base is configured", async () => {
    const fetchMock = vi.fn(async (input: string | URL | Request) => {
      expect(String(input)).toBe("/api/v1/overview");
      return { ok: true, json: async () => ({}) } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);

    await getOverview();

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("same-origin API proxy", () => {
  const originalOrigin = process.env.SIGNALFORGE_API_ORIGIN;

  afterEach(() => {
    vi.unstubAllGlobals();
    if (originalOrigin === undefined) delete process.env.SIGNALFORGE_API_ORIGIN;
    else process.env.SIGNALFORGE_API_ORIGIN = originalOrigin;
  });

  it("forwards an approved path and query to the fixed configured origin", async () => {
    vi.stubEnv("SIGNALFORGE_API_ORIGIN", "http://api.internal:8000");
    const fetchMock = vi.fn(async (input: string | URL | Request) => {
      expect(String(input)).toBe("http://api.internal:8000/api/v1/overview?view=full");
      return new Response(JSON.stringify({ ok: true }), { status: 200, headers: { "content-type": "application/json" } });
    });
    vi.stubGlobal("fetch", fetchMock);

    const response = await GET(new Request("http://demo.test/api/v1/overview?view=full"), { params: Promise.resolve({ path: ["v1", "overview"] }) });

    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toContain("application/json");
    await expect(response.json()).resolves.toEqual({ ok: true });
  });

  it("rejects an unapproved path and ignores a user-supplied absolute URL", async () => {
    vi.stubEnv("SIGNALFORGE_API_ORIGIN", "http://api.internal:8000");
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const response = await GET(new Request("http://demo.test/api/https://attacker.test/v1/overview"), { params: Promise.resolve({ path: ["https:", "attacker.test", "v1", "overview"] }) });

    expect(response.status).toBe(404);
    await expect(response.json()).resolves.toMatchObject({ error: "NOT_FOUND" });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
