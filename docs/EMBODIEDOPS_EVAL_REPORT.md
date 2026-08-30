# EmbodiedOps 离线诊断评测报告

这份报告由 `eval/run_embodied_eval.py` 从 `eval/embodied_gold_cases.jsonl` 生成。它是一个**可复现的合成 fixture 回归**，不是实时模型评测，也不是 Unitree 或其他真实机器人生产数据的质量声明。

## 评测边界

- 数据集版本：`embodied-demo-v1`
- 案例数：5
- 数据类型：`synthetic_tabletop_fixture`
- `manual_review_status`：所有案例均为 `not_performed`
- Provider：`offline_fixture`
- Live Qwen 调用：0 次
- 真机控制或部署：0 次

评测把模型原始尝试与验证器之后的系统结果分开保存。被验证器拒绝的未知事件引用不会计入“验证后证据”，也不会进入持久化诊断：`persisted_unknown_event_ids = 0`。

## Raw model 与 validated system

| 层级 | 结果 |
| --- | --- |
| `raw_model` | `attempt_count=0`；本 fixture 没有调用实时 provider，因此不声称模型质量 |
| `validated_system` | 5 个案例经过证据 ID、状态和阶段规则的离线评分 |
| 未知事件引用 | 1 个被拒绝的候选引用；验证后持久化数量为 0 |

## Validated system 指标

每个指标同时保存 `numerator`、`denominator` 和 `rate`。分母为 0 时 rate 为 `null`，不会伪造为 0%。

| 指标 | Numerator | Denominator | Rate |
| --- | ---: | ---: | ---: |
| phase accuracy | 5 | 5 | 1.000 |
| event evidence precision | 3 | 4 | 0.750 |
| event evidence coverage | 3 | 4 | 0.750 |
| refusal correctness | 4 | 5 | 0.800 |
| decision status accuracy | 5 | 5 | 1.000 |
| validated event evidence | 3 | 3 | 1.000 |

这里的 0.750 不是模型能力结论：fixture 中刻意包含一个未知事件引用，用来验证 fail-closed 行为；它被拒绝后不能成为系统证据。

## 展示快照

`data/embodied/demo_diagnosis_snapshot.json` 是首页可展示的离线验证快照，明确标记：

- `snapshot_type = offline_validation_snapshot`
- `provider = deterministic_offline_fixture`
- `live_model = false`
- `manual_review_status = not_performed`
- 来源 episode、dataset version 和 event ID
- 当前诊断因为缺少反例证据而是 `needs_evidence`

因此前端可以展示完整的诊断、证据链和拒绝原因，但不能把快照包装成实时 Qwen 输出。

## 可复现命令

```powershell
.venv\Scripts\python.exe eval\run_embodied_eval.py `
  --fixture eval\embodied_gold_cases.jsonl `
  --output eval\embodied_results.json
```

再次运行会得到相同 JSON 内容。该命令不启动 Ollama、不访问网络、不创建诊断任务。

## 限制与下一步

1. 案例是人工构造的桌面抓取仿真 fixture，尚未做真实机器人或独立双人标注复核。
2. 未评估 Qwen 或其他模型的原始输出质量；CPU-only 本地模型的超时结果应单独报告，不能被本快照覆盖。
3. 上线前需要加入真实脱敏 episode、按任务分层的 hold-out 集、人工根因标注和安全复核。
4. 真实指标应补充 P50/P95 延迟、拒绝率、未知引用率、人工采纳率和实验后的成功率变化。
