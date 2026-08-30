"use client";

import React, { useCallback, useEffect, useState } from "react";
import { Navigation } from "@/components/navigation";
import { RiskTimeline } from "@/components/risk-timeline";
import { getRisks } from "@/lib/api";
import type { OverviewResponse } from "@/lib/types";

export default function RisksPage() {
  const [data, setData] = useState<Pick<OverviewResponse, "active_dataset" | "risks"> | null>(null);
  const [error, setError] = useState(false);
  const loadRisks = useCallback(() => {
    setError(false);
    getRisks().then(setData).catch(() => setError(true));
  }, []);

  useEffect(() => { loadRisks(); }, [loadRisks]);

  return (
    <main className="app-shell">
      <Navigation />
      <section className="workspace">
        <header className="topbar"><div><h1>产业风险雷达</h1><p>使用人工审核的权威来源，追踪外部变化与下一检查点。</p></div></header>
        <div className="risks-page">
          {error ? <section className="state-panel error"><p>无法加载风险事件。</p><button type="button" onClick={loadRisks}>重新加载</button></section> : !data ? <section className="state-panel">正在加载权威来源事件……</section> : (
            <>
              <p className="version-note">当前数据版本：{data.active_dataset.id} · {data.risks.length} 项事件</p>
              <RiskTimeline risks={data.risks} />
            </>
          )}
        </div>
      </section>
    </main>
  );
}
