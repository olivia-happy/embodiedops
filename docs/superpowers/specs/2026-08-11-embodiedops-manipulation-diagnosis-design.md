# EmbodiedOps：桌面抓取任务失败诊断与策略迭代平台

## 1. 产品定位

EmbodiedOps 面向机器人算法、具身智能产品和数据闭环团队，解决“任务失败后只能看结果、无法快速定位原因、无法稳定复现和验证修复”的问题。

第一版聚焦 6 自由度机械臂 + 两指夹爪的桌面抓取与放置任务。产品不直接控制真实机器人，而是把仿真 episode 与少量公开真实轨迹转换为可追溯的失败诊断、复现实验和策略评测。

核心承诺：

- 每个诊断结论都绑定任务、数据集版本和时间段证据。
- 数字、成功率、失败率和最终评测状态由确定性服务端规则计算。
- AI 只能在受控证据集合中提出机制解释和实验草案。
- 证据不足、引用未知或无法复现时，系统必须拒答或标记 needs_evidence。

## 2. 目标用户与使用场景

### 目标用户

- 机器人算法工程师：定位策略失败的阶段和根因。
- 具身智能产品经理：把失败模式转为可排期的实验和数据需求。
- 数据闭环团队：挖掘难例、维护数据版本和评测切片。
- 交付与测试团队：确认修复是否改善真实任务指标。

### 核心场景

用户选择一个数据集版本和任务，例如“把红色方块放入目标盒”。系统展示任务成功率、阶段失败率和失败类型 Pareto。用户打开某一条 episode，按时间轴查看相机帧、关节状态、末端位姿、夹爪状态、碰撞和超时事件。系统生成根因候选，但每个候选必须列出支持证据、反例、未知项和置信边界。用户可以把候选根因转成复现实验，比较修复前后的任务指标。

## 3. 数据策略

### 3.1 仿真主数据

使用 ManiSkill 或兼容的操作仿真环境生成 1,000～3,000 个 episode。首版任务保持桌面抓取与放置，改变物体类别、初始位姿、光照、遮挡、摩擦、相机噪声和控制延迟。

每个 episode 必须记录：

```text
episode_id, dataset_version, task_id, scene_id, seed
instruction, robot_model, object_category
observation_timestamps, camera_frame_ids
joint_states, end_effector_pose, gripper_width, gripper_force
action_sequence, collision_events, timeout_events
phase_timestamps, success, failure_type
```

仿真故障标签由环境或故障注入器产生，至少覆盖：抓取失败、目标遮挡、末端定位偏差、碰撞、规划不可达、动作超时和夹爪力度不足。

### 3.2 公开真实轨迹

使用少量 robomimic 或 DROID 轨迹验证统一 schema、真实图像噪声、动作格式和跨场景分布。公开轨迹只作为外部校验，不把其许可、标签或任务语义假设为本项目自有数据。

### 3.3 数据版本与许可

每次导入生成不可变 dataset version，记录来源、文件 hash、字段映射、行数和导入时间。所有公开数据必须在数据卡中记录许可证、引用、下载时间和可再分发边界。当前项目不把真实 Unitree 生产日志作为既成事实；未来通过 adapter 接入真机或官方仿真日志。

## 4. 系统架构

```text
Simulator / Public Trajectories
        |
        v
ETL + Schema Normalizer + Dataset Versioning
        |
        v
Episode Store / Event Store / Feature Views
        |
        +--> Deterministic Metrics & Phase Rules
        |
        +--> Controlled Agent Orchestrator
        |       |-> Episode Analyzer
        |       |-> Failure Classifier
        |       |-> Root Cause Reviewer
        |       `-> Experiment Planner
        |
        v
Evidence Validator + Safety Gates + Trace
        |
        v
Next.js Mission Overview / Timeline / Diagnosis / Evaluation
```

### 4.1 Episode 分析层

先将连续轨迹切成接近、对准、抓取、搬运和放置阶段。阶段切分优先使用时间戳、末端位姿、夹爪状态和事件规则；模型不能自行发明阶段边界。

### 4.2 受控 Agent 层

四个阶段共享统一的 episode context，但每阶段都有严格输出 schema：

1. `Episode Analyzer`：输出阶段候选、异常时间窗和使用的字段。
2. `Failure Classifier`：从允许的失败类型集合中选择候选并给出证据 ID。
3. `Root Cause Reviewer`：输出机制解释、支持证据、反例证据和未知项。
4. `Experiment Planner`：输出变量、复现条件、样本量、成功指标、护栏指标和停止条件。

它们是受控 Agentic workflow，不是可以直接发出机器人控制指令的自主 Agent。所有模型输出必须通过结构化 schema、episode 版本边界和证据白名单验证。

### 4.3 规则与安全边界

服务端拥有以下事实：成功率、失败率、阶段耗时、碰撞计数、样本数、数据版本和最终状态。模型不得覆盖这些事实。未知字段、未知 episode、未知事件 ID、时间窗越界、脱敏内容误用和反例遗漏都会触发拒答或 needs_evidence。

## 5. AI 模型与推理策略

第一版支持本地 Ollama provider。模型、量化、prompt version、配置、耗时和错误码必须写入 Trace。在线模型和云端 API 不属于默认路径，也不在未批准时启用。

模型适合做：

- 从结构化事件和受限轨迹摘要中提炼机制候选。
- 把诊断结果转成工程师可读的实验草案。

模型不适合做：

- 计算成功率、失败率或安全阈值。
- 直接决定是否部署策略。
- 凭空补充传感器读数、任务结果或实验效果。

如果本地模型超时或无法通过 schema，系统保留失败 Trace，不把失败伪装成成功；对离线展示，可使用明确标记的验证快照，但不得称为实时模型产出。

## 6. 评测体系

### 6.1 任务层指标

- task success rate
- grasp success rate
- placement success rate
- collision rate
- timeout rate
- average completion time

### 6.2 诊断层指标

- failure phase accuracy
- root-cause evidence precision
- root-cause evidence coverage
- unknown-cause refusal rate
- replay reproducibility

### 6.3 产品层指标

- 从失败 episode 到可复现实验的时间。
- 算法工程师人工排查时长。
- 诊断建议采纳率。
- 修复后成功率提升。
- 错误策略进入真机灰度的比例。

固定合成案例只能验证代码门禁和指标实现，不能证明真实模型质量。正式评测必须冻结模型 digest、数据版本、prompt、temperature、seed 和硬件，并将 raw model 指标与 post-validation system 指标分开报告。

## 7. 前端信息架构

### Mission Overview

显示当前数据集版本、任务数量、总体成功率、阶段失败率、失败类型 Pareto 和待处理高风险模式。

### Episode Timeline

以时间轴同步相机帧、关节曲线、末端位姿、夹爪状态、动作和碰撞事件。所有时间窗均显示来源字段和 dataset version。

### Failure Diagnosis

展示失败阶段、根因候选、支持证据、反例证据、未知项、模型 Trace 和“证据不足”状态。

### Experiment Builder

从诊断候选生成复现实验草案，但责任人、数据采集、上线和真机执行必须由人确认。

### Evaluation Compare

对比修复前后的成功率、失败类型、碰撞率、耗时和切片表现；不允许只展示单一平均分。

## 8. MVP 验收标准

1. 至少 1,000 个仿真 episode 可导入、版本化和回放。
2. 至少覆盖 5 类可解释失败模式。
3. 任一诊断候选都能回到 episode、字段和时间窗证据。
4. 未知字段或未知事件 ID 不得进入最终诊断。
5. 无法判断根因时返回 refused 或 needs_evidence。
6. 至少完成一组修复前后对比实验。
7. 仿真数据和公开真实轨迹通过同一 schema 校验。
8. Trace 能记录数据版本、模型、prompt、阶段、耗时和验证状态。
9. 前端能完整展示失败时间线、根因证据和实验结果。
10. 演示模式默认只读，不向真实机器人发送控制命令。

## 9. 非目标与风险

第一版不实现：真实机器人控制、在线策略部署、全自动数据标注、全量 Open X-Embodiment 训练、云端模型调用和真机安全认证。

主要风险：仿真故障与真机故障存在分布差异；公开真实轨迹标签可能不完整；时间同步和阶段切分误差会影响根因诊断；本地模型延迟可能不满足在线要求。每项风险都必须在产品页面和面试叙事中显式披露。

## 10. 面试叙事

一句话：

> EmbodiedOps 不是让大模型直接控制机器人，而是把机器人失败 episode 转成有证据、可复现、可评测的策略迭代闭环。

面试中必须区分：已实现的本地验证闭环、固定合成数据的回归指标、尚未完成的真机 Pilot，以及当前模型在本机硬件上的延迟限制。
