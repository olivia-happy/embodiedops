import "@testing-library/jest-dom/vitest";
import React from "react";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { DecisionMemo } from "../src/lib/types";

const memo: DecisionMemo = {
  id: "memo-demo",
  dataset_version_id: "v1",
  decision_status: "actionable",
  decision_statement: "只读展示结论",
  topic: "服务",
  subproblem: "等待时间",
  facts: { review_count: 4, negative_count: 3, negative_rate: 75, average_rating: 2, severity: 75, affected: 100, evidence: 40, opportunity_score: 78.3, scoreable: true, missing_fields: [] },
  supporting_evidence: [{ evidence_id: "r1", rationale: "证据" }],
  counter_evidence: [],
  counter_evidence_checked: true,
  unknowns: [],
  reasoning_summary: "总结",
  experiment: {
    hypothesis: "假设",
    target_segment: "用户",
    intervention: "干预",
    primary_metric: "满意度",
    guardrail_metric: "投诉率",
    duration_days: 14,
    stop_conditions: ["停止条件"],
  },
  evidence_plan: null,
  refusal_reason: null,
  model_name: "qwen3.5:9b",
  prompt_version: "decision-memo-v1",
};

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

describe("demo read-only build", () => {
  it("hides generation and experiment actions while preserving evidence", async () => {
    vi.stubEnv("NEXT_PUBLIC_DEMO_READ_ONLY", "true");
    const { DecisionMemoPanel } = await import("../src/components/decision-memo-panel");
    render(<DecisionMemoPanel memo={memo} job={null} health={null} loading={false} error={null} onGenerate={vi.fn()} onRefresh={vi.fn()} onShowEvidence={vi.fn()} />);

    expect(screen.getByText("只读演示模式")).toBeVisible();
    expect(screen.queryByRole("button", { name: "生成决策备忘录" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "创建实验" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "查看支持证据" })).toBeEnabled();
  });

  it("hides retry after a failed generation in the public build", async () => {
    vi.stubEnv("NEXT_PUBLIC_DEMO_READ_ONLY", "true");
    const { DecisionMemoPanel } = await import("../src/components/decision-memo-panel");
    render(<DecisionMemoPanel memo={null} job={{ id: "job-1", dataset_version_id: "v1", status: "failed", memo_id: null, error_code: "LOCAL_MODEL_TIMEOUT", created_at: "2026-08-10T09:00:00Z", updated_at: "2026-08-10T09:00:01Z" }} health={null} loading={false} error={null} onGenerate={vi.fn()} onRefresh={vi.fn()} onShowEvidence={vi.fn()} />);

    expect(screen.getByText("LOCAL_MODEL_TIMEOUT")).toBeVisible();
    expect(screen.queryByRole("button", { name: "重新生成" })).not.toBeInTheDocument();
  });
});
