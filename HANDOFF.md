# 项目交接文档

## 1. 这是什么项目

**EmbodiedOps / SignalForge**：一个面向具身智能团队的本地、只读故障诊断工作台。
它把一次桌面抓取任务拆成可追溯的 episode、阶段、事件证据、受控诊断与仿真实验草案，
帮助机器人产品、算法和测试团队把"任务失败"转化为可复现的排查与验证问题。

**这不是机器人控制系统**：页面不发送控制命令，不接入真实机器人的执行链路，
也不持有 Unitree、优必选、傅利叶或其他公司的内部数据。

用途：秋招 GitHub 展示 + 面试 Demo（AI 产品经理岗，体现 AI 边界设计、评测方案与工程闭环）。

## 2. 技术栈与架构

- 后端：FastAPI + Python 3.11（本地运行，零付费 API / 云模型 / 商业数据；Ollama 为可选本地模型）
- 前端：Next.js + React + TypeScript
- 数据：本地 fixture（合成 episode JSONL + 不可变 manifest，SHA-256 校验）
- 质量：pytest + ruff（后端）、Vitest + ESLint（前端）
- 测试：后端 266 个用例 + 前端 42 个用例

### 目录结构

```
embodiedops/
├── backend/
│   ├── signalforge/
│   │   ├── api/routers/embodied.py    版本化 episode 与只读演示 API
│   │   ├── embodied/                  episode 契约、阶段/指标、受控诊断、实验与评测
│   │   │                              annotations / business_metrics / diagnosis / diagnosis_models /
│   │   │                              eval / experiment / metrics / models / phase_analysis /
│   │   │                              robomimic_import / robomimic_ingest / simulator
│   │   ├── core / db / etl / services
│   ├── tests/                          pytest 266 个用例（38 个文件 + conftest）
│   └── pyproject.toml
├── frontend/
│   ├── src/app/embodied/              只读回放、阶段时间线、证据链与指标界面
│   ├── src/app/                       原 SignalForge 决策工作台（decisions / insights / risks）
│   └── tests/                          Vitest 42 个用例
├── data/embodied/                      合成 fixture + 不可变 manifest
├── eval/                               可重复的离线评测输入与输出
├── docs/                               14 篇文档（PRD / CASE_STUDY / EVAL_REPORT / DATA_CARD /
│                                       DECISION_RULES / BUSINESS_EVAL / REAL_DATA_CARD /
│                                       ROBOMIMIC_SOURCE_CARD / INTERVIEW_STORY / PROJECT_PLAN ...）
├── scripts/                            verify_local / verify_model / import_robomimic_lift /
│                                       generate_embodied_demo / start|stop_demo_tunnel
└── docker-compose.yml
```

## 3. 当前功能状态（已完成并验证）

### 后端（266 个 pytest 用例）

- **episode 契约**：版本化 JSONL + manifest SHA-256 校验；fixture 缺失、hash 不匹配或版本冲突时**失败关闭**，不会以空指标运行
- **阶段切分**：`approach → align → grasp → transfer → place`
- **失败分布**：抓取未保持、遮挡、碰撞、规划不可达、超时、末端执行器未对齐、夹爪力不足
- **受控诊断 Agent**：仅在允许的 episode 摘要与事件 ID 范围内归纳候选机制；
  规则层验证事件、时间窗、数值主张与反例
- **fail-closed 安全边界**：未知事件、越界时间、捏造数值、反例缺失 → 输出 `needs_evidence` 或拒绝状态，而非编造结论
- **仿真实验草案**：输出为 `simulation_only`，人工审核后才进入仿真或真实机器人流程
- **只读演示模式**：`DEMO_READ_ONLY=true` 时后端拒绝写接口

### 前端（42 个 Vitest 用例）

- 只读回放、阶段时间线、证据抽屉、失败分布指标
- 演示模式下不展示诊断生成、重试或实验执行入口
- 原 SignalForge 决策工作台（决策备忘录、洞察、风险页）

### 离线评测工件

- `eval/embodied_gold_cases.jsonl`：固定诊断评测样例
- `eval/embodied_results.json`：诊断评测输出
- `eval/embodied_business_cases.jsonl`：**明确标注为合成的**业务指标样例

## 4. 本次对话总结（如何走到这一步）

### 需求演进

用户目标：秋招 AI 产品经理，统计学研究生，需要展示"AI 边界设计 + 评测方案 + 全栈工程"。
选择具身智能的**失败诊断**场景，因为它是"AI 到底该做什么、不该做什么"最容易被追问的领域——
诊断结论一旦编造，工程上就是灾难。

### 关键技术决策

1. **只读、不控制**：页面不发送控制命令。理由：诊断工具的价值在于可信，一旦能控制机器人，责任边界就模糊了。
2. **fail-closed 而不是 best-effort**：证据不足时输出 `needs_evidence` 或拒绝，而不是给一个"看着合理"的结论。
3. **模型与规则分工**：Agent 只在允许的摘要与事件 ID 范围内归纳候选机制；事件、时间窗、数值主张与反例由规则层验证。
4. **阶段切分做成确定性规则**：五阶段切分不交给模型，保证同一份 episode 每次得到同样的切分结果。
5. **demo 数据不可变**：manifest 带 SHA-256，版本冲突即失败关闭——避免演示数据被悄悄改掉后结论失真。
6. **评测与业务结果分开报告**：诊断规则质量、模型质量、人工接受率、业务结果四层分开；
   合成 before/after 样例**不构成生产 ROI 结论**。

### 已实现的重要模块

| 模块 | 说明 |
| --- | --- |
| phase_analysis | 五阶段确定性切分 |
| metrics / business_metrics | 失败分布与业务指标（业务指标明确标注为合成） |
| diagnosis + diagnosis_models | 受控诊断 Agent 与其数据契约 |
| eval | 离线评测输入/输出与 gold cases |
| experiment | simulation_only 实验草案 |
| robomimic_import / robomimic_ingest | 公开 RoboMimic 轨迹导入链路 |
| annotations | 人工标注与审核状态 |

### 已知风险与待办

1. **演示数据全部是合成数据**：30 条合成 tabletop pick-and-place episode，
   固定标注 `dataset_version=embodied-demo-v1` / `real_robot_data=false` / `live_model=false` /
   `manual_review_status=not_performed`。**没有公开真实机器人轨迹、生产日志或客户数据。**
2. **真实数据契约尚未被填充**：`docs/EMBODIEDOPS_REAL_DATA_CARD.md` 描述了未来接入经许可脱敏的真实导出时所需的
   来源、固件、任务族、授权、红线处理、holdout 与审核要求，但该契约当前**未被真实数据填充**。
3. **离线结果不等于实时模型质量证明**：本地 Ollama 可选用于受控诊断适配，
   但公开的离线评测结果**不能**当作实时模型质量声明。
4. **无 sim-to-real 结论**：合成 before/after 样例不构成生产 ROI 结论，也不代表 sim-to-real 成功率提升。
5. **人工接受率未测**：当前没有真实人工审核数据，`manual_review_status=not_performed`。
6. **隧道仅用于临时演示**：Cloudflare Quick Tunnel 无 SLA，地址会变更，
   **不能写入 README、简历或 GitHub**；公开前必须再跑 `backend/tests/test_public_artifact_safety.py`
   （检查真实密钥、数据库文件、临时隧道链接与原始模型输入/输出泄漏）。

## 5. 新电脑安装环境（完整步骤）

> 前提：Docker Desktop（推荐）或 Python 3.11 + Node.js 20+。

### 5.1 Docker 方式（推荐）

```powershell
Set-Location D:\path\to\embodiedops

$env:DEMO_READ_ONLY = "true"
$env:NEXT_PUBLIC_DEMO_READ_ONLY = "true"
$env:NEXT_PUBLIC_API_BASE_URL = ""
$env:SIGNALFORGE_API_ORIGIN = "http://api:8000"

docker compose up --build -d
powershell -ExecutionPolicy Bypass -File scripts/verify_local.ps1
```

打开：

- 具身智能工作台：http://localhost:3000/embodied
- 原 SignalForge 决策工作台：http://localhost:3000
- 健康检查：http://localhost:8000/healthz

端口冲突时（不改动占用 3000 的其他项目）：

```powershell
$env:WEB_HOST_PORT = "3001"
docker compose up --build -d web
```

### 5.2 本地开发方式

```powershell
# 后端
& .\.venv\Scripts\python.exe -m pytest backend\tests -q
& .\.venv\Scripts\python.exe -m ruff check backend

# 前端
Set-Location frontend
npm ci
npm test -- --run
npm run lint
npm run build
```

### 5.3 停止

```powershell
docker compose down
```

## 6. 常见问题

- **启动失败并报 hash 不匹配**：这是**预期行为**。只读启动会校验 `data/embodied/demo_episode_manifest.json` 的 SHA-256，
  fixture 缺失或版本冲突会失败关闭，不会以空指标运行。
- **3000 端口被占**：用 `WEB_HOST_PORT=3001` 只重建 web 服务，不要停掉未知容器。
- **临时外网演示**：先跑 `scripts/stop_demo_tunnel.ps1` 停旧隧道，再用 `-WebBaseUrl` 指定新端口启动。
- **公开前自查**：跑 `backend/tests/test_public_artifact_safety.py`。

## 7. 面试叙事要点

1. **用户与痛点**：一次 pick-and-place 失败，只记 success/fail 无法回答——失败在哪个阶段、支持判断的事件是什么、有没有反例、下一步验证什么。
2. **为什么做诊断工作台而不是控制台**：只读是产品定位，也是责任边界。
3. **AI 的边界怎么划**：Agent 只在允许的 episode 摘要与事件 ID 范围内归纳候选机制；事件、时间窗、数值与反例由规则层验证。
4. **fail-closed 的价值**：证据不足时输出 `needs_evidence` 或拒绝，而不是编造一个合理结论——这是这个产品能不能被信任的前提。
5. **评测怎么拆**：诊断规则、模型质量、人工接受率、业务结果四层分开报告；合成样例不当作 ROI 结论。
6. **工程闭环**：266 个 pytest + 42 个 Vitest 用例；demo 数据带 SHA-256 不可变 manifest；只读演示模式后端拒绝写接口。
