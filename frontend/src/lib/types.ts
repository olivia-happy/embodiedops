export type OverviewResponse = {
  active_dataset: { id: string; source_name: string; row_count: number; imported_at: string; is_stale: boolean };
  summary_metrics: { review_count: number; negative_review_count: number; negative_review_rate: number | null; topic_count: number; market_event_count: number };
  opportunities: Array<{
    id: string;
    title: string;
    dataset_version_id: string;
    aspect: string;
    review_count: number;
    negative_count: number;
    negative_rate: number | null;
    score: number | null;
    scoreable: boolean;
    missing_fields: string[];
    contributions: Record<string, number>;
    evidence_ids: string[];
    evidence_count: number;
  }>;
  risks: Array<{ id: string; dataset_version_id: string; source_url: string; published_on: string; excerpt: string; event_type: "policy" | "market" | "talent"; industry: string; evidence_quality: number; score: number; contributions: Record<string, number>; mitigation_action: string; owner: string; status: "monitoring" | "needs_review" }>;
};

export type Evidence = { id: string; dataset_version_id: string; content: string; source_type: "review" | "market_event"; rating: number | null; aspect: string | null; sentiment: "positive" | "neutral" | "negative" | "unknown"; relevance_score: number | null; redacted: boolean };
export type InsightsResponse = {
  active_dataset: OverviewResponse["active_dataset"];
  metrics: Array<{ aspect: string; review_count: number; negative_count: number; negative_rate: number | null; average_rating: number | null; severity: number | null; scoreable: boolean }>;
  insights: Array<{ id: string; title: string; claim: string; confidence: number; evidence_ids: string[]; unknowns: string[]; recommended_action: string }>;
};
export type DecisionCard = { id: string; dataset_version_id: string; title: string; evidence_ids: string[]; problem_statement: string; hypothesis: string; primary_metric: string; guardrail_metric: string; owner: string; due_date: string; score: number | null; score_breakdown: Record<string, number>; status: string };
export type TraceStage = "generation" | "analyzing_signals" | "grouping_evidence" | "validating_evidence" | "generating_memo";
export type Trace = {
  id: string;
  entity_type: "insight" | "decision" | "risk" | "memo";
  entity_id: string;
  dataset_version_id: string;
  prompt_version: string;
  model_name: string | null;
  provider: string | null;
  stage: TraceStage;
  retry_count: number;
  evidence_ids: string[];
  validation_status: "accepted" | "refused" | "invalid" | "retried" | "fallback" | "numeric_mismatch" | "failed";
  latency_ms: number;
  token_estimate: number;
  created_at: string;
};

export type MemoFacts = { review_count: number; negative_count: number; negative_rate: number | null; average_rating: number | null; severity: number | null; affected: number | null; evidence: number | null; opportunity_score: number | null; scoreable: boolean; missing_fields: string[] };
export type MemoEvidenceReference = { evidence_id: string; rationale: string };
export type ExperimentPlan = { hypothesis: string; target_segment: string; intervention: string; primary_metric: string; guardrail_metric: string; duration_days: number; stop_conditions: string[] };
export type EvidencePlan = { candidate_subproblems: string[]; collection_fields: string[]; minimum_evidence_per_subproblem: number; reassessment_condition: string };
export type DecisionMemo = { id: string; dataset_version_id: string; decision_status: "actionable" | "needs_evidence" | "refused"; decision_statement: string; topic: string; subproblem: string | null; facts: MemoFacts; supporting_evidence: MemoEvidenceReference[]; counter_evidence: MemoEvidenceReference[]; counter_evidence_checked: boolean; unknowns: string[]; reasoning_summary: string; experiment: ExperimentPlan | null; evidence_plan: EvidencePlan | null; refusal_reason: string | null; model_name: string | null; prompt_version: string };
export type MemoGenerationJob = { id: string; dataset_version_id: string; status: "queued" | "analyzing_signals" | "grouping_evidence" | "validating_evidence" | "generating_memo" | "completed" | "failed"; memo_id: string | null; error_code: string | null; created_at: string; updated_at: string };
export type ModelHealth = { configured: boolean; ready: boolean; provider: "ollama"; model_name: string | null; error_code: string | null };

export type EmbodiedRateMetric = { numerator: number; denominator: number; rate: number | null };
export type EmbodiedPhaseFailure = { phase: "approach" | "align" | "grasp" | "transfer" | "place"; failure_count: number; denominator: number; failure_rate: number | null };
export type EmbodiedTaskMetrics = {
  episode_count: number;
  success_count: number;
  success_rate: EmbodiedRateMetric;
  grasp_success_rate: EmbodiedRateMetric;
  placement_success_rate: EmbodiedRateMetric;
  collision_rate: EmbodiedRateMetric;
  timeout_rate: EmbodiedRateMetric;
  mean_completion_time_s: number | null;
  per_phase_failure_rate: EmbodiedPhaseFailure[];
  failure_distribution: Array<{ failure_type: string; count: number }>;
};
export type EmbodiedTasksResponse = { dataset_version_id: string; tasks: Array<{ task_id: string; metrics: EmbodiedTaskMetrics }> };
export type EmbodiedDatasetCatalogItem = {
  dataset_version_id: string;
  source_name: string;
  source_url: string | null;
  file_hash: string;
  episode_count: number;
  real_robot_data: boolean;
  data_type: "synthetic_simulation" | "public_simulation";
  purpose: "failure_diagnosis_regression" | "success_replay_baseline";
  default_episode_id: string | null;
  task_count: number;
  success_rate: EmbodiedRateMetric | null;
  interpretation: string;
};
export type EmbodiedDatasetsResponse = { datasets: EmbodiedDatasetCatalogItem[] };
export type EmbodiedPhaseWindow = { phase: EmbodiedPhaseFailure["phase"]; start_t: number; end_t: number; observation_start: number; observation_end: number; event_ids: string[] };
export type EmbodiedEpisodeEvent = { event_id: string; t: number; event_type: string; severity: "info" | "warning" | "critical" };
export type EmbodiedReplayFrame = {
  t: number;
  camera_frame_id: string;
  joint_positions: number[];
  end_effector_pose: number[];
  gripper_width: number;
  gripper_force: number | null;
};
export type EmbodiedEpisodeResponse = {
  dataset_version_id: string;
  episode: { episode_id: string; task_id: string; scene_id: string; seed: number; instruction: string; robot_model: string; observations: EmbodiedReplayFrame[]; events: EmbodiedEpisodeEvent[]; outcome: { success: boolean; failure_type: string | null; completion_time_s: number } };
  phases: EmbodiedPhaseWindow[];
  features: Record<string, unknown>;
  events: EmbodiedEpisodeEvent[];
  outcome: { success: boolean; failure_type: string | null; completion_time_s: number };
};
export type EmbodiedDiagnosisResponse = {
  id?: string;
  status: "queued" | "completed" | "failed";
  dataset_version_id: string;
  episode_id: string;
  validation_code: string | null;
  payload: { decision_status: "actionable" | "needs_evidence" | "refused"; failure_phase: EmbodiedPhaseFailure["phase"]; validation_code?: string; supporting_event_ids: string[]; counter_event_ids?: string[]; unknowns: string[] } | null;
  trace_ids: string[];
};
export type EmbodiedExperiment = { hypothesis: string; variable: string; controlled_conditions: string[]; minimum_sample_count: number; primary_metric: string; guardrail_metrics: string[]; stop_or_reassessment: string[]; execution_mode: "simulation_only"; human_owner: "human_review_required" };
