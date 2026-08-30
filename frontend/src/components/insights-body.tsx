import React from "react";
import { SignalSpectrum } from "./signal-spectrum";
import type { DecisionMemo, InsightsResponse, MemoEvidenceReference } from "@/lib/types";

type InsightsBodyProps = {
  metrics: InsightsResponse["metrics"];
  insights: InsightsResponse["insights"];
  memo?: DecisionMemo | null;
  selectedAspect?: string;
  onSelectAspect?: (aspect: string) => void;
  onShowEvidence: (ids: string[]) => Promise<void> | void;
};

function EvidenceGroup({
  items,
  label,
  onShowEvidence,
}: {
  items: MemoEvidenceReference[];
  label: "支持证据" | "反例证据";
  onShowEvidence: (ids: string[]) => Promise<void> | void;
}) {
  const evidenceIds = items.map((item) => item.evidence_id);

  return (
    <div>
      <h3>{label}</h3>
      {items.length ? (
        <ul>
          {items.map((item) => (
            <li key={item.evidence_id}>
              <code>{item.evidence_id}</code>
              <span>{item.rationale}</span>
            </li>
          ))}
        </ul>
      ) : <p>当前备忘录未引用此类证据。</p>}
      <button disabled={!items.length} onClick={() => onShowEvidence(evidenceIds)} type="button">
        查看{label}
      </button>
    </div>
  );
}

export function InsightsBody({
  metrics,
  insights,
  memo = null,
  selectedAspect = "",
  onSelectAspect = () => undefined,
  onShowEvidence,
}: InsightsBodyProps) {
  return (
    <>
      <SignalSpectrum metrics={metrics} selectedAspect={selectedAspect} onSelectAspect={onSelectAspect} />
      {memo ? (
        <section className="insight-list" aria-labelledby="memo-evidence-title">
          <div className="section-title">
            <div>
              <p className="eyebrow">当前决策备忘录</p>
              <h2 id="memo-evidence-title">支持与反例证据</h2>
            </div>
            <span>这里只审阅备忘录引用，不生成另一套结论</span>
          </div>
          <article className="insight-card">
            <small>数据版本 · {memo.dataset_version_id}</small>
            <h3>{memo.decision_statement}</h3>
            <p>{memo.reasoning_summary}</p>
            {!memo.counter_evidence_checked && (
              <p className="audit-warning">反例审计尚未完成；当前内容不可作为行动依据。</p>
            )}
            <div className="memo-evidence-columns">
              <EvidenceGroup items={memo.supporting_evidence} label="支持证据" onShowEvidence={onShowEvidence} />
              <EvidenceGroup items={memo.counter_evidence} label="反例证据" onShowEvidence={onShowEvidence} />
            </div>
          </article>
        </section>
      ) : (
        <section className="insight-list" aria-labelledby="verified-insights-title">
          <div className="section-title">
            <h2 id="verified-insights-title">已验证洞察</h2>
            <span>每条结论均可打开原始证据</span>
          </div>
          {insights.length ? insights.map((insight) => (
            <article className="insight-card" key={insight.id}>
              <small>{Math.round(insight.confidence * 100)}% 置信度 · {insight.recommended_action}</small>
              <h3>{insight.title}</h3>
              <p>{insight.claim}</p>
              {insight.unknowns.length ? <p className="unknowns">未知项：{insight.unknowns.join("、")}</p> : null}
              <button onClick={() => onShowEvidence(insight.evidence_ids)} type="button">
                查看 {insight.evidence_ids.length} 条证据
              </button>
            </article>
          )) : (
            <section className="empty-card" aria-labelledby="no-insights-title">
              <h3 id="no-insights-title">尚未形成可验证洞察</h3>
              <p>先审阅主题信号与原始证据，再决定是否形成结论。</p>
            </section>
          )}
        </section>
      )}
    </>
  );
}
