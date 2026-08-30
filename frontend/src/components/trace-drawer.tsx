import React, { useEffect, useState } from "react";
import { getTraces } from "@/lib/api";
import type { Trace } from "@/lib/types";

export function TraceDrawer({ entityId, onClose }: { entityId: string; onClose: () => void }) {
  const [traces, setTraces] = useState<Trace[] | null>(null); useEffect(() => { getTraces(entityId).then(setTraces).catch(() => setTraces([])); }, [entityId]);
  return <aside className="evidence-drawer"><header><div><small>不含提示词、响应正文或密钥</small><h2>Trace 审计</h2></div><button onClick={onClose}>关闭</button></header>{traces?.length ? traces.map((trace) => <article key={trace.id}><code>{trace.validation_status}</code><p>数据版本：{trace.dataset_version_id}<br />提示词版本：{trace.prompt_version}<br />模型：{trace.model_name ?? "规则回退"}<br />耗时：{trace.latency_ms} ms · Token 估算：{trace.token_estimate}<br />证据：{trace.evidence_ids.join(", ")}</p></article>) : <p className="drawer-empty">暂无可用 Trace。</p>}</aside>;
}
