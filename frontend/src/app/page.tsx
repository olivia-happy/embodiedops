"use client";

import React, { useEffect, useRef, useState } from "react";
import { DecisionMemoPanel } from "@/components/decision-memo-panel";
import { EvidenceDrawer } from "@/components/evidence-drawer";
import { MetricCard } from "@/components/metric-card";
import { Navigation } from "@/components/navigation";
import { OpportunityCard } from "@/components/opportunity-card";
import { RiskCard } from "@/components/risk-card";
import { getEvidence, getOverview } from "@/lib/api";
import { formatOpportunityTitle } from "@/lib/aspect-labels";
import type { Evidence, OverviewResponse } from "@/lib/types";
import { useDecisionMemo } from "@/hooks/use-decision-memo";

type EvidenceState = "closed" | "loading" | "error" | "ready";
const DEMO_READ_ONLY = process.env.NEXT_PUBLIC_DEMO_READ_ONLY === "true";

export default function OverviewPage() {
  const [data, setData] = useState<OverviewResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [evidence, setEvidence] = useState<Evidence[]>([]);
  const [evidenceState, setEvidenceState] = useState<EvidenceState>("closed");
  const [selectedEvidenceIds, setSelectedEvidenceIds] = useState<string[]>([]);
  const evidenceEpochRef = useRef(0);
  const memoWorkflow = useDecisionMemo(data?.active_dataset.id);

  const load = () => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    getOverview(controller.signal)
      .then(setData)
      .catch((cause: unknown) => {
        if ((cause as Error).name !== "AbortError") setError("无法加载决策总览，请确认后端服务与数据导入状态。");
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  };

  useEffect(() => load(), []);

  useEffect(() => {
    evidenceEpochRef.current += 1;
    setEvidence([]);
    setSelectedEvidenceIds([]);
    setEvidenceState("closed");
  }, [data?.active_dataset.id]);

  useEffect(() => () => {
    evidenceEpochRef.current += 1;
  }, []);

  const closeEvidence = () => {
    evidenceEpochRef.current += 1;
    setEvidence([]);
    setEvidenceState("closed");
  };
  const showEvidence = async (ids: string[]) => {
    if (!data || !ids.length) return;
    const datasetVersionId = data.active_dataset.id;
    const requestEpoch = ++evidenceEpochRef.current;
    setSelectedEvidenceIds(ids);
    setEvidence([]);
    setEvidenceState("loading");
    try {
      const result = await getEvidence(datasetVersionId, ids);
      if (evidenceEpochRef.current !== requestEpoch) return;
      setEvidence(result);
      setEvidenceState("ready");
    } catch {
      if (evidenceEpochRef.current !== requestEpoch) return;
      setEvidenceState("error");
    }
  };

  const retryEvidence = () => void showEvidence(selectedEvidenceIds);
  const opportunityQueue = data?.opportunities.slice(1) ?? [];
  const heroOpportunity = data?.opportunities[0];

  return (
    <main className="app-shell">
      <Navigation />
      <section className="workspace">
        <header className="topbar">
          <div>
            <h1>本周决策台</h1>
            <p>先给结论，再逐层审阅真实证据、评分与未知项。</p>
          </div>
          <div className="data-status">
            {DEMO_READ_ONLY && <span className="demo-read-only-badge">只读演示模式</span>}
            <span>{data?.active_dataset.is_stale ? "数据已过期" : "数据可用"}</span>
            <small>{data ? `快照：${new Date(data.active_dataset.imported_at).toLocaleString("zh-CN")}` : "正在连接数据服务"}</small>
          </div>
        </header>
        {loading ? <section className="state-panel">正在加载决策数据…</section> : error ? (
          <section className="state-panel error"><p>{error}</p><button onClick={load}>重试</button></section>
        ) : data ? (
          <div className="dashboard">
            <section className="metrics" aria-label="数据概况">
              <MetricCard label="样本量" value={data.summary_metrics.review_count.toLocaleString()} detail="本次导入评论" />
              <MetricCard label="负向反馈" value={data.summary_metrics.negative_review_rate == null ? "不足以判断" : `${data.summary_metrics.negative_review_rate}%`} detail={`${data.summary_metrics.negative_review_count} 条负向反馈`} tone="red" />
              <MetricCard label="主题覆盖" value={String(data.summary_metrics.topic_count)} detail="当前数据中的主题数量" tone="amber" />
              <MetricCard label="市场事件" value={String(data.summary_metrics.market_event_count)} detail="已纳入风险跟踪" tone="green" />
            </section>
            <DecisionMemoPanel memo={memoWorkflow.memo} job={memoWorkflow.job} health={memoWorkflow.health} loading={memoWorkflow.loading} error={memoWorkflow.error} onGenerate={() => void memoWorkflow.generate()} onRefresh={() => void memoWorkflow.refresh()} onShowEvidence={(ids) => void showEvidence(ids)} />
            {heroOpportunity ? <section className="hero-opportunity"><div><p className="eyebrow">规则评分候选信号</p><h2>{formatOpportunityTitle(heroOpportunity.title, heroOpportunity.aspect)}</h2><p>规则优先级 {heroOpportunity.score?.toFixed(1) ?? "不可评分"} · {heroOpportunity.evidence_count} 条证据</p><p>仅作为评分基线；是否可行动以当前决策备忘录的证据审计为准。</p></div><button type="button" onClick={() => void showEvidence(heroOpportunity.evidence_ids)}>查看原始证据</button></section> : <section className="empty-card">暂无可排序的候选信号。</section>}
            <section className="content-grid">
              <div>
                <div className="section-title"><h2>待验证主题</h2><span>规则评分基线，不构成备忘录之外的第二结论</span></div>
                <div className="opportunity-list">
                  {opportunityQueue.length ? opportunityQueue.map((card) => <OpportunityCard key={card.id} card={card} onShowEvidence={showEvidence} />) : <div className="empty-card">暂无其他待验证主题。</div>}
                </div>
              </div>
              <div>
                <div className="section-title"><h2>产业风险雷达</h2><a href="/risks">查看全部 →</a></div>
                <div className="risk-list">{data.risks.map((risk) => <RiskCard key={risk.id} risk={risk} />)}</div>
              </div>
            </section>
          </div>
        ) : <section className="state-panel">暂无数据。请先运行导入器后刷新。</section>}
      </section>
      {evidenceState === "ready" && <EvidenceDrawer open evidence={evidence} onClose={closeEvidence} />}
      {evidenceState === "loading" && <aside className="evidence-drawer" role="dialog" aria-modal="true" aria-label="原始证据"><header><h2>证据抽屉</h2><button onClick={closeEvidence}>关闭</button></header><p>正在加载原始证据…</p></aside>}
      {evidenceState === "error" && <aside className="evidence-drawer" role="dialog" aria-modal="true" aria-label="原始证据"><header><h2>证据抽屉</h2><button onClick={closeEvidence}>关闭</button></header><p>无法加载原始证据。</p><button onClick={retryEvidence}>重试</button></aside>}
    </main>
  );
}
