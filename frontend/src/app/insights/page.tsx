"use client";

import React, { useCallback, useEffect, useState } from "react";
import { EvidenceDrawer } from "@/components/evidence-drawer";
import { InsightFilters } from "@/components/insight-filters";
import { InsightsBody } from "@/components/insights-body";
import { Navigation } from "@/components/navigation";
import { getDecisionMemo, getEvidence, getInsights } from "@/lib/api";
import type { DecisionMemo, Evidence, InsightsResponse } from "@/lib/types";

function isAbortError(error: unknown) {
  return (error as { name?: string }).name === "AbortError";
}

export default function InsightsPage() {
  const [data, setData] = useState<InsightsResponse | null>(null);
  const [memo, setMemo] = useState<DecisionMemo | null>(null);
  const [aspect, setAspect] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [memoError, setMemoError] = useState<string | null>(null);
  const [evidence, setEvidence] = useState<Evidence[]>([]);
  const [evidenceIds, setEvidenceIds] = useState<string[]>([]);
  const [evidenceLoading, setEvidenceLoading] = useState(false);
  const [evidenceError, setEvidenceError] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setData(null);
    setError(null);
    setMemoError(null);

    getInsights(aspect || undefined, controller.signal)
      .then(async (nextData) => {
        let nextMemo: DecisionMemo | null = null;
        try {
          nextMemo = await getDecisionMemo(nextData.active_dataset.id, controller.signal);
        } catch (requestError: unknown) {
          if (isAbortError(requestError)) throw requestError;
          setMemoError("决策备忘录暂时无法读取；当前仅展示既有的可验证洞察。");
        }
        if (!controller.signal.aborted) {
          setData(nextData);
          setMemo(nextMemo);
        }
      })
      .catch((requestError: unknown) => {
        if (!isAbortError(requestError)) {
          setError("无法加载洞察数据，请确认后端服务正在运行。");
        }
      });

    const query = aspect ? `?aspect=${encodeURIComponent(aspect)}` : "";
    window.history.replaceState(null, "", `/insights${query}`);
    return () => controller.abort();
  }, [aspect]);

  const loadEvidence = useCallback(async (ids: string[]) => {
    if (!data) return;
    setEvidenceIds(ids);
    setEvidence([]);
    setEvidenceError(null);
    setEvidenceLoading(true);
    setDrawerOpen(true);
    try {
      setEvidence(await getEvidence(data.active_dataset.id, ids));
    } catch {
      setEvidenceError("原始证据加载失败，请重试。");
    } finally {
      setEvidenceLoading(false);
    }
  }, [data]);

  return (
    <main className="app-shell">
      <Navigation />
      <section className="workspace">
        <header className="topbar">
          <div>
            <h1>用户洞察工作台</h1>
            <p>筛选主题、审阅当前备忘录引用的支持与反例，再打开原始证据核验</p>
          </div>
          {data && (
            <InsightFilters
              aspects={data.metrics.map((metric) => metric.aspect)}
              value={aspect}
              onChange={setAspect}
            />
          )}
        </header>
        <div className="insights-page">
          {memoError && <section className="state-panel error">{memoError}</section>}
          {error ? (
            <section className="state-panel error">{error}</section>
          ) : !data ? (
            <section className="state-panel">正在加载主题、备忘录与证据…</section>
          ) : (
            <InsightsBody
              metrics={data.metrics}
              insights={data.insights}
              memo={memo}
              selectedAspect={aspect}
              onSelectAspect={setAspect}
              onShowEvidence={loadEvidence}
            />
          )}
        </div>
        <EvidenceDrawer
          error={evidenceError}
          evidence={evidence}
          loading={evidenceLoading}
          onClose={() => setDrawerOpen(false)}
          onRetry={() => loadEvidence(evidenceIds)}
          open={drawerOpen}
        />
      </section>
    </main>
  );
}
