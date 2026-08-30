import "@testing-library/jest-dom/vitest";
import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import DecisionsPage from "../src/app/decisions/page";
import { DecisionForm } from "../src/components/decision-form";
import { DecisionResult } from "../src/components/decision-result";
import { ScoreBreakdown } from "../src/components/score-breakdown";
import type { DecisionCard, DecisionMemo, InsightsResponse } from "../src/lib/types";

const mocks = vi.hoisted(() => ({
  createDecision: vi.fn(),
  getDecisionMemo: vi.fn(),
  getInsights: vi.fn(),
}));

vi.mock("next/navigation", () => ({ usePathname: () => "/decisions" }));
vi.mock("../src/lib/api", () => ({
  createDecision: mocks.createDecision,
  getDecisionMemo: mocks.getDecisionMemo,
  getInsights: mocks.getInsights,
}));

const insightsResponse: InsightsResponse = {
  active_dataset: { id: "v1", source_name: "demo.csv", row_count: 4, imported_at: "2026-08-10T09:00:00", is_stale: false },
  metrics: [],
  insights: [],
};

const supportingEvidence = Array.from({ length: 10 }, (_, index) => ({
  evidence_id: `support-${index + 1}`,
  rationale: `支持理由 ${index + 1}`,
}));

const actionableMemo: DecisionMemo = {
  id: "memo-actionable",
  dataset_version_id: "v1",
  decision_status: "actionable",
  decision_statement: "验证午高峰排队信息透明度是否改善服务体验",
  topic: "服务",
  subproblem: "午高峰排队透明度",
  facts: { review_count: 4, negative_count: 3, negative_rate: 75, average_rating: 2, severity: 75, affected: 100, evidence: 40, opportunity_score: 78.3, scoreable: true, missing_fields: [] },
  supporting_evidence: supportingEvidence,
  counter_evidence: [{ evidence_id: "counter-1", rationale: "非高峰体验正常" }],
  counter_evidence_checked: true,
  unknowns: [],
  reasoning_summary: "证据集中于午高峰。",
  experiment: {
    hypothesis: "展示预计等待时间可改善服务体验",
    target_segment: "午高峰到店用户",
    intervention: "展示实时排队进度",
    primary_metric: "服务满意度",
    guardrail_metric: "投诉率",
    duration_days: 14,
    stop_conditions: ["护栏指标持续恶化时停止"],
  },
  evidence_plan: null,
  refusal_reason: null,
  model_name: "qwen-local",
  prompt_version: "decision-memo-v1",
};

const createdCard: DecisionCard = {
  id: "decision-created",
  dataset_version_id: "v1",
  title: "人工录入实验",
  evidence_ids: ["manual-1", "manual-2"],
  problem_statement: "人工确认的问题",
  hypothesis: "人工确认的假设",
  primary_metric: "转化率",
  guardrail_metric: "退款率",
  owner: "王明",
  due_date: "2026-08-20",
  score: 80,
  score_breakdown: {},
  status: "draft",
};

beforeEach(() => {
  mocks.createDecision.mockReset();
  mocks.getDecisionMemo.mockReset();
  mocks.getInsights.mockReset();
  mocks.getInsights.mockResolvedValue(insightsResponse);
});

describe("ScoreBreakdown", () => {
  it("renders all opportunity-score contributions", () => {
    render(<ScoreBreakdown contributions={{ affected: 30, negativity: 20, severity: 14, business_fit: 9, evidence: 9 }} total={82} />);
    expect(screen.getByText("82.0 / 100")).toBeVisible();
  });

  it("opens Trace while retaining the decision evidence", () => {
    const onOpenTrace = vi.fn();
    render(<DecisionResult card={{ id: "decision-1", dataset_version_id: "v1", title: "改善交付体验", evidence_ids: ["review-1"], problem_statement: "交付时间过长", hypothesis: "缩短审批链路", primary_metric: "交付时长", guardrail_metric: "投诉率", owner: "王明", due_date: "2026-08-20", score: 82, score_breakdown: { evidence: 20 }, status: "draft" }} onOpenTrace={onOpenTrace} />);

    expect(screen.getByText("数据版本：v1")).toBeVisible();
    expect(screen.getByText("review-1")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "查看 Trace" }));
    expect(onOpenTrace).toHaveBeenCalledOnce();
  });
});

describe("DecisionsPage memo prefill", () => {
  it("prefills only an actionable experiment, limits evidence to eight, and keeps human ownership blank", async () => {
    mocks.getDecisionMemo.mockResolvedValue(actionableMemo);

    render(<DecisionsPage />);

    expect(await screen.findByLabelText("标题")).toHaveValue("午高峰排队透明度");
    expect(screen.getByLabelText("问题陈述")).toHaveValue(actionableMemo.decision_statement);
    expect(screen.getByLabelText("假设")).toHaveValue(actionableMemo.experiment?.hypothesis);
    expect(screen.getByLabelText("主指标")).toHaveValue(actionableMemo.experiment?.primary_metric);
    expect(screen.getByLabelText("护栏指标")).toHaveValue(actionableMemo.experiment?.guardrail_metric);
    expect(screen.getByLabelText("证据 ID（逗号分隔）")).toHaveValue(supportingEvidence.slice(0, 8).map((item) => item.evidence_id).join(", "));
    expect(screen.getByText("为符合接口上限，已预填前 8 条支持证据；另有 2 条未自动带入。")).toBeVisible();
    expect(screen.getByLabelText("负责人")).toHaveValue("");
    expect(screen.getByLabelText("截止日期")).toHaveValue("");
  });

  it.each(["needs_evidence", "refused"] as const)("does not prefill a %s memo", async (decisionStatus) => {
    mocks.getDecisionMemo.mockResolvedValue({
      ...actionableMemo,
      id: `memo-${decisionStatus}`,
      decision_status: decisionStatus,
      experiment: decisionStatus === "needs_evidence" ? null : actionableMemo.experiment,
      evidence_plan: decisionStatus === "needs_evidence" ? {
        candidate_subproblems: ["午高峰排队透明度"],
        collection_fields: ["实际等待时长"],
        minimum_evidence_per_subproblem: 3,
        reassessment_condition: "补齐证据后重评",
      } : null,
      refusal_reason: decisionStatus === "refused" ? "COUNTER_EVIDENCE_NOT_CHECKED" : null,
    });

    render(<DecisionsPage />);

    expect(await screen.findByLabelText("标题")).toHaveValue("");
    expect(screen.getByLabelText("证据 ID（逗号分隔）")).toHaveValue("");
    expect(screen.getByLabelText("假设")).toHaveValue("");
  });
});

describe("DecisionForm", () => {
  it("retains fully manual creation when no memo defaults are supplied", async () => {
    mocks.createDecision.mockResolvedValue(createdCard);
    const onCreated = vi.fn();
    render(<DecisionForm datasetVersionId="v1" onCreated={onCreated} />);

    expect(screen.getByLabelText("标题").closest("form")).toHaveAttribute("id", "experiment-form");
    fireEvent.change(screen.getByLabelText("标题"), { target: { value: "人工录入实验" } });
    fireEvent.change(screen.getByLabelText("证据 ID（逗号分隔）"), { target: { value: "manual-1, manual-2" } });
    fireEvent.change(screen.getByLabelText("问题陈述"), { target: { value: "人工确认的问题" } });
    fireEvent.change(screen.getByLabelText("假设"), { target: { value: "人工确认的假设" } });
    fireEvent.change(screen.getByLabelText("主指标"), { target: { value: "转化率" } });
    fireEvent.change(screen.getByLabelText("护栏指标"), { target: { value: "退款率" } });
    fireEvent.change(screen.getByLabelText("负责人"), { target: { value: "王明" } });
    fireEvent.change(screen.getByLabelText("截止日期"), { target: { value: "2026-08-20" } });
    fireEvent.click(screen.getByRole("button", { name: "创建并评分" }));

    await waitFor(() => expect(mocks.createDecision).toHaveBeenCalledWith({
      dataset_version_id: "v1",
      title: "人工录入实验",
      evidence_ids: ["manual-1", "manual-2"],
      problem_statement: "人工确认的问题",
      hypothesis: "人工确认的假设",
      primary_metric: "转化率",
      guardrail_metric: "退款率",
      owner: "王明",
      due_date: "2026-08-20",
      business_fit: 70,
    }));
    expect(onCreated).toHaveBeenCalledWith(createdCard);
  });
});
