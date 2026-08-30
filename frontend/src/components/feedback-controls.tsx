import React, { useState } from "react";
import { submitFeedback } from "@/lib/api";

export function FeedbackControls({ entityType, entityId }: { entityType: "insight" | "decision" | "risk"; entityId: string }) {
  const [message, setMessage] = useState(""); const send = async (decision: "confirmed" | "rejected" | "edited") => { const reason = decision === "edited" ? window.prompt("请说明修改原因：") ?? "" : undefined; if (decision === "edited" && !reason?.trim()) { setMessage("修改反馈必须说明原因。"); return; } try { await submitFeedback(entityType, entityId, decision, reason); setMessage("反馈已保存。"); } catch { setMessage("反馈保存失败。"); } };
  return <div className="feedback-controls"><button onClick={() => send("confirmed")}>确认</button><button onClick={() => send("rejected")}>驳回</button><button onClick={() => send("edited")}>需修改</button>{message && <small>{message}</small>}</div>;
}
