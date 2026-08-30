"use client";

import React, { useState } from "react";
import type { OverviewResponse } from "@/lib/types";

type Opportunity = OverviewResponse["opportunities"][number];

const contributionLabels: Record<string, string> = {
  affected: "受影响度",
  negativity: "负向强度",
  severity: "问题严重度",
  negative_rate: "负向反馈",
  review_count: "样本规模",
  business_fit: "业务匹配",
  evidence: "证据覆盖",
  evidence_count: "证据覆盖",
};

function readableValue(value: number | null) {
  return value == null ? "不足以判断" : value.toFixed(1);
}

export function DecisionHero({
  opportunity,
  datasetVersionId,
  onShowEvidence,
}: {
  opportunity: Opportunity;
  datasetVersionId: string;
  onShowEvidence: (ids: string[]) => Promise<void> | void;
}) {
  const [expanded, setExpanded] = useState(false);
  const evidenceAvailable = opportunity.evidence_ids.length > 0;

  return (
    <section className="decision-hero" aria-labelledby="decision-hero-title">
      <div className="decision-hero__seal" aria-hidden="true">决</div>
      <div className="decision-hero__content">
        <p className="eyebrow">本周第一优先级 · {opportunity.aspect || "不足以判断"}</p>
        <h2 id="decision-hero-title">{opportunity.title}</h2>
        <p>
          先处理这一信号：负向反馈 {opportunity.negative_rate == null ? "不足以判断" : `${opportunity.negative_rate.toFixed(1)}%`}，
          基于 {opportunity.review_count} 条真实样本。
        </p>
        <div className="decision-hero__actions">
          <button
            type="button"
            aria-expanded={expanded}
            aria-controls={`decision-evidence-${opportunity.id}`}
            onClick={() => setExpanded((current) => !current)}
          >
            {expanded ? "收起依据" : "展开依据"}
          </button>
          <button type="button" onClick={() => onShowEvidence(opportunity.evidence_ids)} disabled={!evidenceAvailable}>
            查看原始证据
          </button>
        </div>
      </div>
      <div className="decision-hero__score" aria-label={`优先级评分 ${readableValue(opportunity.score)}`}>
        <small>优先级评分</small>
        <strong>{readableValue(opportunity.score)}</strong>
        <span>{opportunity.scoreable ? "可评分" : "不足以判断"}</span>
      </div>
      {expanded && (
        <div className="decision-hero__fold" id={`decision-evidence-${opportunity.id}`}>
          <p>负向反馈 {opportunity.negative_rate == null ? "不足以判断" : `${opportunity.negative_rate.toFixed(1)}%`} · {opportunity.review_count} 条样本 · {opportunity.evidence_count} 条证据</p>
          <p>数据版本 {datasetVersionId}</p>
          <dl>
            {Object.entries(opportunity.contributions).length ? Object.entries(opportunity.contributions).map(([key, value]) => (
              <div key={key}>
                <dt>{contributionLabels[key] ?? key}</dt>
                <dd>{readableValue(value)}</dd>
              </div>
            )) : <div><dt>评分构成</dt><dd>不足以判断</dd></div>}
          </dl>
          {!opportunity.scoreable && opportunity.missing_fields.length > 0 && <p>待补充：{opportunity.missing_fields.join("、")}</p>}
        </div>
      )}
    </section>
  );
}
