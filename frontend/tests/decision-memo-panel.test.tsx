import "@testing-library/jest-dom/vitest";
import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DecisionMemoPanel } from "../src/components/decision-memo-panel";
import type { DecisionMemo, MemoGenerationJob, ModelHealth } from "../src/lib/types";

const health: ModelHealth = {
  configured: true,
  ready: true,
  provider: "ollama",
  model_name: "qwen-local",
  error_code: null,
};

const needsEvidenceMemo: DecisionMemo = {
  id: "memo-1",
  dataset_version_id: "v1",
  decision_status: "needs_evidence",
  decision_statement: "暂不建议采取业务动作",
  topic: "服务",
  subproblem: null,
  facts: { review_count: 4, negative_count: 3, negative_rate: 75, average_rating: 2, severity: 75, affected: 100, evidence: 40, opportunity_score: 78.3, scoreable: true, missing_fields: [] },
  supporting_evidence: [{ evidence_id: "r1", rationale: "排队问题" }],
  counter_evidence: [{ evidence_id: "r4", rationale: "已有正向体验" }],
  counter_evidence_checked: true,
  unknowns: ["午高峰等待时长"],
  reasoning_summary: "现有证据分散。",
  experiment: null,
  evidence_plan: { candidate_subproblems: ["排队透明度"], collection_fields: ["午高峰等待时长"], minimum_evidence_per_subproblem: 3, reassessment_condition: "每个候选子问题至少 3 条独立证据" },
  refusal_reason: null,
  model_name: "qwen-local",
  prompt_version: "decision-memo-v1",
};

const actionableMemo: DecisionMemo = {
  ...needsEvidenceMemo,
  id: "memo-actionable",
  decision_status: "actionable",
  decision_statement: "验证午高峰排队透明度是否改善服务体验",
  subproblem: "午高峰排队透明度",
  experiment: {
    hypothesis: "展示预计等待时间可改善服务体验",
    target_segment: "午高峰到店用户",
    intervention: "展示实时排队进度与预计等待时间",
    primary_metric: "服务满意度",
    guardrail_metric: "投诉率",
    duration_days: 14,
    stop_conditions: ["护栏指标持续恶化时停止", "数据质量不足时暂停"],
  },
  evidence_plan: null,
};

const baseProps = {
  health,
  loading: false,
  error: null,
  onGenerate: vi.fn(),
  onRefresh: vi.fn(),
  onShowEvidence: vi.fn(),
};

describe("DecisionMemoPanel", () => {
  it("shows a needs-evidence plan without an experiment action", () => {
    render(<DecisionMemoPanel memo={needsEvidenceMemo} job={null} {...baseProps} />);

    expect(screen.getAllByText("暂不建议采取业务动作")[0]).toBeVisible();
    expect(screen.getAllByText("每个候选子问题至少 3 条独立证据").length).toBeGreaterThan(0);
    expect(screen.getByText("排队透明度")).toBeVisible();
    expect(screen.getByText("排队问题")).toBeVisible();
    expect(screen.queryByRole("button", { name: "创建实验" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "创建实验" })).not.toBeInTheDocument();
  });

  it("keeps a refused memo non-actionable and surfaces an incomplete counter-evidence audit", () => {
    render(<DecisionMemoPanel memo={{ ...needsEvidenceMemo, decision_status: "refused", decision_statement: "不输出行动结论", evidence_plan: null, refusal_reason: "COUNTER_EVIDENCE_NOT_CHECKED", counter_evidence_checked: false }} job={null} {...baseProps} />);

    expect(screen.getByText("暂不形成业务结论")).toBeVisible();
    expect(screen.getByText("反例审计未完成：当前结论仅用于拒绝或继续核查。")).toBeVisible();
    expect(screen.queryByRole("button", { name: "创建实验" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "创建实验" })).not.toBeInTheDocument();
  });

  it("shows the server stage and sends real evidence ids to the drawer", () => {
    const onShowEvidence = vi.fn();
    render(<DecisionMemoPanel memo={null} job={{ id: "job-1", dataset_version_id: "v1", status: "validating_evidence", memo_id: null, error_code: null, created_at: "2026-08-10T09:00:00", updated_at: "2026-08-10T09:00:01" }} {...baseProps} onShowEvidence={onShowEvidence} />);

    expect(screen.getAllByText("正在验证证据")[0]).toBeVisible();
    render(<DecisionMemoPanel memo={needsEvidenceMemo} job={null} {...baseProps} onShowEvidence={onShowEvidence} />);
    fireEvent.click(screen.getByRole("button", { name: "查看支持证据" }));
    expect(onShowEvidence).toHaveBeenCalledWith(["r1"]);
  });

  it("links an actionable memo to the experiment form and shows the full falsifiable plan", () => {
    render(<DecisionMemoPanel memo={actionableMemo} job={null} {...baseProps} />);

    expect(screen.getByText("午高峰排队透明度")).toBeVisible();
    expect(screen.getByText("午高峰到店用户")).toBeVisible();
    expect(screen.getByText("展示实时排队进度与预计等待时间")).toBeVisible();
    expect(screen.getByText("服务满意度")).toBeVisible();
    expect(screen.getByText("投诉率")).toBeVisible();
    expect(screen.getByText("14 天")).toBeVisible();
    expect(screen.getByText("护栏指标持续恶化时停止")).toBeVisible();
    expect(screen.getByText("数据质量不足时暂停")).toBeVisible();
    expect(screen.getByRole("link", { name: "创建实验" })).toHaveAttribute("href", "/decisions#experiment-form");
    expect(screen.queryByRole("button", { name: "创建实验" })).not.toBeInTheDocument();
  });

  it("does not open an empty evidence drawer and explains missing citations", () => {
    const onShowEvidence = vi.fn();
    render(<DecisionMemoPanel memo={{ ...actionableMemo, supporting_evidence: [], counter_evidence: [] }} job={null} {...baseProps} onShowEvidence={onShowEvidence} />);

    expect(screen.getByText("未引用支持证据")).toBeVisible();
    expect(screen.getByText("未发现反例证据")).toBeVisible();
    const supportButton = screen.getByRole("button", { name: "查看支持证据" });
    const counterButton = screen.getByRole("button", { name: "查看反例证据" });
    expect(supportButton).toBeDisabled();
    expect(counterButton).toBeDisabled();
    fireEvent.click(supportButton);
    fireEvent.click(counterButton);
    expect(onShowEvidence).not.toHaveBeenCalled();
  });

  it("surfaces a safe failed-job code without assuming every failure is model availability", () => {
    const failedJob: MemoGenerationJob = {
      id: "job-failed",
      dataset_version_id: "v1",
      status: "failed",
      memo_id: null,
      error_code: "SERVICE_RESTARTED",
      created_at: "2026-08-10T09:00:00",
      updated_at: "2026-08-10T09:00:01",
    };
    render(<DecisionMemoPanel memo={null} job={failedJob} {...baseProps} />);

    expect(screen.getByText("生成服务在任务执行期间重启，请重新生成。")).toBeVisible();
    expect(screen.getByText("SERVICE_RESTARTED")).toBeVisible();
    expect(screen.queryByText("请确认本地模型服务后再试。")).not.toBeInTheDocument();
  });

  it("lets the user recheck local-model health without starting generation", () => {
    const onRefresh = vi.fn();
    const onGenerate = vi.fn();
    render(<DecisionMemoPanel memo={null} job={null} {...baseProps} health={{ ...health, ready: false }} onRefresh={onRefresh} onGenerate={onGenerate} />);

    fireEvent.click(screen.getByRole("button", { name: "重新检查本地模型" }));
    expect(onRefresh).toHaveBeenCalledOnce();
    expect(onGenerate).not.toHaveBeenCalled();
  });
});
