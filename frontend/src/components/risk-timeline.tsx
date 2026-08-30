import React from "react";
import type { OverviewResponse } from "@/lib/types";
import { FeedbackControls } from "./feedback-controls";

type Risk = OverviewResponse["risks"][number];

const contributionLabels: Record<string, string> = {
  impact: "影响范围",
  urgency: "紧迫程度",
  likelihood: "发生可能",
  evidence_quality: "证据质量",
};

function statusLabel(status: Risk["status"]) {
  return status === "needs_review" ? "待复核" : "持续监测";
}

export function RiskTimeline({ risks }: { risks: OverviewResponse["risks"] }) {
  const orderedRisks = [...risks].sort((a, b) => b.score - a.score);

  return (
    <div className="risk-timeline" aria-label="产业风险时间轴">
      {orderedRisks.map((risk) => (
        <article key={risk.id} className="risk-card">
          <div className="risk-date"><time dateTime={risk.published_on}>{risk.published_on}</time></div>
          <section>
            <header className="card-heading">
              <div>
                <p className="eyebrow">{risk.event_type}</p>
                <h3>{risk.industry}</h3>
              </div>
              <strong aria-label={`风险评分 ${risk.score.toFixed(1)}`}>{risk.score.toFixed(1)}</strong>
            </header>
            <p>{risk.excerpt}</p>
            <dl className="risk-metadata">
              <div><dt>证据质量</dt><dd>{risk.evidence_quality.toFixed(1)}</dd></div>
              <div><dt>负责人</dt><dd>{risk.owner}</dd></div>
              <div><dt>状态</dt><dd>{statusLabel(risk.status)}</dd></div>
              <div><dt>缓解动作</dt><dd>{risk.mitigation_action}</dd></div>
            </dl>
            <details>
              <summary>查看评分依据</summary>
              {Object.entries(risk.contributions).length ? (
                <ul className="score-list">{Object.entries(risk.contributions).map(([name, value]) => <li key={name}>{contributionLabels[name] ?? name} {value.toFixed(1)}</li>)}</ul>
              ) : <p>暂无评分构成。</p>}
            </details>
            <a href={risk.source_url} target="_blank" rel="noreferrer">打开权威来源</a>
            <footer><FeedbackControls entityType="risk" entityId={risk.id} /></footer>
          </section>
        </article>
      ))}
    </div>
  );
}
