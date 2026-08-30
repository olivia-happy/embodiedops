import React from "react";

export function MetricCard({ label, value, detail, tone = "blue" }: { label: string; value: string; detail: string; tone?: string }) {
  return <article className="metric-card"><span>{label}</span><strong>{value}</strong><small className={`metric-detail ${tone}`}>{detail}</small></article>;
}
