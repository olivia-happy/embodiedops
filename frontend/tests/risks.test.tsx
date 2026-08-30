import "@testing-library/jest-dom/vitest";
import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RiskTimeline } from "../src/components/risk-timeline";

describe("RiskTimeline", () => {
  const lowRisk = { id: "event-low", dataset_version_id: "v1", source_url: "https://low.example.com", published_on: "2026-08-01", excerpt: "低分事件", event_type: "market" as const, industry: "半导体", evidence_quality: 62, score: 25, contributions: { evidence_quality: 10 }, mitigation_action: "继续监测", owner: "陈晨", status: "monitoring" as const };
  const highRisk = { id: "event-high", dataset_version_id: "v1", source_url: "https://high.example.com", published_on: "2026-08-02", excerpt: "高分事件", event_type: "policy" as const, industry: "半导体", evidence_quality: 90, score: 85, contributions: { evidence_quality: 30, urgency: 55 }, mitigation_action: "完成影响核对", owner: "李雷", status: "needs_review" as const };

  it("orders risks by descending score and reveals the scoring basis", () => {
    render(<RiskTimeline risks={[lowRisk, highRisk]} />);

    expect(screen.getAllByRole("article")[0]).toHaveTextContent("高分事件");
    expect(screen.getAllByRole("link", { name: "打开权威来源" })[0]).toHaveAttribute("href", highRisk.source_url);
    expect(screen.getAllByText("查看评分依据")[0].closest("details")).toHaveTextContent("紧迫程度 55.0");
  });

  it("links a risk card to its authority source", () => {
    render(<RiskTimeline risks={[highRisk]} />);
    expect(screen.getByRole("link", { name: "打开权威来源" })).toHaveAttribute("href", "https://high.example.com");
  });
});
