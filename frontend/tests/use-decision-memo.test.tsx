import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useDecisionMemo } from "../src/hooks/use-decision-memo";
import type { DecisionMemo, MemoGenerationJob, ModelHealth } from "../src/lib/types";

const mocks = vi.hoisted(() => ({
  getDecisionMemo: vi.fn(),
  getDecisionMemoJob: vi.fn(),
  getModelHealth: vi.fn(),
  startDecisionMemo: vi.fn(),
}));

vi.mock("../src/lib/api", () => ({
  getDecisionMemo: mocks.getDecisionMemo,
  getDecisionMemoJob: mocks.getDecisionMemoJob,
  getModelHealth: mocks.getModelHealth,
  startDecisionMemo: mocks.startDecisionMemo,
}));

type Deferred<T> = {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason?: unknown) => void;
};

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((onResolve, onReject) => {
    resolve = onResolve;
    reject = onReject;
  });
  return { promise, resolve, reject };
}

const health: ModelHealth = {
  configured: true,
  ready: true,
  provider: "ollama",
  model_name: "qwen-local",
  error_code: null,
};

function memoFor(version: string): DecisionMemo {
  return {
    id: `memo-${version}`,
    dataset_version_id: version,
    decision_status: "needs_evidence",
    decision_statement: `${version} 暂不建议采取业务动作`,
    topic: "服务",
    subproblem: null,
    facts: {
      review_count: 4,
      negative_count: 3,
      negative_rate: 75,
      average_rating: 2,
      severity: 75,
      affected: 100,
      evidence: 40,
      opportunity_score: 78.3,
      scoreable: true,
      missing_fields: [],
    },
    supporting_evidence: [{ evidence_id: `${version}-support`, rationale: "支持理由" }],
    counter_evidence: [{ evidence_id: `${version}-counter`, rationale: "反例理由" }],
    counter_evidence_checked: true,
    unknowns: ["实际等待时长"],
    reasoning_summary: "现有证据仍需补充。",
    experiment: null,
    evidence_plan: {
      candidate_subproblems: ["排队透明度"],
      collection_fields: ["实际等待时长"],
      minimum_evidence_per_subproblem: 3,
      reassessment_condition: "补齐证据后重评",
    },
    refusal_reason: null,
    model_name: "qwen-local",
    prompt_version: "decision-memo-v1",
  };
}

function jobFor(
  version: string,
  status: MemoGenerationJob["status"] = "queued",
): MemoGenerationJob {
  return {
    id: `job-${version}`,
    dataset_version_id: version,
    status,
    memo_id: status === "completed" ? `memo-${version}` : null,
    error_code: status === "failed" ? "MEMO_GENERATION_FAILED" : null,
    created_at: "2026-08-10T09:00:00",
    updated_at: "2026-08-10T09:00:01",
  };
}

beforeEach(() => {
  mocks.getDecisionMemo.mockReset();
  mocks.getDecisionMemoJob.mockReset();
  mocks.getModelHealth.mockReset();
  mocks.startDecisionMemo.mockReset();
  mocks.getDecisionMemo.mockResolvedValue(null);
  mocks.getModelHealth.mockResolvedValue(health);
  mocks.startDecisionMemo.mockResolvedValue(jobFor("v1"));
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useDecisionMemo version isolation", () => {
  it("clears the visible snapshot immediately and ignores a slow refresh from the previous dataset", async () => {
    const v1 = deferred<DecisionMemo | null>();
    const v2 = deferred<DecisionMemo | null>();
    mocks.getDecisionMemo.mockImplementation((version: string) => version === "v1" ? v1.promise : v2.promise);

    const { result, rerender } = renderHook(
      ({ version }: { version: string | undefined }) => useDecisionMemo(version),
      { initialProps: { version: "v1" as string | undefined } },
    );

    rerender({ version: "v2" });
    expect(result.current.memo).toBeNull();
    expect(result.current.job).toBeNull();
    expect(result.current.health).toBeNull();
    expect(result.current.error).toBeNull();
    expect(result.current.loading).toBe(true);

    await act(async () => {
      v2.resolve(memoFor("v2"));
      await v2.promise;
    });
    await waitFor(() => expect(result.current.memo?.dataset_version_id).toBe("v2"));

    await act(async () => {
      v1.resolve(memoFor("v1"));
      await v1.promise;
    });
    expect(result.current.memo?.dataset_version_id).toBe("v2");
    expect((mocks.getDecisionMemo.mock.calls[0][1] as AbortSignal).aborted).toBe(true);

    rerender({ version: undefined });
    expect(result.current.memo).toBeNull();
    expect(result.current.job).toBeNull();
    expect(result.current.health).toBeNull();
    expect(result.current.error).toBeNull();
    expect(result.current.loading).toBe(false);
  });

  it("ignores a generate response that belongs to the previous dataset", async () => {
    const generation = deferred<MemoGenerationJob>();
    mocks.startDecisionMemo.mockReturnValue(generation.promise);

    const { result, rerender } = renderHook(
      ({ version }) => useDecisionMemo(version),
      { initialProps: { version: "v1" } },
    );
    await act(async () => { await Promise.resolve(); });

    act(() => { void result.current.generate(); });
    rerender({ version: "v2" });

    await act(async () => {
      generation.resolve(jobFor("v1"));
      await generation.promise;
    });
    expect(result.current.job).toBeNull();
    expect(result.current.memo).toBeNull();
  });

  it("does not let an old completed job or its delayed memo fetch write into the new dataset", async () => {
    vi.useFakeTimers();
    const completedMemo = deferred<DecisionMemo | null>();
    let v1Reads = 0;
    mocks.getDecisionMemo.mockImplementation((version: string) => {
      if (version === "v2") return Promise.resolve(memoFor("v2"));
      v1Reads += 1;
      return v1Reads === 1 ? Promise.resolve(null) : completedMemo.promise;
    });
    mocks.getDecisionMemoJob.mockResolvedValue(jobFor("v1", "completed"));
    mocks.startDecisionMemo.mockResolvedValue(jobFor("v1"));

    const { result, rerender } = renderHook(
      ({ version }) => useDecisionMemo(version),
      { initialProps: { version: "v1" } },
    );
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await result.current.generate(); });
    await act(async () => {
      vi.advanceTimersByTime(800);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(mocks.getDecisionMemoJob).toHaveBeenCalledTimes(1);
    expect(v1Reads).toBe(2);

    rerender({ version: "v2" });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(result.current.memo?.dataset_version_id).toBe("v2");

    await act(async () => {
      completedMemo.resolve(memoFor("v1"));
      await completedMemo.promise;
    });
    act(() => { vi.advanceTimersByTime(1_600); });
    expect(result.current.memo?.dataset_version_id).toBe("v2");
    expect(result.current.job).toBeNull();
    expect(mocks.getDecisionMemoJob).toHaveBeenCalledTimes(1);
  });

  it("ignores a completed poll response that arrives only after the dataset has changed", async () => {
    vi.useFakeTimers();
    const latePoll = deferred<MemoGenerationJob>();
    mocks.getDecisionMemo.mockImplementation((version: string) => Promise.resolve(version === "v2" ? memoFor("v2") : null));
    mocks.getDecisionMemoJob.mockReturnValue(latePoll.promise);

    const { result, rerender } = renderHook(
      ({ version }) => useDecisionMemo(version),
      { initialProps: { version: "v1" } },
    );
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await result.current.generate(); });
    await act(async () => {
      vi.advanceTimersByTime(800);
      await Promise.resolve();
    });
    expect(mocks.getDecisionMemoJob).toHaveBeenCalledTimes(1);

    rerender({ version: "v2" });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(result.current.memo?.dataset_version_id).toBe("v2");

    await act(async () => {
      latePoll.resolve(jobFor("v1", "completed"));
      await latePoll.promise;
    });
    expect(result.current.memo?.dataset_version_id).toBe("v2");
    expect(result.current.job).toBeNull();
    expect(mocks.getDecisionMemo).toHaveBeenCalledTimes(2);
  });

  it("waits 800ms after each poll settles, never overlaps, and stops after unmount", async () => {
    vi.useFakeTimers();
    const firstPoll = deferred<MemoGenerationJob>();
    const secondPoll = deferred<MemoGenerationJob>();
    mocks.getDecisionMemoJob
      .mockReturnValueOnce(firstPoll.promise)
      .mockReturnValueOnce(secondPoll.promise);

    const { result, unmount } = renderHook(() => useDecisionMemo("v1"));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await result.current.generate(); });

    await act(async () => {
      vi.advanceTimersByTime(800);
      await Promise.resolve();
    });
    expect(mocks.getDecisionMemoJob).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(2_400);
      await Promise.resolve();
    });
    expect(mocks.getDecisionMemoJob).toHaveBeenCalledTimes(1);

    await act(async () => {
      firstPoll.resolve(jobFor("v1", "grouping_evidence"));
      await firstPoll.promise;
    });
    vi.advanceTimersByTime(799);
    expect(mocks.getDecisionMemoJob).toHaveBeenCalledTimes(1);
    await act(async () => {
      vi.advanceTimersByTime(1);
      await Promise.resolve();
    });
    expect(mocks.getDecisionMemoJob).toHaveBeenCalledTimes(2);

    unmount();
    await act(async () => {
      secondPoll.resolve(jobFor("v1", "grouping_evidence"));
      await secondPoll.promise;
      vi.advanceTimersByTime(1_600);
    });
    expect(mocks.getDecisionMemoJob).toHaveBeenCalledTimes(2);
  });
});
