import { afterEach, describe, expect, it, vi } from "vitest";

import { getEvidence } from "../src/lib/api";
import type { Evidence } from "../src/lib/types";

function evidence(id: string): Evidence {
  return {
    id,
    dataset_version_id: "v1",
    content: `content-${id}`,
    source_type: "review",
    rating: null,
    aspect: null,
    sentiment: "unknown",
    relevance_score: null,
    redacted: false,
  };
}

function successfulResponse(items: Evidence[]): Response {
  return {
    ok: true,
    json: async () => items,
  } as Response;
}

describe("getEvidence", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("deduplicates IDs, requests at most eight per batch, and preserves first-requested order", async () => {
    const requestedBatches: string[][] = [];
    const fetchMock = vi.fn(async (input: string | URL | Request) => {
      const url = new URL(String(input));
      const ids = url.searchParams.getAll("evidence_id");
      requestedBatches.push(ids);
      return successfulResponse(ids.toReversed().map(evidence));
    });
    vi.stubGlobal("fetch", fetchMock);

    const items = await getEvidence("v1", ["e9", "e1", "e2", "e3", "e4", "e5", "e6", "e7", "e8", "e9", "e10"]);

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(requestedBatches.every((batch) => batch.length <= 8)).toBe(true);
    expect(requestedBatches.flat()).toEqual(["e9", "e1", "e2", "e3", "e4", "e5", "e6", "e7", "e8", "e10"]);
    expect(items.map((item) => item.id)).toEqual(["e9", "e1", "e2", "e3", "e4", "e5", "e6", "e7", "e8", "e10"]);

    for (const [url] of fetchMock.mock.calls) {
      expect(new URL(String(url)).searchParams.get("dataset_version_id")).toBe("v1");
    }
  });

  it("returns an empty list without fetching when no IDs are requested", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await expect(getEvidence("v1", [])).resolves.toEqual([]);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects the whole request when any batch fails", async () => {
    const fetchMock = vi.fn(async (input: string | URL | Request) => {
      const ids = new URL(String(input)).searchParams.getAll("evidence_id");
      return ids.includes("e9")
        ? ({ ok: false, json: async () => [] } as unknown as Response)
        : successfulResponse(ids.map(evidence));
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(getEvidence("v1", ["e1", "e2", "e3", "e4", "e5", "e6", "e7", "e8", "e9"])).rejects.toThrow();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("passes the same AbortSignal to every batch", async () => {
    const fetchMock = vi.fn(async (input: string | URL | Request) => {
      const ids = new URL(String(input)).searchParams.getAll("evidence_id");
      return successfulResponse(ids.map(evidence));
    });
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    await getEvidence("v1", ["e1", "e2", "e3", "e4", "e5", "e6", "e7", "e8", "e9"], controller.signal);

    expect(fetchMock).toHaveBeenCalledTimes(2);
    for (const [, init] of fetchMock.mock.calls) {
      expect(init).toMatchObject({ cache: "no-store", signal: controller.signal });
    }
  });
});
