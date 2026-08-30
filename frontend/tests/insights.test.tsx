import "@testing-library/jest-dom/vitest";
import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { InsightsBody } from "../src/components/insights-body";
import { InsightFilters } from "../src/components/insight-filters";
import { SignalSpectrum } from "../src/components/signal-spectrum";
import type { DecisionMemo } from "../src/lib/types";

const metric = {
  aspect: "service",
  review_count: 4,
  negative_count: 3,
  negative_rate: 75,
  average_rating: 2,
  severity: 0.8,
  scoreable: true,
};

const actionableMemo: DecisionMemo = {
  id: "memo-1",
  dataset_version_id: "v1",
  decision_status: "actionable",
  decision_statement: "针对午高峰排队透明度开展小范围实验",
  topic: "服务",
  subproblem: "午高峰排队透明度",
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
  supporting_evidence: [
    { evidence_id: "support-1", rationale: "午高峰等待信息不透明" },
    { evidence_id: "support-2", rationale: "用户无法判断预计等待时间" },
  ],
  counter_evidence: [{ evidence_id: "counter-1", rationale: "部分非高峰用户体验正常" }],
  counter_evidence_checked: true,
  unknowns: ["午高峰实际等待时长"],
  reasoning_summary: "证据集中指向午高峰的信息透明度问题。",
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

describe("SignalSpectrum", () => {
  it("uses the current aspect selection flow", () => {
    const onSelectAspect = vi.fn();
    render(<SignalSpectrum metrics={[metric]} selectedAspect="" onSelectAspect={onSelectAspect} />);

    fireEvent.click(screen.getByRole("button", { name: /服务.*75.0% 负向/ }));

    expect(onSelectAspect).toHaveBeenCalledWith("service");
  });

  it("displays Chinese topic labels while preserving raw filter values", () => {
    const onChange = vi.fn();
    render(<InsightFilters aspects={["service", "food"]} value="" onChange={onChange} />);

    fireEvent.change(screen.getByLabelText("主题筛选"), { target: { value: "food" } });

    expect(screen.getByRole("option", { name: "服务" })).toHaveValue("service");
    expect(screen.getByRole("option", { name: "餐饮" })).toHaveValue("food");
    expect(onChange).toHaveBeenCalledWith("food");
  });
});

describe("InsightsBody", () => {
  it("uses the memo as the single conclusion source and opens its real evidence ids", () => {
    const onShowEvidence = vi.fn();
    render(
      <InsightsBody
        metrics={[metric]}
        insights={[{
          id: "legacy-insight",
          title: "旧洞察标题",
          claim: "不应与备忘录并列的第二套结论",
          confidence: 0.8,
          evidence_ids: ["legacy-1"],
          unknowns: [],
          recommended_action: "旧建议",
        }]}
        memo={actionableMemo}
        onShowEvidence={onShowEvidence}
      />,
    );

    expect(screen.getByText("支持证据")).toBeVisible();
    expect(screen.getByText("午高峰等待信息不透明")).toBeVisible();
    expect(screen.getByText("反例证据")).toBeVisible();
    expect(screen.queryByText("旧洞察标题")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "查看支持证据" }));
    fireEvent.click(screen.getByRole("button", { name: "查看反例证据" }));

    expect(onShowEvidence).toHaveBeenNthCalledWith(1, ["support-1", "support-2"]);
    expect(onShowEvidence).toHaveBeenNthCalledWith(2, ["counter-1"]);
  });

  it("honestly explains when there are no verifiable insights", () => {
    render(<InsightsBody metrics={[metric]} insights={[]} onShowEvidence={vi.fn()} />);

    expect(screen.getByText("尚未形成可验证洞察")).toBeVisible();
    expect(screen.getByText(/先审阅主题信号与原始证据/)).toBeVisible();
  });
});
