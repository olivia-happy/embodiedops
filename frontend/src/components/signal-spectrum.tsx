import React from "react";
import { formatAspectLabel } from "@/lib/aspect-labels";
import type { InsightsResponse } from "@/lib/types";

type SignalSpectrumProps = {
  metrics: InsightsResponse["metrics"];
  selectedAspect: string;
  onSelectAspect: (aspect: string) => void;
};

function formatRate(rate: number | null) {
  return rate === null ? "不足以判断" : `${rate.toFixed(1)}% 负向`;
}

function formatSeverity(severity: number | null) {
  return severity === null ? "严重度不足以判断" : `严重度 ${severity.toFixed(2)}`;
}

export function SignalSpectrum({ metrics, selectedAspect, onSelectAspect }: SignalSpectrumProps) {
  const highestSampleCount = Math.max(1, ...metrics.map((metric) => metric.review_count));

  return (
    <section className="signal-spectrum" aria-labelledby="signal-spectrum-title">
      <div className="section-title">
        <div>
          <p className="eyebrow">主题信号</p>
          <h2 id="signal-spectrum-title">从样本到风险的信号谱</h2>
        </div>
        <span>选择一个主题以筛选可验证洞察</span>
      </div>
      {metrics.length ? (
        <div className="signal-spectrum-list">
          {metrics.map((metric) => {
            const isSelected = selectedAspect === metric.aspect;
            const displayAspect = formatAspectLabel(metric.aspect);
            const details = `${metric.review_count} 条样本 · ${formatRate(metric.negative_rate)} · ${formatSeverity(metric.severity)} · ${metric.scoreable ? "可评分" : "暂不可评分"}`;
            return (
              <button
                aria-pressed={isSelected}
                className={`signal-band${isSelected ? " selected" : ""}`}
                key={metric.aspect}
                onClick={() => onSelectAspect(isSelected ? "" : metric.aspect)}
                type="button"
              >
                <span className="signal-band__label">{displayAspect}</span>
                <span aria-hidden="true" className="signal-band__track">
                  <i style={{ width: `${(metric.review_count / highestSampleCount) * 100}%` }} />
                </span>
                <span className="signal-band__metrics">{details}</span>
              </button>
            );
          })}
        </div>
      ) : (
        <p className="empty-card">当前数据集没有可审阅的主题信号。</p>
      )}
    </section>
  );
}
