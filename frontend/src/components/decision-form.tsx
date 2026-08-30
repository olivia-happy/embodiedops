import React, { FormEvent, useState } from "react";
import { createDecision } from "@/lib/api";
import type { DecisionCard } from "@/lib/types";

export type DecisionFormInitialValues = {
  title: string;
  evidence_ids: string[];
  omitted_evidence_count?: number;
  problem_statement: string;
  hypothesis: string;
  primary_metric: string;
  guardrail_metric: string;
};

type DecisionFormProps = {
  datasetVersionId: string;
  onCreated: (card: DecisionCard) => void;
  initialValues?: DecisionFormInitialValues;
};

export function DecisionForm({ datasetVersionId, onCreated, initialValues }: DecisionFormProps) {
  const [message, setMessage] = useState("");

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    const evidence_ids = String(values.get("evidence_ids"))
      .split(",")
      .map((id) => id.trim())
      .filter(Boolean);

    try {
      const card = await createDecision({
        dataset_version_id: datasetVersionId,
        title: String(values.get("title")),
        evidence_ids,
        problem_statement: String(values.get("problem_statement")),
        hypothesis: String(values.get("hypothesis")),
        primary_metric: String(values.get("primary_metric")),
        guardrail_metric: String(values.get("guardrail_metric")),
        owner: String(values.get("owner")),
        due_date: String(values.get("due_date")),
        business_fit: 70,
      });
      onCreated(card);
      setMessage("决策卡已创建。");
      form.reset();
    } catch {
      setMessage("创建失败：请确认所有字段和证据 ID 均来自当前数据版本。");
    }
  };

  const omittedEvidenceCount = initialValues?.omitted_evidence_count ?? 0;

  return (
    <form className="decision-form" id="experiment-form" onSubmit={submit}>
      <h2>{initialValues ? "确认实验并创建决策卡" : "创建决策卡"}</h2>
      {initialValues && (
        <p className="version-note">AI 只预填服务端已验证的实验草案；负责人和截止日期仍需人工确认。</p>
      )}
      <label>
        标题
        <input defaultValue={initialValues?.title} name="title" required maxLength={120} />
      </label>
      <label>
        证据 ID（逗号分隔）
        <input
          defaultValue={initialValues?.evidence_ids.join(", ")}
          name="evidence_ids"
          required
          placeholder="review-1, review-2"
        />
      </label>
      {omittedEvidenceCount > 0 && (
        <p className="audit-warning">
          为符合接口上限，已预填前 8 条支持证据；另有 {omittedEvidenceCount} 条未自动带入。
        </p>
      )}
      <label>
        问题陈述
        <textarea defaultValue={initialValues?.problem_statement} name="problem_statement" required />
      </label>
      <label>
        假设
        <textarea defaultValue={initialValues?.hypothesis} name="hypothesis" required />
      </label>
      <div>
        <label>
          主指标
          <input defaultValue={initialValues?.primary_metric} name="primary_metric" required />
        </label>
        <label>
          护栏指标
          <input defaultValue={initialValues?.guardrail_metric} name="guardrail_metric" required />
        </label>
      </div>
      <div>
        <label>
          负责人
          <input name="owner" required />
        </label>
        <label>
          截止日期
          <input name="due_date" type="date" required />
        </label>
      </div>
      <button type="submit">创建并评分</button>
      {message && <small aria-live="polite">{message}</small>}
    </form>
  );
}
