import "@testing-library/jest-dom/vitest";
import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import EmbodiedPage from "../src/app/embodied/page";

const mocks = vi.hoisted(() => ({
  getEmbodiedTasks: vi.fn(),
  getEmbodiedEpisode: vi.fn(),
  getEmbodiedDiagnosis: vi.fn(),
  getEmbodiedExperiment: vi.fn(),
  getEmbodiedDatasets: vi.fn(),
}));

vi.mock("next/navigation", () => ({ usePathname: () => "/embodied" }));
vi.mock("../src/lib/api", () => mocks);

const task = {
  task_id: "pick_place",
  instruction: "把红色方块放入蓝色托盘",
  metrics: {
    episode_count: 30,
    success_count: 5,
    success_rate: { numerator: 5, denominator: 30, rate: 1 / 6 },
    grasp_success_rate: { numerator: 5, denominator: 30, rate: 1 / 6 },
    placement_success_rate: { numerator: 5, denominator: 30, rate: 1 / 6 },
    collision_rate: { numerator: 5, denominator: 30, rate: 1 / 6 },
    timeout_rate: { numerator: 5, denominator: 30, rate: 1 / 6 },
    mean_completion_time_s: 8.2,
    per_phase_failure_rate: [
      { phase: "approach", failure_count: 5, denominator: 30, failure_rate: 1 / 6 },
      { phase: "align", failure_count: 5, denominator: 30, failure_rate: 1 / 6 },
      { phase: "grasp", failure_count: 5, denominator: 30, failure_rate: 1 / 6 },
      { phase: "transfer", failure_count: 5, denominator: 30, failure_rate: 1 / 6 },
      { phase: "place", failure_count: 5, denominator: 30, failure_rate: 1 / 6 },
    ],
    failure_distribution: [{ failure_type: "grasp_miss", count: 5 }],
  },
};

const episode = {
  dataset_version_id: "embodied-demo-v1",
  episode: { episode_id: "embodied-demo-0001", task_id: "pick_place", scene_id: "scene-01", seed: 7, instruction: "把红色方块放入蓝色托盘", robot_model: "arm6_gripper", observations: [], events: [], outcome: { success: false, failure_type: "grasp_miss", completion_time_s: 7.2 } },
  phases: ["approach", "align", "grasp", "transfer", "place"].map((phase, index) => ({ phase, start_t: index, end_t: index + 1, observation_start: index, observation_end: index + 1, event_ids: [] })),
  features: { phase_features: [] },
  events: [{ event_id: "embodied-demo-0001-event-001", t: 2.4, event_type: "grasp_contact", severity: "warning" }],
  outcome: { success: false, failure_type: "grasp_miss", completion_time_s: 7.2 },
};

describe("EmbodiedOps workspace", () => {
  it("switches to a public success baseline without presenting a failure diagnosis", async () => {
    mocks.getEmbodiedDatasets.mockResolvedValue({
      datasets: [
        {
          dataset_version_id: "embodied-demo-v1", source_name: "synthetic_tabletop_fixture",
          source_url: null, file_hash: "demo-hash", episode_count: 30, real_robot_data: false,
          data_type: "synthetic_simulation", purpose: "failure_diagnosis_regression",
          default_episode_id: "embodied-demo-0001", task_count: 1,
          success_rate: { numerator: 5, denominator: 30, rate: 1 / 6 }, interpretation: "failure fixture",
        },
        {
          dataset_version_id: "robomimic-lift-ph-low-dim-v1", source_name: "robomimic_v0.1_lift_proficient_human_low_dim",
          source_url: "https://downloads.cs.stanford.edu/example", file_hash: "public-hash", episode_count: 200, real_robot_data: false,
          data_type: "public_simulation", purpose: "success_replay_baseline",
          default_episode_id: "robomimic-lift-0", task_count: 1,
          success_rate: { numerator: 200, denominator: 200, rate: 1 }, interpretation: "public success only",
        },
      ],
    });
    mocks.getEmbodiedTasks.mockImplementation((id: string) => Promise.resolve({
      dataset_version_id: id, tasks: [{ ...task, metrics: { ...task.metrics, episode_count: id.startsWith("robomimic") ? 200 : 30 } }],
    }));
    mocks.getEmbodiedEpisode.mockImplementation((_: string, id: string) => Promise.resolve({ ...episode, dataset_version_id: id }));
    mocks.getEmbodiedDiagnosis.mockResolvedValue(null);

    render(<EmbodiedPage />);

    const selector = await screen.findByLabelText("数据集版本");
    fireEvent.change(selector, { target: { value: "robomimic-lift-ph-low-dim-v1" } });

    expect(await screen.findByText("公开仿真成功基线")).toBeVisible();
    expect(screen.getByText("public success only")).toBeVisible();
    expect(screen.getByText("不生成失败诊断")).toBeVisible();
    expect(mocks.getEmbodiedTasks).toHaveBeenLastCalledWith(
      "robomimic-lift-ph-low-dim-v1", expect.anything(),
    );
  });

  it("shows metrics, phase timeline, diagnosis trace and honest offline evaluation", async () => {
    mocks.getEmbodiedDatasets.mockResolvedValue({ datasets: [] });
    mocks.getEmbodiedTasks.mockResolvedValue({ dataset_version_id: "embodied-demo-v1", tasks: [task] });
    mocks.getEmbodiedEpisode.mockResolvedValue(episode);
    mocks.getEmbodiedDiagnosis.mockResolvedValue({ status: "completed", dataset_version_id: "embodied-demo-v1", episode_id: "embodied-demo-0001", payload: { decision_status: "needs_evidence", failure_phase: "grasp", validation_code: "MISSING_COUNTER_EVIDENCE", supporting_event_ids: ["embodied-demo-0001-event-001"], unknowns: ["没有反例证据"] }, trace_ids: ["trace-emb-001"] });
    mocks.getEmbodiedExperiment.mockResolvedValue({ hypothesis: "降低抓取接触偏差", variable: "approach_offset_mm", controlled_conditions: ["同一场景", "同一随机种子"], minimum_sample_count: 30, primary_metric: "success_rate", guardrail_metrics: ["collision_rate"], execution_mode: "simulation_only", human_owner: "human_review_required" });

    render(<EmbodiedPage />);

    expect(await screen.findByRole("heading", { name: "EmbodiedOps" })).toBeVisible();
    expect(screen.getByText("成功率")).toBeVisible();
    expect(screen.getByText("抓取阶段")).toBeVisible();
    expect(screen.getByText("MISSING_COUNTER_EVIDENCE")).toBeVisible();
    expect(screen.getByText("trace-emb-001")).toBeVisible();
    expect(screen.getByText("离线验证，不代表真实机器人效果")).toBeVisible();
    expect(screen.getByText("仅仿真 / 只读演示")).toBeVisible();
  });

  it("replays local observations and connects evidence without a robot action", async () => {
    mocks.getEmbodiedDatasets.mockResolvedValue({ datasets: [] });
    mocks.getEmbodiedTasks.mockResolvedValue({ dataset_version_id: "embodied-demo-v1", tasks: [task] });
    mocks.getEmbodiedEpisode.mockResolvedValue({
      ...episode,
      episode: {
        ...episode.episode,
        observations: [
          { t: 0, camera_frame_id: "frame-000", joint_positions: [0, 0, 0, 0, 0, 0], end_effector_pose: [0.42, 0.05, 0.12, 0, 0, 0, 1], gripper_width: 0.045, gripper_force: 4 },
          { t: 2.4, camera_frame_id: "frame-002", joint_positions: [0.04, 0.04, 0.04, 0.04, 0.04, 0.04], end_effector_pose: [0.45, 0.05, 0.14, 0, 0, 0, 1], gripper_width: 0.029, gripper_force: 5 },
        ],
      },
    });
    mocks.getEmbodiedDiagnosis.mockResolvedValue({
      status: "completed", dataset_version_id: "embodied-demo-v1", episode_id: "embodied-demo-0001",
      payload: { decision_status: "needs_evidence", failure_phase: "grasp", validation_code: "MISSING_COUNTER_EVIDENCE", supporting_event_ids: ["embodied-demo-0001-event-001"], counter_event_ids: [], unknowns: ["no counter evidence"] },
      trace_ids: ["trace-emb-001"],
    });
    mocks.getEmbodiedExperiment.mockResolvedValue({
      hypothesis: "reduce contact error", variable: "approach_offset_mm", controlled_conditions: ["same scene"], minimum_sample_count: 30,
      primary_metric: "success_rate", guardrail_metrics: ["collision_rate"], execution_mode: "simulation_only", human_owner: "human_review_required",
    });

    render(<EmbodiedPage />);

    expect(await screen.findByText("现象 → 阶段 → 证据 → 诊断 → 实验")).toBeVisible();
    expect(screen.getAllByText("不控制机器人")[0]).toBeVisible();
    expect(screen.getAllByText("simulation_only")[0]).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: /grasp_contact/i }));

    expect(screen.getByText("选中事件")).toBeVisible();
    expect(screen.getByText("2.40s")).toBeVisible();
    expect(screen.getByText("warning")).toBeVisible();
    expect(screen.getByText("frame-002")).toBeVisible();
  });
});
