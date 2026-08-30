"use client";

import React, { useState } from "react";
import { formatAspectLabel, formatOpportunityTitle } from "@/lib/aspect-labels";
import type { OverviewResponse } from "@/lib/types";

export type OpportunityPreview = OverviewResponse["opportunities"][number];

export function OpportunityCard({
  card,
  onShowEvidence,
}: {
  card: OpportunityPreview;
  onShowEvidence?: (ids: string[]) => Promise<void> | void;
}) {
  const [expanded, setExpanded] = useState(false);
  const toggle = () => setExpanded((current) => !current);

  return (
    <article className="opportunity-card">
      <div>
        <div className="card-heading">
          <h3>{formatOpportunityTitle(card.title, card.aspect)}</h3>
          <span>{card.evidence_count} 条证据</span>
        </div>
        <p>{card.negative_rate == null ? "不足以判断" : `负向反馈 ${card.negative_rate.toFixed(1)}% · ${card.review_count} 条样本`}</p>
        <button
          type="button"
          aria-expanded={expanded}
          aria-controls={`opportunity-detail-${card.id}`}
          aria-label="展开机会详情"
          onClick={toggle}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              toggle();
            }
          }}
        >
          {expanded ? "收起机会详情" : "展开机会详情"}
        </button>
        {expanded && (
          <div id={`opportunity-detail-${card.id}`} className="opportunity-card__detail">
            <p>主题：{formatAspectLabel(card.aspect)}</p>
            <p>优先级：{card.score == null ? "不足以判断" : card.score.toFixed(1)}</p>
            {onShowEvidence && <button type="button" onClick={() => onShowEvidence(card.evidence_ids)} disabled={!card.evidence_ids.length}>查看原始证据</button>}
          </div>
        )}
      </div>
      <div className="priority-score">
        <strong>{card.score == null ? "不足以判断" : card.score.toFixed(1)}</strong>
        <small>优先级</small>
      </div>
    </article>
  );
}
