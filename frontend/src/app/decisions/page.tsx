"use client";

import React, { useEffect, useState } from "react";
import { DecisionForm, type DecisionFormInitialValues } from "@/components/decision-form";
import { DecisionResult } from "@/components/decision-result";
import { Navigation } from "@/components/navigation";
import { TraceDrawer } from "@/components/trace-drawer";
import { getDecisionMemo, getInsights } from "@/lib/api";
import type { DecisionCard, DecisionMemo } from "@/lib/types";

const MAX_PREFILLED_EVIDENCE = 8;
const DEMO_READ_ONLY = process.env.NEXT_PUBLIC_DEMO_READ_ONLY === "true";

function isAbortError(error: unknown) {
  return (error as { name?: string }).name === "AbortError";
}

function initialValuesFromMemo(memo: DecisionMemo | null): DecisionFormInitialValues | undefined {
  if (memo?.decision_status !== "actionable" || !memo.experiment) return undefined;

  const evidenceIds = memo.supporting_evidence
    .map((item) => item.evidence_id)
    .slice(0, MAX_PREFILLED_EVIDENCE);

  return {
    title: memo.subproblem?.trim() || memo.topic,
    evidence_ids: evidenceIds,
    omitted_evidence_count: Math.max(0, memo.supporting_evidence.length - evidenceIds.length),
    problem_statement: memo.decision_statement,
    hypothesis: memo.experiment.hypothesis,
    primary_metric: memo.experiment.primary_metric,
    guardrail_metric: memo.experiment.guardrail_metric,
  };
}

export default function DecisionsPage() {
  const [version, setVersion] = useState<string | null>(null);
  const [memo, setMemo] = useState<DecisionMemo | null>(null);
  const [memoLoadWarning, setMemoLoadWarning] = useState<string | null>(null);
  const [card, setCard] = useState<DecisionCard | null>(null);
  const [traceOpen, setTraceOpen] = useState(false);

  useEffect(() => {
    const controller = new AbortController();

    const load = async () => {
      try {
        const data = await getInsights(undefined, controller.signal);
        let currentMemo: DecisionMemo | null = null;
        try {
          currentMemo = await getDecisionMemo(data.active_dataset.id, controller.signal);
        } catch (requestError: unknown) {
          if (isAbortError(requestError)) return;
          setMemoLoadWarning("未能读取当前决策备忘录；你仍可人工填写实验，不会使用伪造预填内容。");
        }
        if (!controller.signal.aborted) {
          setMemo(currentMemo);
          setVersion(data.active_dataset.id);
        }
      } catch (requestError: unknown) {
        if (!isAbortError(requestError)) setVersion("");
      }
    };

    void load();
    return () => controller.abort();
  }, []);

  const initialValues = initialValuesFromMemo(memo);

  return (
    <main className="app-shell">
      <Navigation />
      <section className="workspace">
        <header className="topbar">
          <div>
            <h1>决策实验室</h1>
            <p>只把已验证且可行动的备忘录转成实验草案，责任人与日期始终由人确认</p>
          </div>
          {DEMO_READ_ONLY && <span className="demo-read-only-badge">只读演示模式</span>}
        </header>
        <div className="decision-page">
          {version === null ? (
            <section className="state-panel">正在读取当前数据版本与决策备忘录…</section>
          ) : version ? (
            <>
              {memoLoadWarning && <section className="state-panel error">{memoLoadWarning}</section>}
              {DEMO_READ_ONLY ? (
                <section className="state-panel demo-read-only-panel">
                  <h2>演示环境已锁定为只读</h2>
                  <p>你可以审阅 AI 结论、证据和 Trace；创建实验与写入操作在面试展示期间已关闭。</p>
                </section>
              ) : (
                <DecisionForm
                  datasetVersionId={version}
                  initialValues={initialValues}
                  key={memo?.id ?? "manual"}
                  onCreated={setCard}
                />
              )}
              {card && <DecisionResult card={card} onOpenTrace={() => setTraceOpen(true)} />}
            </>
          ) : (
            <section className="state-panel error">无法读取数据版本，请启动后端后重试。</section>
          )}
        </div>
        {traceOpen && card && <TraceDrawer entityId={card.id} onClose={() => setTraceOpen(false)} />}
      </section>
    </main>
  );
}
