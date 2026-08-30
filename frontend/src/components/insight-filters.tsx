import React from "react";
import { formatAspectLabel } from "@/lib/aspect-labels";

export function InsightFilters({ aspects, value, onChange }: { aspects: string[]; value: string; onChange: (value: string) => void }) {
  return <label className="insight-filter">主题筛选<select value={value} onChange={(event) => onChange(event.target.value)}><option value="">全部主题</option>{aspects.map((aspect) => <option value={aspect} key={aspect}>{formatAspectLabel(aspect)}</option>)}</select></label>;
}
