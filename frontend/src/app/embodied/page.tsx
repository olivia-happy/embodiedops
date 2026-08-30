"use client";

import React, { useEffect, useMemo, useState } from "react";
import { Navigation } from "@/components/navigation";
import {
  getEmbodiedDiagnosis,
  getEmbodiedEpisode,
  getEmbodiedExperiment,
  getEmbodiedTasks,
} from "@/lib/api";
import type {
  EmbodiedDiagnosisResponse,
  EmbodiedEpisodeEvent,
  EmbodiedEpisodeResponse,
  EmbodiedExperiment,
  EmbodiedReplayFrame,
  EmbodiedTaskMetrics,
} from "@/lib/types";

const DATASET_VERSION = process.env.NEXT_PUBLIC_EMBODIED_DATASET_VERSION ?? "embodied-demo-v1";
const EPISODE_ID = process.env.NEXT_PUBLIC_EMBODIED_EPISODE_ID ?? "embodied-demo-0001";
const DIAGNOSIS_ID = process.env.NEXT_PUBLIC_EMBODIED_DIAGNOSIS_ID ?? "diag-demo-0001";
const DEMO_READ_ONLY = process.env.NEXT_PUBLIC_DEMO_READ_ONLY === "true";

const phaseLabels: Record<string, string> = {
  approach: "接近",
  align: "对齐",
  grasp: "抓取",
  transfer: "搬运",
  place: "放置",
};

function percent(rate: number | null | undefined) {
  return rate == null ? "—" : `${(rate * 100).toFixed(0)}%`;
}

function Metric({ label, value, detail }: { label: string; value: string; detail: string }) {
  return <article className="embodied-metric"><span>{label}</span><strong>{value}</strong><small>{detail}</small></article>;
}

function nearestFrame(frames: EmbodiedReplayFrame[], event?: EmbodiedEpisodeEvent) {
  if (!event || frames.length === 0) return frames[0] ?? null;
  return frames.reduce((nearest, frame) => Math.abs(frame.t - event.t) < Math.abs(nearest.t - event.t) ? frame : nearest);
}

function LoadingState() {
  return <section className="state-panel embodied-state">正在读取仿真轨迹与版本化指标…</section>;
}

export default function EmbodiedPage() {
  const [metrics, setMetrics] = useState<EmbodiedTaskMetrics | null>(null);
  const [episode, setEpisode] = useState<EmbodiedEpisodeResponse | null>(null);
  const [diagnosis, setDiagnosis] = useState<EmbodiedDiagnosisResponse | null>(null);
  const [experiment, setExperiment] = useState<EmbodiedExperiment | null>(null);
  const [selectedEventId, setSelectedEventId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      getEmbodiedTasks(DATASET_VERSION, controller.signal),
      getEmbodiedEpisode(EPISODE_ID, DATASET_VERSION, controller.signal),
      getEmbodiedDiagnosis(DIAGNOSIS_ID, controller.signal).catch(() => null),
    ]).then(([tasks, nextEpisode, nextDiagnosis]) => {
      setMetrics(tasks.tasks[0]?.metrics ?? null);
      setEpisode(nextEpisode);
      setDiagnosis(nextDiagnosis);
      setSelectedEventId(nextEpisode.events[0]?.event_id ?? null);
      if (nextDiagnosis?.status === "completed") {
        getEmbodiedExperiment(DIAGNOSIS_ID, controller.signal).then(setExperiment).catch(() => setExperiment(null));
      }
    }).catch((cause: unknown) => {
      if ((cause as Error).name !== "AbortError") setError("无法读取 EmbodiedOps 数据，请确认后端与数据版本正在运行。");
    });
    return () => controller.abort();
  }, []);

  const selectedEvent = useMemo(
    () => episode?.events.find((event) => event.event_id === selectedEventId) ?? null,
    [episode, selectedEventId],
  );
  const selectedFrame = useMemo(
    () => nearestFrame(episode?.episode.observations ?? [], selectedEvent ?? undefined),
    [episode, selectedEvent],
  );
  const phaseFailures = useMemo(
    () => new Map((metrics?.per_phase_failure_rate ?? []).map((item) => [item.phase, item.failure_rate])),
    [metrics],
  );
  const supportingIds = diagnosis?.payload?.supporting_event_ids ?? [];
  const counterIds = diagnosis?.payload?.counter_event_ids ?? [];

  if (error) return <main className="app-shell"><Navigation /><section className="workspace"><header className="topbar"><div><p className="eyebrow">EMBODIEDOPS / DATA CONTRACT</p><h1>EmbodiedOps</h1></div></header><section className="state-panel error">{error}</section></section></main>;
  if (!metrics || !episode) return <main className="app-shell"><Navigation /><section className="workspace"><header className="topbar"><div><p className="eyebrow">EMBODIEDOPS / DATA CONTRACT</p><h1>EmbodiedOps</h1></div></header><LoadingState /></section></main>;

  return <main className="app-shell">
    <Navigation />
    <section className="workspace">
      <header className="topbar embodied-topbar">
        <div><p className="eyebrow">EMBODIEDOPS / FAILURE INTELLIGENCE</p><h1>EmbodiedOps</h1><p>把一次“抓取失败”拆成可复现的阶段证据、受控实验与人审决策。</p></div>
        <div className="data-status"><span>仅仿真 / 只读演示</span><small>dataset_version · {DATASET_VERSION}{DEMO_READ_ONLY ? " · writes blocked" : ""}</small></div>
      </header>
      <div className="embodied-page">
        <section className="embodied-hero">
          <div><p className="eyebrow">TASK / {episode.episode.task_id}</p><h2>桌面抓取：从失败现象到下一次可验证动作</h2><p>当前 episode <code>{episode.episode.episode_id}</code> 在 <strong>{episode.episode.scene_id}</strong> 中以 seed {episode.episode.seed} 重放。系统只消费观测与事件，不发送机器人控制指令。</p></div>
          <div className={`outcome-stamp ${episode.outcome.success ? "success" : "failed"}`}><strong>{episode.outcome.success ? "成功" : "失败"}</strong><small>{episode.outcome.failure_type ?? "—"}</small></div>
        </section>

        <section className="embodied-chain" aria-label="诊断证据链">
          <p className="eyebrow">READ-ONLY DECISION PATH</p>
          <h2>现象 → 阶段 → 证据 → 诊断 → 实验</h2>
          <ol>
            <li><span>现象</span><b>{episode.outcome.failure_type ?? "任务完成"}</b></li>
            <li><span>阶段</span><b>{phaseLabels[diagnosis?.payload?.failure_phase ?? ""] ?? "待判定"}</b></li>
            <li><span>证据</span><b>{supportingIds.length} 支持 / {counterIds.length} 反例</b></li>
            <li><span>诊断</span><b>{diagnosis?.payload?.decision_status ?? "待命"}</b></li>
            <li><span>实验</span><b>{experiment?.execution_mode ?? "不执行"}</b></li>
          </ol>
          <p>本链路为分析和实验设计，不控制机器人，也不会触发模型或执行实验。</p>
        </section>

        <section className="embodied-metrics" aria-label="任务指标">
          <Metric label="成功率" value={percent(metrics.success_rate.rate)} detail={`${metrics.success_count}/${metrics.episode_count} episodes`} />
          <Metric label="抓取成功率" value={percent(metrics.grasp_success_rate.rate)} detail="保守代理指标" />
          <Metric label="碰撞率" value={percent(metrics.collision_rate.rate)} detail={`${metrics.collision_rate.numerator} 次碰撞事件`} />
          <Metric label="平均完成时间" value={metrics.mean_completion_time_s == null ? "—" : `${metrics.mean_completion_time_s.toFixed(1)}s`} detail="从首帧到 outcome" />
        </section>

        <div className="embodied-grid">
          <section className="embodied-card timeline-card"><header className="embodied-card-heading"><div><p className="eyebrow">PHASE TRACE</p><h2>阶段时间线</h2></div><span>规则版本 deterministic_v1</span></header><div className="phase-timeline">{episode.phases.map((phase) => <article className={`phase-node ${phase.phase === diagnosis?.payload?.failure_phase ? "hot" : ""}`} key={phase.phase}><div className="phase-dot" /><div><strong>{phaseLabels[phase.phase] ?? phase.phase}阶段</strong><small>{phase.start_t.toFixed(1)}s — {phase.end_t.toFixed(1)}s</small><p>{phase.event_ids.length ? `${phase.event_ids.length} 个绑定事件` : "无异常事件"} · 失败率 {percent(phaseFailures.get(phase.phase))}</p></div></article>)}</div></section>

          <section className="embodied-card event-card"><header className="embodied-card-heading"><div><p className="eyebrow">OBSERVATION LOG</p><h2>事件证据</h2></div><span>{episode.events.length} events</span></header>{episode.events.length ? <ul className="event-list">{episode.events.map((event) => <li key={event.event_id}><button className={event.event_id === selectedEventId ? "selected" : ""} type="button" onClick={() => setSelectedEventId(event.event_id)} aria-pressed={event.event_id === selectedEventId}><code>{event.event_id}</code><span>{event.event_type}</span><small>{event.t.toFixed(2)}s · {event.severity}</small></button></li>)}</ul> : <p className="muted">本 episode 没有结构化事件；系统不会把空证据伪装成结论。</p>}</section>
        </div>

        <section className="embodied-card replay-card"><header className="embodied-card-heading"><div><p className="eyebrow">LOCAL TRAJECTORY REPLAY</p><h2>事件与帧回放</h2></div><span className="read-only-pill">不控制机器人</span></header><div className="replay-scrubber" role="list" aria-label="轨迹帧">{episode.episode.observations.map((frame) => <span className={selectedFrame?.camera_frame_id === frame.camera_frame_id ? "active" : ""} key={frame.camera_frame_id} role="listitem"><i />{frame.t.toFixed(1)}s</span>)}</div>{selectedEvent && selectedFrame ? <div className="replay-detail"><div><p className="eyebrow">选中事件</p><strong>{selectedEvent.event_type}</strong><dl><div><dt>时间</dt><dd>{selectedEvent.t.toFixed(2)}s</dd></div><div><dt>严重度</dt><dd>{selectedEvent.severity}</dd></div><div><dt>最近帧</dt><dd>{selectedFrame.camera_frame_id}</dd></div></dl></div><div className="frame-readout"><span>夹爪宽度</span><strong>{selectedFrame.gripper_width.toFixed(3)}m</strong><span>夹爪力</span><strong>{selectedFrame.gripper_force == null ? "未记录" : `${selectedFrame.gripper_force.toFixed(1)}N`}</strong><small>姿态 x/y/z：{selectedFrame.end_effector_pose.slice(0, 3).map((value) => value.toFixed(3)).join(" / ")}</small></div></div> : <p className="muted">没有可关联的事件或观测帧。</p>}</section>

        <section className="embodied-card diagnosis-card"><header className="embodied-card-heading"><div><p className="eyebrow">CONTROLLED AGENT</p><h2>诊断与 Trace</h2></div><span className="read-only-pill">不控制机器人</span></header>{diagnosis?.payload ? <div className="diagnosis-content"><div className="diagnosis-verdict"><strong>{phaseLabels[diagnosis.payload.failure_phase] ?? diagnosis.payload.failure_phase}阶段 · {diagnosis.payload.decision_status}</strong><code>{diagnosis.payload.validation_code ?? diagnosis.validation_code ?? "VALIDATED"}</code><p>Agent 只从 allowlist 事件中形成候选机制；没有反例证据时自动降级为 needs_evidence。</p></div><div className="diagnosis-facts"><div><span>支持事件</span><strong>{supportingIds.length}</strong></div><div><span>未知项</span><strong>{diagnosis.payload.unknowns.length}</strong></div><div><span>Trace ID</span><strong>{diagnosis.trace_ids[0] ?? "—"}</strong></div></div></div> : <div className="diagnosis-empty"><strong>{diagnosis?.status === "queued" ? "诊断任务排队中" : "诊断待命"}</strong><p>只读演示不会自动触发模型调用；需要由人审确认 episode、版本与实验边界。</p></div>}</section>

        <section className="embodied-card evidence-card"><header className="embodied-card-heading"><div><p className="eyebrow">EVIDENCE BOUNDARY</p><h2>支持、反例与未知</h2></div><span>fail-closed</span></header><div className="evidence-columns"><div><b>支持事件</b>{supportingIds.length ? supportingIds.map((id) => <code key={id}>{id}</code>) : <p>没有支持事件</p>}</div><div><b>反例事件</b>{counterIds.length ? counterIds.map((id) => <code key={id}>{id}</code>) : <p>没有反例；不提升置信度。</p>}</div><div><b>未知项</b>{diagnosis?.payload?.unknowns.length ? diagnosis.payload.unknowns.map((item) => <p key={item}>{item}</p>) : <p>诊断尚未生成</p>}</div></div></section>

        <section className="embodied-card experiment-card"><header className="embodied-card-heading"><div><p className="eyebrow">REPRODUCTION PLAN</p><h2>实验草案</h2></div><span>simulation_only</span></header>{experiment ? <div className="experiment-grid"><p><b>假设</b>{experiment.hypothesis}</p><p><b>变量</b><code>{experiment.variable}</code></p><p><b>样本量</b>{experiment.minimum_sample_count} episodes</p><p><b>主指标</b>{experiment.primary_metric}</p><p><b>护栏指标</b>{experiment.guardrail_metrics.join(" / ")}</p><p><b>执行人</b>{experiment.human_owner}</p></div> : <p className="muted">诊断完成后生成；当前不执行实验。</p>}</section>

        <aside className="evaluation-note"><strong>离线验证，不代表真实机器人效果</strong><p>评测来自可复现的合成 tabletop fixture（30 episodes，固定 dataset_version）。它验证 schema、阶段规则、证据引用与拒答边界，不等价于真实硬件成功率，也没有宣称 Unitree 生产数据。</p><div><span>real_robot_data = false</span><span>live_model = false</span><span>manual_review = required</span></div></aside>
      </div>
    </section>
  </main>;
}
