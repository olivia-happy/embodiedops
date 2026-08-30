import type { DecisionCard, DecisionMemo, EmbodiedDatasetsResponse, EmbodiedDiagnosisResponse, EmbodiedEpisodeResponse, EmbodiedExperiment, EmbodiedTasksResponse, Evidence, InsightsResponse, MemoGenerationJob, ModelHealth, OverviewResponse, Trace } from "./types";

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

export async function getOverview(signal?: AbortSignal): Promise<OverviewResponse> {
  const response = await fetch(`${apiBaseUrl}/api/v1/overview`, { cache: "no-store", signal });
  if (!response.ok) throw new Error("无法加载决策总览");
  return response.json() as Promise<OverviewResponse>;
}

export async function getInsights(aspect?: string, signal?: AbortSignal): Promise<InsightsResponse> {
  const query = aspect ? `?aspect=${encodeURIComponent(aspect)}` : "";
  const response = await fetch(`${apiBaseUrl}/api/v1/insights${query}`, { cache: "no-store", signal });
  if (!response.ok) throw new Error("无法加载用户洞察");
  return response.json() as Promise<InsightsResponse>;
}

async function fetchEvidenceBatch(datasetVersionId: string, ids: string[], signal?: AbortSignal): Promise<Evidence[]> {
  const query = new URLSearchParams({ dataset_version_id: datasetVersionId });
  ids.forEach((id) => query.append("evidence_id", id));
  const sameOriginBase = apiBaseUrl || (typeof window === "undefined" ? "" : window.location.origin);
  const response = await fetch(`${sameOriginBase}/api/v1/evidence?${query.toString()}`, { cache: "no-store", signal });
  if (!response.ok) throw new Error("无法加载原始证据");
  return response.json() as Promise<Evidence[]>;
}

export async function getEvidence(datasetVersionId: string, ids: string[], signal?: AbortSignal): Promise<Evidence[]> {
  const uniqueIds = [...new Set(ids)];
  if (uniqueIds.length === 0) return [];

  const batches = Array.from(
    { length: Math.ceil(uniqueIds.length / 8) },
    (_, index) => uniqueIds.slice(index * 8, index * 8 + 8),
  );
  const responses = await Promise.all(batches.map((batch) => fetchEvidenceBatch(datasetVersionId, batch, signal)));
  const byId = new Map(responses.flat().map((item) => [item.id, item]));
  return uniqueIds.flatMap((id) => byId.has(id) ? [byId.get(id)!] : []);
}

export async function getRisks(): Promise<Pick<OverviewResponse, "active_dataset" | "risks">> {
  const response = await fetch(`${apiBaseUrl}/api/v1/risks`, { cache: "no-store" });
  if (!response.ok) throw new Error("无法加载产业风险");
  return response.json() as Promise<Pick<OverviewResponse, "active_dataset" | "risks">>;
}

export async function createDecision(body: Omit<DecisionCard, "id" | "score" | "score_breakdown" | "status"> & { business_fit?: number }): Promise<DecisionCard> {
  const response = await fetch(`${apiBaseUrl}/api/v1/decisions`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  if (!response.ok) throw new Error("创建决策卡失败，请检查必填项和证据 ID。");
  return response.json() as Promise<DecisionCard>;
}

export async function submitFeedback(entity_type: "insight" | "decision" | "risk", entity_id: string, decision: "confirmed" | "rejected" | "edited", reason?: string): Promise<void> {
  const response = await fetch(`${apiBaseUrl}/api/v1/feedback`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ entity_type, entity_id, decision, reason }) });
  if (!response.ok) throw new Error("保存反馈失败。");
}

export async function getTraces(entityId: string): Promise<Trace[]> {
  const response = await fetch(`${apiBaseUrl}/api/v1/traces/${encodeURIComponent(entityId)}`, { cache: "no-store" });
  if (!response.ok) throw new Error("无法加载 Trace。 ");
  const body = await response.json() as { traces: Trace[] };
  return body.traces;
}

async function requestMemo<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, { cache: "no-store", ...init });
  if (!response.ok) throw new Error("决策备忘录服务暂不可用，请稍后重试。");
  return response.json() as Promise<T>;
}

export async function getDecisionMemo(datasetVersionId: string, signal?: AbortSignal): Promise<DecisionMemo | null> {
  const response = await fetch(`${apiBaseUrl}/api/v1/decision-memo?dataset_version_id=${encodeURIComponent(datasetVersionId)}`, { cache: "no-store", signal });
  if (response.status === 404) return null;
  if (!response.ok) throw new Error("无法加载决策备忘录，请稍后重试。");
  return response.json() as Promise<DecisionMemo>;
}

export function startDecisionMemo(datasetVersionId: string): Promise<MemoGenerationJob> {
  return requestMemo<MemoGenerationJob>("/api/v1/decision-memo/generate", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ dataset_version_id: datasetVersionId }) });
}

export function getDecisionMemoJob(jobId: string, signal?: AbortSignal): Promise<MemoGenerationJob> {
  return requestMemo<MemoGenerationJob>(`/api/v1/decision-memo/jobs/${encodeURIComponent(jobId)}`, { signal });
}

export function getModelHealth(signal?: AbortSignal): Promise<ModelHealth> {
  return requestMemo<ModelHealth>("/api/healthz/model", { signal });
}

export function getEmbodiedTasks(datasetVersionId: string, signal?: AbortSignal): Promise<EmbodiedTasksResponse> {
  return requestMemo<EmbodiedTasksResponse>(`/api/v1/embodied/tasks?dataset_version_id=${encodeURIComponent(datasetVersionId)}`, { signal });
}

export function getEmbodiedDatasets(signal?: AbortSignal): Promise<EmbodiedDatasetsResponse> {
  return requestMemo<EmbodiedDatasetsResponse>("/api/v1/embodied/datasets", { signal });
}

export function getEmbodiedEpisode(episodeId: string, datasetVersionId: string, signal?: AbortSignal): Promise<EmbodiedEpisodeResponse> {
  return requestMemo<EmbodiedEpisodeResponse>(`/api/v1/embodied/episodes/${encodeURIComponent(episodeId)}?dataset_version_id=${encodeURIComponent(datasetVersionId)}`, { signal });
}

export function getEmbodiedDiagnosis(diagnosisId: string, signal?: AbortSignal): Promise<EmbodiedDiagnosisResponse> {
  return requestMemo<EmbodiedDiagnosisResponse>(`/api/v1/embodied/diagnoses/${encodeURIComponent(diagnosisId)}`, { signal });
}

export function getEmbodiedExperiment(diagnosisId: string, signal?: AbortSignal): Promise<EmbodiedExperiment> {
  return requestMemo<EmbodiedExperiment>(`/api/v1/embodied/experiments/${encodeURIComponent(diagnosisId)}`, { signal });
}
