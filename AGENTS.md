# AGENTS.md

> 本文件是写给 AI 编码助手（Codex CLI / Gemini CLI / Cursor / Copilot / Aider / Zed / Windsurf 等）的项目约定。
> 人类读者请先看 [README.md](README.md) 和 [HANDOFF.md](HANDOFF.md)。
> 修改本项目前，请先读完本文件——尤其是「架构红线」一节。

## 项目一句话

**EmbodiedOps（内部包名 `signalforge`）**：面向具身智能 / 机器人任务失败的**证据绑定诊断工作台**。
输入是机器人执行 episode 的轨迹与日志，输出是「哪个阶段失败了、可能是什么机制、证据是什么」。
诊断结论必须可追溯到具体事件与数值，不允许模型自由发挥。

## 技术栈

| 层 | 技术 |
| --- | --- |
| 后端 | Python ≥ 3.11 · FastAPI · Pydantic v2 · DuckDB · pandas · h5py |
| 前端 | Next.js 15 · React 19 · TypeScript · TanStack Query · Recharts |
| 测试 | pytest（后端）· Vitest + Testing Library（前端） |
| 部署 | docker-compose（api + web） |

## 环境与命令

```bash
# 后端（在仓库根目录执行；pytest 配置在 backend/pyproject.toml）
cd backend
python -m venv .venv && .venv/Scripts/activate      # Windows
pip install -e ".[dev]"                             # 含 pytest / ruff
pytest -q                                           # 266 个用例
ruff check .                                        # 代码风格（line-length 100）

# 前端
cd frontend
npm install
npm run lint                                        # eslint .
npm test                                            # vitest run，42 个用例
npm run dev                                         # 本地起 Next.js

# 一键本地校验（Windows PowerShell）
./scripts/verify_local.ps1
```

**测试入口就是这两个**：`cd backend && pytest -q`、`cd frontend && npm test`。
提交前请至少跑后端测试；改动前端请跑 `npm test` 与 `npm run lint`。

## 架构红线（改代码前必须确认没有违反）

1. **只读、不控制。** 本项目**不发送任何机器人控制命令**。诊断工具的价值在于可信，
   一旦能控制机器人，责任边界就模糊了。**不要**添加任何下发指令、回放动作、修改机器人状态的接口。
2. **fail-closed，不是 best-effort。** 证据不足时输出 `needs_evidence` 或直接拒绝，
   **不要**给一个「看着合理」的结论。宁可少说，不可猜。
3. **模型与规则分工固定。**
   - 模型（LLM）**只允许**在给定摘要与事件 ID 范围内归纳候选机制。
   - 事件、时间窗、数值主张、反例，**全部由规则层验证**。
   - 不要让模型产出数值、时间戳或未经规则校验的结论。
4. **阶段切分是确定性规则，不是模型推断。** 五阶段切分（`phase_analysis`）必须保证
   同一份 episode 每次得到完全相同的切分结果。**不要**把切分逻辑换成模型调用。
5. **demo 数据不可变。** 演示数据 manifest 带 SHA-256 校验，版本冲突即**失败关闭**。
   不要「顺手修一下」demo 数据来让演示更好看——那会让结论失真。
6. **评测与业务结果分开报告。** 诊断规则质量、模型质量、人工接受率、业务结果**四层分开**，
   不要合并成一个「准确率」。合成 before/after 样例**不构成生产 ROI 结论**。
7. **降级必须透明。** 模型不可用、数据缺失、契约未填充，都要在输出中显式标注，
   **不允许静默回落**到看起来正常的默认值。

## 数据边界（写文档、写简历、对外介绍时的措辞纪律）

当前仓库里的**全部是合成数据**：30 条 synthetic tabletop pick-and-place episode，
固定标注 `dataset_version=embodied-demo-v1` / `real_robot_data=false` / `live_model=false` /
`manual_review_status=not_performed`。

- **没有**公开真实机器人轨迹、生产日志或客户数据。
- `docs/EMBODIEDOPS_REAL_DATA_CARD.md` 描述的是**未来接入真实数据所需的契约**，
  该契约**当前未被真实数据填充**——不要写成「已接入真实数据」。
- 离线评测结果**不能**当作实时模型质量声明。
- **无 sim-to-real 结论**：不宣称成功率提升，不宣称生产 ROI。
- **人工接受率未测**，不要编造数字。
- Cloudflare Quick Tunnel 仅用于**临时演示**，无 SLA、地址会变，
  **绝不能写进 README / 简历 / GitHub**。

对外发布任何内容前，请先跑：

```bash
cd backend && pytest tests/test_public_artifact_safety.py -q
```

该测试会检查真实密钥、数据库文件、临时隧道链接、原始模型输入输出的泄漏。

> 诚实披露「没验到什么」，在这个项目里是**加分项**，不是减分项。不要为了好看而删边界声明。

## 目录导航

```
backend/signalforge/     # 后端主包
  api/                   # FastAPI 路由
  core/                  # 通用能力（配置、检索、生成）
  db/                    # DuckDB 接入与仓储基类
  embodied/              # 具身诊断核心：episode 模型、五阶段切分、指标、诊断
  etl/                   # 数据导入与转换
  services/              # 业务服务层（决策备忘录、任务调度等）
backend/tests/           # 266 个 pytest 用例
frontend/src/            # Next.js 应用（app / components / hooks / lib）
frontend/tests/          # 42 个 Vitest 用例
eval/                    # 离线评测脚本与工件（gold cases、results）
docs/                    # PRD、评测报告、数据卡、决策规则、案例研究
scripts/                 # 环境校验、demo 生成、隧道启停（PowerShell）
data/                    # 数据目录（*.duckdb 与 raw/ 已 gitignore）
```

## 关键文件速查

| 你要改什么 | 去哪 |
| --- | --- |
| 五阶段切分逻辑 | `backend/signalforge/embodied/`（phase_analysis） |
| 失败分布 / 业务指标 | 同上（metrics、business_metrics；业务指标明确标注为合成） |
| 诊断 Agent 与数据契约 | diagnosis、diagnosis_models |
| 离线评测 | `eval/run_embodied_eval.py`、`eval/run_local_model_eval.py` |
| 产品口径 / 边界声明 | `docs/EMBODIEDOPS_PRD.md`、`docs/EVAL_REPORT.md` |
| 数据来源与授权要求 | `docs/DATA_CARD.md`、`docs/EMBODIEDOPS_REAL_DATA_CARD.md` |
| 交付给下一个人的上下文 | [HANDOFF.md](HANDOFF.md) |

## 代码风格

- Python：ruff（`select = ["E","F","I","UP"]`，行长 100）。类型注解尽量补全。
- 前端：ESLint（`eslint-config-next`），TypeScript strict。
- 提交信息请写清「做了什么 + 为什么」，不要只写 "update"。
- **不要提交**：`.env`、`data/*.duckdb`、`data/embodied/raw/`、任何真实密钥或客户数据。

## 不要做的事

- ❌ 不要添加机器人控制能力（违反红线 1）
- ❌ 不要用模型生成数值 / 时间戳 / 未经规则校验的结论（违反红线 3）
- ❌ 不要删除或美化「已知风险与待办」中的边界声明
- ❌ 不要为了让测试通过而放宽 fail-closed 行为
- ❌ 不要把临时 demo 隧道地址写进任何对外文件
- ❌ 不要在对外材料中把合成数据表述为真实数据
