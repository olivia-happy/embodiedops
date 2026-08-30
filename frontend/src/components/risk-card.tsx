import React from "react";
import type { OverviewResponse } from "@/lib/types";

const names = { policy: "政策", market: "市场", talent: "人才" };
type Risk = OverviewResponse["risks"][number];

export function RiskCard({ risk }: { risk: Risk }) {
  return <article className="risk-card"><div className="card-heading"><span className={risk.status === "needs_review" ? "risk-chip urgent" : "risk-chip"}>{names[risk.event_type]}风险</span><strong>{risk.score.toFixed(1)}</strong></div><p>{risk.excerpt}</p><footer>{risk.mitigation_action}</footer></article>;
}
