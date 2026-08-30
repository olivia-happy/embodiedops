# 数据卡：SignalForge MVP 演示快照

## 结论先行

仓库默认提供的是一个**可自由分发的、原创合成中文评论样本**与十条人工复核的权威公开事件索引。它用于验证导入、脱敏、版本化、证据追溯和风险卡流程；不用于声称真实用户比例、市场预测或商业结论。

项目不打包、下载或再分发未经单独核实许可的 ASAP 原始评论。ASAP 仅作为未来可选数据连接器的字段结构与研究引用来源。

## 数据资产与许可

| 资产 | 位置 | 内容 | 分发/使用边界 |
|---|---|---|---|
| 原创合成评论 | `data/demo/asap_reviews_sample.csv` | 12 条项目作者编写的虚构中文评论 | 仅此文件按 CC0-1.0 发布；不含 ASAP 原文、真实订单、账号或联系信息 |
| 权威事件索引 | `data/demo/market_events_sample.csv` | 10 条对公开政策、交易所资讯和公告的简短原创释义与链接 | URL 指向原始来源；不复制原文全文，不构成投资或合规建议 |
| 导入清单 | `data/manifests/*.json` | 文件 SHA-256、字段映射、行数、导入时间与来源声明 | 每次导入生成不可变版本记录 |

### ASAP 的位置与引用

- 上游项目：<https://github.com/Meituan-Dianping/asap>
- 上游论文：Tian et al., *ASAP: A Chinese Review Dataset towards Aspect Category Sentiment Analysis and Rating Prediction*, 2020。
- 上游仓库标注许可证：Apache-2.0。使用者在自行下载或导入任何上游文件前，必须自行复核具体文件的许可证、署名要求、数据使用条款与个人信息风险。
- 本仓库的文件名保留 `asap_reviews_sample.csv` 是为了演示可映射的评论输入结构，**不是**“ASAP 子集”的声明。

## 字段、清洗与质量控制

### 评论输入与标准化输出

| 标准字段 | 默认 CSV 列 | 处理规则 |
|---|---|---|
| `id` | `review_id` | 加上来源 slug；移除路径和控制字符，保证稳定且可安全作为证据 ID |
| `content` | `review` | 去除首尾/重复空白；替换中国大陆手机号为 `[PHONE]`、邮箱为 `[EMAIL]` |
| `rating` | `rating` | 必须是 1–5 的整数；否则整批导入被拒绝 |
| `aspect` | `aspect` | 缺失时为 `unknown`；不由导入器臆测 |
| `sentiment` | `sentiment` | 仅接受 `positive`、`neutral`、`negative`；其它值降为 `unknown` |
| `redacted` | 推导字段 | 只要文本发生手机号或邮箱替换即为 `true` |

`SourceMetadata.review_column_mapping` 允许将外部列显式映射到上述输入字段。缺少 `review_id`、`review` 或 `rating` 的列会在写库前失败，并返回行号、字段和原因。评论和事件均先完成全量验证，随后才在一个 DuckDB 事务中写入；失败导入不会替换已有版本。

### 市场事件字段

每条事件保存 `source_url`、`published_on`、`excerpt`、`event_type`、`industry`、`evidence_quality`。`excerpt` 是本项目为演示编写的摘要，不是原文摘录；`evidence_quality` 是来源透明度的人工演示标签（0–100），不是事实准确度或风险分数。

## 事件来源复核范围

事件 URL 指向国家发展改革委、工信部、上交所、中央网信办、国务院或地方政府公开页面。样本在 2026-08-09 进行 URL 与发布日期人工复核；页面后续可能迁移、更新或失效。应用默认显示为“版本化快照”，不承诺实时性。任何生产刷新连接器都必须先验证站点条款、robots.txt、访问频率与再利用限制。

## 局限与不当用途

- 12 条合成评论不能代表真实用户、满意度、需求规模或模型效果；它们只支持端到端演示与测试。
- 十条事件不能代表完整产业动态，也不能用于股票、采购、供应链或监管决策。
- 脱敏规则只覆盖高置信度的手机号和邮箱模式；它不是完整的个人信息识别系统。导入真实数据前应增加人工抽检、访问控制和适用的数据保护评估。
- 文件哈希能证明本地文件版本一致，不能证明上游网页在任何时间点的真实性或持续可访问性。

## 可复现导入

从 `backend/` 运行：

```powershell
..\.venv\Scripts\python.exe -m signalforge.etl.load `
  --reviews ..\data\demo\asap_reviews_sample.csv `
  --events ..\data\demo\market_events_sample.csv
```

成功后会在 `data/manifests/` 写入一个以 `slug + 评论文件 SHA-256 前缀` 命名的清单，并在本地 DuckDB 创建相同版本 ID。重复导入相同评论文件是幂等的。
