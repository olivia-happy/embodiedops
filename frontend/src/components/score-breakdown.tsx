import React from "react";

const labels: Record<string, string> = { affected: "受影响度", negativity: "负向程度", severity: "严重度", business_fit: "业务匹配", evidence: "证据充分度" };
export function ScoreBreakdown({ contributions, total }: { contributions: Record<string, number>; total: number | null }) {
  if (total == null) return <p className="score-pending">待补充数据，暂不展示误导性的优先级。</p>;
  return <section className="score-breakdown"><strong>{total.toFixed(1)} / 100</strong>{Object.entries(contributions).map(([key, value]) => <span key={key}>{labels[key] ?? key} {value.toFixed(1)}</span>)}</section>;
}
