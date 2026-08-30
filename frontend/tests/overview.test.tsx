import "@testing-library/jest-dom/vitest";
import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import OverviewPage from "../src/app/page";
import type { DecisionMemo, Evidence, OverviewResponse } from "../src/lib/types";
import { OpportunityCard } from "../src/components/opportunity-card";

const mocks = vi.hoisted(() => ({
  getOverview: vi.fn(),
  getEvidence: vi.fn(),
  useDecisionMemo: vi.fn(),
  generate: vi.fn(),
}));

vi.mock("next/navigation", () => ({ usePathname: () => "/" }));
vi.mock("../src/lib/api", () => ({ getOverview: mocks.getOverview, getEvidence: mocks.getEvidence }));
vi.mock("../src/hooks/use-decision-memo", () => ({ useDecisionMemo: mocks.useDecisionMemo }));

const opportunity = {
  id: "service",
  title: "缩短服务响应时间",
  dataset_version_id: "v1",
  aspect: "服务",
  review_count: 4,
  negative_count: 3,
  negative_rate: 75,
  score: 82.5,
  scoreable: true,
  missing_fields: [],
  evidence_count: 2,
  evidence_ids: ["review-1", "review-2"],
  contributions: { negative_rate: 52.5, review_count: 20, business_fit: 10 },
};

const overview: OverviewResponse = {
  active_dataset: { id: "v1", source_name: "demo.csv", row_count: 4, imported_at: "2026-08-10T09:00:00", is_stale: false },
  summary_metrics: { review_count: 4, negative_review_count: 3, negative_review_rate: 75, topic_count: 1, market_event_count: 0 },
  opportunities: [opportunity],
  risks: [],
};

const needsEvidenceMemo: DecisionMemo = {
  id: "memo-v1",
  dataset_version_id: "v1",
  decision_status: "needs_evidence",
  decision_statement: "暂不建议采取业务动作",
  topic: "服务",
  subproblem: null,
  facts: { review_count: 4, negative_count: 3, negative_rate: 75, average_rating: 2, severity: 75, affected: 100, evidence: 40, opportunity_score: 78.3, scoreable: true, missing_fields: [] },
  supporting_evidence: [{ evidence_id: "support-1", rationale: "支持理由" }],
  counter_evidence: [{ evidence_id: "counter-1", rationale: "反例理由" }],
  counter_evidence_checked: true,
  unknowns: ["实际等待时长"],
  reasoning_summary: "当前证据不足以形成行动结论。",
  experiment: null,
  evidence_plan: { candidate_subproblems: ["排队透明度"], collection_fields: ["实际等待时长"], minimum_evidence_per_subproblem: 3, reassessment_condition: "补齐证据后重评" },
  refusal_reason: null,
  model_name: "qwen-local",
  prompt_version: "decision-memo-v1",
};

const supportEvidence: Evidence = { id: "support-1", dataset_version_id: "v1", content: "支持证据原文", source_type: "review", rating: 1, aspect: "服务", sentiment: "negative", relevance_score: 0.9, redacted: false };
const counterEvidence: Evidence = { id: "counter-1", dataset_version_id: "v1", content: "反例证据原文", source_type: "review", rating: 5, aspect: "服务", sentiment: "positive", relevance_score: 0.8, redacted: false };

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((onResolve) => { resolve = onResolve; });
  return { promise, resolve };
}

describe("OverviewPage", () => {
  beforeEach(() => {
    mocks.getOverview.mockResolvedValue(overview);
    mocks.getEvidence.mockResolvedValue([]);
    mocks.generate.mockReset();
    mocks.useDecisionMemo.mockReturnValue({ memo: null, job: null, health: { configured: true, ready: true, provider: "ollama", model_name: "qwen-local", error_code: null }, loading: false, error: null, generate: mocks.generate, retry: mocks.generate, refresh: vi.fn() });
  });

  it("把备忘录作为唯一结论，并把规则评分降级为候选信号", async () => {
    render(<OverviewPage />);

    expect(await screen.findByText("AI 决策备忘录")).toBeVisible();
    expect(screen.getByText("缩短服务响应时间")).toBeVisible();
    expect(screen.getByText("主题覆盖")).toBeVisible();
    expect(screen.getByText("当前数据中的主题数量")).toBeVisible();
    expect(screen.queryByText("可行动主题")).not.toBeInTheDocument();
    expect(screen.getByText("规则评分候选信号")).toBeVisible();
    expect(screen.getByText("待验证主题")).toBeVisible();
    expect(screen.getByText(/规则评分基线，不构成备忘录之外的第二结论/)).toBeVisible();
    expect(screen.queryByText("确定性机会基线")).not.toBeInTheDocument();
    expect(screen.queryByText("行动队列")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "生成决策备忘录" }));
    expect(mocks.generate).toHaveBeenCalledOnce();
  });

  it("keeps the latest evidence group when an older request resolves later and cancels writes after close", async () => {
    const first = deferred<Evidence[]>();
    const second = deferred<Evidence[]>();
    const afterClose = deferred<Evidence[]>();
    mocks.getEvidence
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise)
      .mockReturnValueOnce(afterClose.promise);
    mocks.useDecisionMemo.mockReturnValue({
      memo: needsEvidenceMemo,
      job: null,
      health: { configured: true, ready: true, provider: "ollama", model_name: "qwen-local", error_code: null },
      loading: false,
      error: null,
      generate: mocks.generate,
      retry: mocks.generate,
      refresh: vi.fn(),
    });

    render(<OverviewPage />);
    await screen.findByText("规则评分候选信号");

    fireEvent.click(screen.getByRole("button", { name: "查看支持证据" }));
    fireEvent.click(screen.getByRole("button", { name: "查看反例证据" }));
    expect(mocks.getEvidence).toHaveBeenNthCalledWith(1, "v1", ["support-1"]);
    expect(mocks.getEvidence).toHaveBeenNthCalledWith(2, "v1", ["counter-1"]);

    await act(async () => {
      second.resolve([counterEvidence]);
      await second.promise;
    });
    expect(await screen.findByText("反例证据原文")).toBeVisible();

    await act(async () => {
      first.resolve([supportEvidence]);
      await first.promise;
    });
    await waitFor(() => expect(screen.getByText("反例证据原文")).toBeVisible());
    expect(screen.queryByText("支持证据原文")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "查看支持证据" }));
    fireEvent.click(screen.getByRole("button", { name: "关闭" }));
    expect(screen.queryByRole("dialog", { name: "原始证据" })).not.toBeInTheDocument();
    await act(async () => {
      afterClose.resolve([supportEvidence]);
      await afterClose.promise;
    });
    expect(screen.queryByRole("dialog", { name: "原始证据" })).not.toBeInTheDocument();
  });
});

describe("OpportunityCard", () => {
  it("可通过键盘展开机会摘要", () => {
    render(<OpportunityCard card={opportunity} />);
    const trigger = screen.getByRole("button", { name: "展开机会详情" });

    fireEvent.keyDown(trigger, { key: "Enter" });

    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("主题：服务")).toBeVisible();
  });

  it("localizes a generated English topic title without changing its raw aspect", () => {
    render(<OpportunityCard card={{ ...opportunity, aspect: "food", title: "food 体验机会" }} />);

    expect(screen.getByRole("heading", { name: "餐饮体验机会" })).toBeVisible();
  });
});
