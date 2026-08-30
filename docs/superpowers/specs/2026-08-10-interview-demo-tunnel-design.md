# SignalForge 面试展示隧道设计

## 目标

为面试官提供一个可临时打开的 HTTPS 展示链接，浏览 SignalForge 的首页、决策、洞察、风险与证据详情。展示模式只读，不让外部访客触发本地模型生成、写入 DuckDB 或改变任务状态。

## 现状与约束

- 前端运行在 `localhost:3000`，后端 API 运行在 `localhost:8000`，Ollama 运行在 `localhost:11434`。
- 当前前端生产配置把 API 指向浏览器侧的 `localhost:8000`；外部访客会错误地请求自己的电脑，因此必须改为同源 `/api` 路径。
- 隧道只代理前端端口；API 与 Ollama 不直接暴露公网。
- 继续使用本地演示数据，不引入付费 API、云端模型或 API key。
- Cloudflare Quick Tunnel 只作为短期测试/面试展示通道；链接随机、进程停止后失效，不承诺生产可用性。

## 推荐架构

```text
面试官浏览器
    │ HTTPS（随机 trycloudflare.com 地址）
    ▼
cloudflared → localhost:3000（Next.js）
                    │ 同源 /api/* rewrite/proxy
                    ▼
                localhost:8000（FastAPI）
                    │ 本机网络
                    ▼
                localhost:11434（Ollama，仅本机）
```

前端使用空 API 基址时，`/api/*` 由 Next.js 代理到后端；本地开发仍允许显式 `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000`。隧道脚本只启动 `cloudflared tunnel --url http://localhost:3000`，不接受外部传入目标地址。

## 只读演示边界

- 演示页面保留 GET 数据读取、页面导航、证据抽屉和失败状态展示。
- 隐藏或禁用生成 memo、重试生成、实验提交与任何写操作 CTA。
- 后端增加演示模式开关；在演示模式下拒绝生成 memo 的 POST，并返回稳定的 `DEMO_READ_ONLY` 错误码。
- 不把模型错误、提示词、证据正文或本机路径泄露到公网错误响应。
- 隧道关闭后，公开 URL 立即失效；启动脚本输出 URL，停止脚本按 PID 精确终止对应 `cloudflared` 进程。

## 认证与数据

本轮不引入账号系统。因为数据是固定演示数据，URL 的主要保护是临时性和只读边界；若未来接入真实数据，应改用带身份控制的命名隧道/Access，而不是继续使用 Quick Tunnel。

## 失败处理

- `cloudflared` 未安装、端口 3000 不可达或隧道启动失败时，脚本返回非零退出码并不打印假链接。
- 外部访问 API 代理失败时，前端显示现有离线/服务不可用状态，不展示伪造 memo。
- API 仍执行本地健康检查；Ollama 不就绪不影响只读页面浏览，但生成入口在演示模式下不可用。

## 验收标准

1. 本机 `http://localhost:3000` 继续返回 200，现有前端 test/lint/build 全部通过。
2. 通过同源 `/api/v1/overview`、`/api/v1/insights`、`/api/v1/evidence` 可从前端访问真实后端数据。
3. 访问演示 URL 可以浏览页面并打开证据抽屉；浏览器不会请求访客自己的 `localhost:8000`。
4. 演示模式下生成 POST 返回 `DEMO_READ_ONLY`，数据库没有新增 job/memo。
5. `cloudflared` 停止后 URL 不再可访问；脚本不会停止 Ollama、Docker 或其他同名进程。
6. 不新增付费依赖、API key、模型下载或外部写入。

## 非目标

- 不把 Quick Tunnel 当作生产部署方案。
- 不公开 8000、11434 或 DuckDB 文件。
- 不在本轮解决 qwen3.5:9b 的 CPU 推理质量问题；AI 生成按钮继续遵循只读演示边界。
