# CLAUDE.md

本项目面向 Claude Code 的约定，**主体内容见 [AGENTS.md](AGENTS.md)**（跨工具通用，同一份约定）。

@AGENTS.md

---

## Claude Code 专属补充

### 这是一个 Python + Next.js 混合仓库

- 后端在 `backend/`（包名 `signalforge`），前端在 `frontend/`。
- **运行后端命令前先 `cd backend`**——`pytest` 配置写在 `backend/pyproject.toml`，在仓库根跑会找不到测试。
- 前端命令在 `frontend/` 下执行。

### 常用定位（Claude 读完 AGENTS.md 后仍可能需要的细节）

| 目标 | 文件 |
| --- | --- |
| 后端入口 | `backend/signalforge/api/` |
| 具身诊断核心 | `backend/signalforge/embodied/` |
| 数据导入链路 | `backend/signalforge/etl/` |
| API 数据契约 | `backend/signalforge/embodied/diagnosis_models.py` |
| 前端页面 | `frontend/src/app/` |
| 前端组件 | `frontend/src/components/` |
| 前端数据请求 | `frontend/src/hooks/`、`frontend/src/lib/` |
| 评测脚本 | `eval/run_embodied_eval.py` |
| 对外安全测试 | `backend/tests/test_public_artifact_safety.py` |

### 修改前必做

1. 读 [AGENTS.md](AGENTS.md) 的「架构红线」7 条——尤其**只读不控制**与 **fail-closed**。
2. 触碰到 demo 数据、评测口径、对外文案时，先看 `docs/` 下的边界声明原文。
3. 改完跑 `cd backend && pytest -q`；改前端跑 `cd frontend && npm test && npm run lint`。

### 高风险文件（改之前请说明意图）

- `backend/signalforge/embodied/phase_analysis.py` —— 五阶段切分，必须是确定性的。
- `backend/signalforge/embodied/diagnosis*.py` —— 模型调用边界就卡在这里。
- `backend/tests/test_public_artifact_safety.py` —— 不要为了让它过而放宽检查。
- `frontend/src/lib/` 下的 API 客户端 —— 与后端契约手工同步，改一侧必须改另一侧。

### 不要做

- ❌ 不要新增任何机器人控制接口。
- ❌ 不要在 demo 数据上「调数字」让演示更好看。
- ❌ 不要删除 `docs/` 与 `HANDOFF.md` 中的边界声明。
- ❌ 不要把 Cloudflare Quick Tunnel 地址写进 README / 简历 / 任何对外文件。
