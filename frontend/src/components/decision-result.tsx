import React from "react";
import type { DecisionCard } from "@/lib/types";
import { FeedbackControls } from "./feedback-controls";

type DecisionResultProps = {
  card: DecisionCard;
  onOpenTrace: () => void;
};

export function DecisionResult({ card, onOpenTrace }: DecisionResultProps) {
  return (
    <article className="decision-result" aria-labelledby={`decision-${card.id}`}>
      <header>
        <small>数据版本：{card.dataset_version_id}</small>
        <p className="status-label">状态：{card.status}</p>
        <h2 id={`decision-${card.id}`}>{card.title}</h2>
      </header>
      <dl className="decision-details">
        <div><dt>问题</dt><dd>{card.problem_statement}</dd></div>
        <div><dt>假设</dt><dd>{card.hypothesis}</dd></div>
        <div><dt>主要指标</dt><dd>{card.primary_metric}</dd></div>
        <div><dt>护栏指标</dt><dd>{card.guardrail_metric}</dd></div>
        <div><dt>负责人</dt><dd>{card.owner}</dd></div>
        <div><dt>截止日期</dt><dd>{card.due_date}</dd></div>
      </dl>
      <section aria-labelledby={`score-${card.id}`}>
        <h3 id={`score-${card.id}`}>评分拆解</h3>
        <p className="decision-score">{card.score == null ? "不足以判断" : `${card.score.toFixed(1)} / 100`}</p>
        {Object.entries(card.score_breakdown).length > 0 ? (
          <ul className="score-list">
            {Object.entries(card.score_breakdown).map(([name, value]) => <li key={name}>{name} {value.toFixed(1)}</li>)}
          </ul>
        ) : <p>暂无评分构成。</p>}
      </section>
      <section aria-labelledby={`evidence-${card.id}`}>
        <h3 id={`evidence-${card.id}`}>关联证据</h3>
        {card.evidence_ids.length ? <ul className="evidence-id-list">{card.evidence_ids.map((id) => <li key={id}>{id}</li>)}</ul> : <p>未关联证据。</p>}
      </section>
      <div className="decision-actions">
        <FeedbackControls entityType="decision" entityId={card.id} />
        <button type="button" onClick={onOpenTrace}>查看 Trace</button>
      </div>
    </article>
  );
}
