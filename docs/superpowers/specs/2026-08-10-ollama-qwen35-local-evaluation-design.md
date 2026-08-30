# SignalForge 本地模型接入与真实验收设计

日期：2026-08-10

## 背景与当前状态

SignalForge 已实现仅允许本机地址的 Ollama provider、模型健康检查、异步决策 memo 任务、证据引用与反例校验，以及生成 Trace。当前机器已安装 Ollama `0.32.6`，但尚未安装任何模型；现有离线评估使用合成固定候选，只能证明代码门禁未退化，不能证明真实模型质量。

本机约有 32GB 内存和 Intel Arc 130T GPU（Windows 报告的 16GB 为共享图形内存）。本轮选择官方 Ollama `qwen3.5:9b` Q4_K_M：模型文件约 6.6GB，兼顾中文能力、质量、延迟和现场可运行性。

## 目标与非目标

目标：

- 完全在本机运行 `qwen3.5:9b`，不使用 API Key、云模型或按量计费服务。
- 让 Docker 内的 API 通过 `host.docker.internal:11434` 访问宿主机 Ollama。
- 真实执行 SignalForge 的两阶段生成，而不以聊天示例代替业务验收。
- 冻结模型 tag、digest、量化、Ollama 版本、提示词版本与硬件信息。
- 同时报告原始模型表现、验证后系统表现和运行成本，避免把“被安全门拦住”误报为“模型从未犯错”。

非目标：

- 本轮不接入 OpenAI、Claude、Gemini、阿里云、Ollama Cloud 或其他远程 provider。
- 本轮不下载 `gemma4:12b` 或 `qwen3.6:27b`；它们只保留为后续同条件 A/B 候选。
- 不因模型输出而改变权威数值事实、评分公式或行动门槛。

## 方案比较与选择

1. `qwen3.5:9b`：约 6.6GB，中文业务语义与本机速度平衡最好，作为本轮主模型。
2. `gemma4:12b`：更新且结构化能力强，但中文业务深度仍需同数据实测，留作第二模型复核。
3. `qwen3.6:27b`：质量潜力更高，但模型文件约 17GB，KV cache 还会增加内存，现场延迟风险过高。

选择方案 1。模型“更新”只在安全、质量和延迟相当时作为决胜因素。

## 架构与数据流

1. Windows 宿主机 Ollama 在 `http://localhost:11434` 提供本地 API。
2. 项目 `.env` 只配置：
   - `LOCAL_MODEL_BASE_URL=http://host.docker.internal:11434`
   - `LOCAL_MODEL_NAME=qwen3.5:9b`
   - `LOCAL_MODEL_TIMEOUT_SECONDS=120`
3. Docker API 使用现有本地 provider 发起两阶段严格 JSON 请求：先做证据分组，再起草 memo。
4. 服务端覆盖所有权威 facts，并验证引用范围、反例覆盖、脱敏、数字文本、实验规则和状态契约。
5. 只有通过验证的 memo 才进入不可变 revision/current projection；失败任务保留稳定错误码和 Trace。

本地 `localhost:11434` 不需要认证。配置文件不得出现占位或真实付费 Key。

## 真实验收

### 原始模型层

- 首次 JSON/领域 schema 通过率。
- 一次重试后的通过率与重试原因。
- 未知引用率、反例遗漏率、模型数字文本拦截率。
- 中文子问题是否指出机制而非复述评论。

### 验证后系统层

- 被接受的未知引用、无支持数字和脱敏泄漏必须为 0。
- 记录 `actionable / needs_evidence / refused / failed`，同时报告有效产出率，禁止靠全部拒绝换取表面安全。
- 支持证据和反例证据必须能从 UI 打开并回到当前 dataset version。

### 运行层

- 冷启动耗时、端到端耗时和各 Trace stage 延迟。
- 重试次数、估算 token、模型 digest、量化和实际 processor。
- 运行时内存/共享 GPU 情况；上下文先保持业务所需的小窗口，不主动使用模型宣称的 256K 上限。

## 错误处理与停止条件

- 下载失败：保留现有应用可用状态，不写入“模型已就绪”结论。
- Ollama 不可达或模型缺失：健康接口返回稳定错误码，UI 不生成伪 memo。
- 首次真实生成超时：先核对 processor、上下文和日志；允许调整本地超时，不切换到付费服务。
- 如果 9B 在当前硬件上不可接受地慢：记录证据后降到 `qwen3.5:4b` 复测，而不是静默换模型。
- 任何云端、API Key、订阅或付费调用都必须再次取得用户批准。

## 交付物与完成标准

- Ollama 中存在固定 digest 的 `qwen3.5:9b`。
- `/healthz/model` 返回 configured/ready 均为 true，模型名准确。
- 至少一次真实端到端 memo 任务到达终态，结果和所有 Trace 可审计。
- 保存一份明确区分“模型原始表现”和“系统验证后表现”的本地模型报告。
- 后端、前端、lint/build 和本地验证脚本继续通过。

