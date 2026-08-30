import React from "react";
import type { InsightsResponse } from "@/lib/types";

export function TopicChart({ metrics }: { metrics: InsightsResponse["metrics"] }) {
  const max = Math.max(1, ...metrics.map((metric) => metric.review_count));
  return <section className="topic-chart"><div className="section-title"><h2>主题分布</h2><span>样本量与负向比例</span></div>{metrics.map((metric) => <div className="topic-row" key={metric.aspect}><span>{metric.aspect}</span><div className="topic-bar"><i style={{ width: `${metric.review_count / max * 100}%` }} /></div><small>{metric.review_count} 条 · {metric.negative_rate ?? 0}% 负向</small></div>)}</section>;
}
