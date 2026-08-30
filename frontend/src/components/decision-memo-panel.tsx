"use client";

import React from "react";
import type { DecisionMemo, MemoEvidenceReference, MemoGenerationJob, ModelHealth } from "@/lib/types";

const stages: Record<MemoGenerationJob["status"], string> = {
  queued: "正在排队",
  analyzing_signals: "正在读取信号",
  grouping_evidence: "正在拆分问题",
  validating_evidence: "正在验证证据",
  generating_memo: "正在生成备忘录",
  completed: "已完成",
  failed: "生成失败",
};

const failureMessages: Record<string, string> = {
  SERVICE_RESTARTED: "生成服务在任务执行期间重启，请重新生成。",
  LOCAL_MODEL_UNAVAILABLE: "本地模型服务暂时不可达，请确认服务状态后重试。",
  LOCAL_MODEL_TIMEOUT: "本地模型响应超时，请检查资源占用后重试。",
  LOCAL_MODEL_NOT_FOUND: "没有找到已配置的本地模型，请检查模型名称。",
  LOCAL_MODEL_INVALID_URL: "本地模型地址未通过本机安全校验，请检查配置。",
  LOCAL_MODEL_RESPONSE_ERROR: "本地模型返回了无法处理的响应，请检查本地服务日志。",
  INVALID_MODEL_JSON: "本地模型输出结构无效，生成流程已拒绝保存结论。",
  MEMO_GENERATION_FAILED: "生成流程未完成，未保存新的决策结论。",
};

type Props = {
  memo: DecisionMemo | null;
  job: MemoGenerationJob | null;
  health: ModelHealth | null;
  loading: boolean;
  error: string | null;
  onGenerate: () => void;
  onRefresh: () => void;
  onShowEvidence: (ids: string[]) => void;
};

// NEXT_PUBLIC_* values are inlined by Next.js at build time. Keeping this
// constant here makes the public demo's write boundary explicit and
// deterministic for interview builds.
export const DEMO_READ_ONLY = process.env.NEXT_PUBLIC_DEMO_READ_ONLY === "true";

const facts = (memo: DecisionMemo) => [
  ["样本", memo.facts.review_count],
  ["负向率", memo.facts.negative_rate == null ? "—" : `${memo.facts.negative_rate}%`],
  ["严重度", memo.facts.severity ?? "—"],
  ["机会分", memo.facts.opportunity_score ?? "—"],
];

function safeFailureCode(errorCode: string | null): string {
  if (errorCode && /^[A-Z][A-Z0-9_]{0,63}$/.test(errorCode)) return errorCode;
  return "MEMO_GENERATION_FAILED";
}

function EvidenceGroup({
  kind,
  items,
  onShowEvidence,
}: {
  kind: "supporting" | "counter";
  items: MemoEvidenceReference[];
  onShowEvidence: (ids: string[]) => void;
}) {
  const supporting = kind === "supporting";
  const title = supporting ? "支持证据" : "反例证据";
  const emptyText = supporting ? "未引用支持证据" : "未发现反例证据";
  const ids = items.map((item) => item.evidence_id);

  return (
    <div>
      <h3>{title}</h3>
      {items.length ? (
        <ul>
          {items.map((item) => <li key={item.evidence_id}><code>{item.evidence_id}</code><p>{item.rationale}</p></li>)}
        </ul>
      ) : <p>{emptyText}</p>}
      <button type="button" disabled={!items.length} onClick={() => { if (ids.length) onShowEvidence(ids); }}>
        查看{title}
      </button>
    </div>
  );
}

export function DecisionMemoPanel({ memo, job, health, loading, error, onGenerate, onRefresh, onShowEvidence }: Props) {
  const running = job && !["completed", "failed"].includes(job.status);

  if (loading && !memo) {
    return <section className="memo-panel" aria-live="polite">正在读取决策备忘录…</section>;
  }

  if (running) {
    return (
      <section className="memo-panel memo-running" aria-live="polite">
        <p className="eyebrow">真实任务进度</p>
        <h2>{stages[job.status]}</h2>
        <ol className="stage-rail">
          {(["analyzing_signals", "grouping_evidence", "validating_evidence", "generating_memo"] as const).map((stage) => (
            <li key={stage} className={job.status === stage ? "active" : ""}>{stages[stage]}</li>
          ))}
        </ol>
      </section>
    );
  }

  if (job?.status === "failed") {
    const failureCode = safeFailureCode(job.error_code);
    return (
      <section className="memo-panel memo-failed">
        <p className="eyebrow">生成任务未完成</p>
        <h2>本次生成失败</h2>
        <p>{failureMessages[failureCode] ?? "生成流程未完成，请根据错误代码检查本地服务日志。"}</p>
        <p>错误代码：<code>{failureCode}</code></p>
        {!DEMO_READ_ONLY && <button type="button" onClick={onGenerate}>重新生成</button>}
      </section>
    );
  }

  if (!memo) {
    const ready = Boolean(health?.configured && health.ready);
    return (
      <section className="memo-panel">
        {DEMO_READ_ONLY && <p className="demo-read-only-badge">只读演示模式</p>}
        <p className="eyebrow">AI 决策备忘录</p>
        <h2>{ready ? "准备从真实证据生成判断" : "本地模型尚未就绪"}</h2>
        <p>{error ?? (health?.configured ? "请确认本地模型服务已启动后生成。" : "尚未配置本地模型；不会使用模板或远程模型替代。")}</p>
        {!DEMO_READ_ONLY && <button type="button" disabled={!ready} onClick={onGenerate}>生成决策备忘录</button>}
        {!ready && <button type="button" onClick={onRefresh}>重新检查本地模型</button>}
      </section>
    );
  }

  const actionable = memo.decision_status === "actionable";
  const refusal = memo.decision_status === "refused";

  return (
    <section className={`memo-panel memo-${memo.decision_status}`}>
      <div className="memo-heading">
        <div>
          {DEMO_READ_ONLY && <p className="demo-read-only-badge">只读演示模式</p>}
          <p className="eyebrow">数据印章 · {memo.dataset_version_id}</p>
          <h2>{refusal ? "暂不形成业务结论" : memo.decision_statement}</h2>
        </div>
        <span>{actionable ? "可实验" : refusal ? "已拒绝" : "需补数"}</span>
      </div>

      {!memo.counter_evidence_checked && <p className="audit-warning">反例审计未完成：当前结论仅用于拒绝或继续核查。</p>}
      <p className="memo-summary">{memo.reasoning_summary}</p>

      {memo.subproblem && <div className="memo-plan"><h3>已验证子问题</h3><p>{memo.subproblem}</p></div>}
      {memo.evidence_plan?.candidate_subproblems.length ? (
        <div className="memo-plan">
          <h3>待验证候选子问题</h3>
          <ul>{memo.evidence_plan.candidate_subproblems.map((item) => <li key={item}>{item}</li>)}</ul>
        </div>
      ) : null}

      <dl className="memo-facts">
        {facts(memo).map(([label, value]) => <div key={String(label)}><dt>{label}</dt><dd>{value}</dd></div>)}
      </dl>

      <div className="memo-evidence-columns">
        <EvidenceGroup kind="supporting" items={memo.supporting_evidence} onShowEvidence={onShowEvidence} />
        <EvidenceGroup kind="counter" items={memo.counter_evidence} onShowEvidence={onShowEvidence} />
      </div>

      <h3>仍未知</h3>
      <ul>{memo.unknowns.map((item) => <li key={item}>{item}</li>)}</ul>

      {memo.decision_status === "needs_evidence" && memo.evidence_plan && (
        <div className="memo-plan">
          <h3>暂不建议采取业务动作</h3>
          <p>{memo.evidence_plan.reassessment_condition}</p>
          <p>补充字段：{memo.evidence_plan.collection_fields.join("、")}</p>
          <p>每个候选子问题至少 {memo.evidence_plan.minimum_evidence_per_subproblem} 条独立证据</p>
        </div>
      )}

      {actionable && memo.experiment && (
        <div className="memo-plan">
          <h3>服务端实验方案</h3>
          <dl>
            <div><dt>假设</dt><dd>{memo.experiment.hypothesis}</dd></div>
            <div><dt>目标人群</dt><dd>{memo.experiment.target_segment}</dd></div>
            <div><dt>干预方式</dt><dd>{memo.experiment.intervention}</dd></div>
            <div><dt>主指标</dt><dd>{memo.experiment.primary_metric}</dd></div>
            <div><dt>护栏指标</dt><dd>{memo.experiment.guardrail_metric}</dd></div>
            <div><dt>实验周期</dt><dd>{memo.experiment.duration_days} 天</dd></div>
          </dl>
          <h4>停止条件</h4>
          <ul>{memo.experiment.stop_conditions.map((condition) => <li key={condition}>{condition}</li>)}</ul>
          {!DEMO_READ_ONLY && <a href="/decisions#experiment-form">创建实验</a>}
        </div>
      )}

      {refusal && <p className="audit-warning">拒绝原因：{memo.refusal_reason}</p>}
    </section>
  );
}
